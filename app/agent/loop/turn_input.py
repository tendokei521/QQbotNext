"""一轮对话的**入口侧**：把事件归一化、取会话、把这条用户消息写进历史。

## 本模块只负责

`prepare_prompt` 开头那三段脏活：

1. `normalize_event`：从事件里抽出 `is_private / session_id / user_id / group_id /
   user_text`，并做「单把尺子」的长度截断（`max_message_length`）；
2. `get_or_create_session`：取或建会话（含归档恢复与群聊冷却判定）；
3. `record_user_turn`：**收到即落盘**（图片）+ 结构化写历史（正文与元信息分离）。

## 为什么单独成文件

这三段有大量历史踩坑注释（QQ 图片 rkey 会过期所以要落盘、纯图片消息不能把元信息冻成
正文、冷却期非 @ 不触发…），它们讲的是「消息怎么进来」，与「这一轮怎么请求模型」无关。
挤在 1,100 行的 `chat.py` 里时，改一条入口规则要跨过装配与流式两段。

## 关键口径（改动前必读）

- **图片收到即落盘**：远端直链带 rkey 会过期，写进历史只会让下一轮被网关 400。
  落盘与写历史在同一个函数里，才能保证「本条消息的图随本条一起进归档」。
- **正文与元信息分离存放**：时间/群号/发送者由渲染器按当前配置重排，
  `message_id` 是后续「按需补全」的锚点。
- **纯图片消息正文留空**：否则历史里会显示成一串元信息，图片段反倒被无视。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.agent import logger
from app.agent.media.image import (
    cache_enabled,
    collect_image_segments,
    max_image_bytes,
    persist_images,
)
from app.agent.session.manager import SessionManager
from app.agent.session.model import split_user_context


@dataclass
class TurnInput:
    """一轮对话的入口侧取值结果。"""

    is_private: bool
    session_id: str
    user_id: str
    group_id: str | None
    user_text: str
    raw_user_text: str
    segment_text: str
    include_pre_history: object


def normalize_event(config, event, ctx=None) -> TurnInput | None:
    """事件 → 本轮入口取值；非群聊/私聊事件直接返回 None。"""
    message_type = getattr(event, "message_type", "")
    if message_type not in ("group", "private"):
        return None

    if message_type == "private":
        is_private = True
        session_id = f"private_{event.user_id}"
        user_id = str(event.user_id)
        group_id = None
        include_pre_history = config.get("include_private_pre_history", "default")
    else:
        is_private = False
        session_id = f"group_{event.group.group_id}"
        user_id = str(event.user_id)
        group_id = str(event.group.group_id)
        include_pre_history = config.get("include_pre_history", False)

    if ctx is not None and ctx.session_id:
        session_id = ctx.session_id

    # 用户原始正文（未包裹元信息）：记忆检索与意图判定都用它，避免元信息里的
    # QQ 号被当成消息 id、昵称污染检索。事件可能没有 message（主动/测试替身）。
    from app.agent.assembly.enhance import _raw_user_text

    segment_text = _raw_user_text(event).strip()
    raw_user_text = ""
    if ctx is not None:
        info = ctx.state.get("user_context") or {}
        raw_user_text = (info.get("sent_text") or "").strip()
    if not raw_user_text:
        raw_user_text = segment_text

    # 本轮 user 内容：流水线已注入元信息时用注入后的文本，否则用原始正文
    user_text = ctx.user_text if (ctx is not None and ctx.user_text) else raw_user_text
    if not user_text.strip() and not collect_image_segments(event):
        return None

    # 截断：单把尺子（max_message_length）；原始正文已由流水线截过，这里只兜底
    max_msg_len = int(config.get("max_message_length", 200) or 200)
    return TurnInput(
        is_private=is_private,
        session_id=session_id,
        user_id=user_id,
        group_id=group_id,
        user_text=user_text[:max_msg_len],
        raw_user_text=raw_user_text[:max_msg_len],
        segment_text=segment_text,
        include_pre_history=include_pre_history,
    )


async def get_or_create_session(session_mgr: SessionManager, turn: TurnInput, config, ctx=None):
    """取或建会话；群聊冷却期内且非 @ 时返回 None（本轮不触发）。"""
    session = session_mgr.get_session(turn.session_id)
    if not session:
        session = session_mgr.create_session(
            turn.session_id,
            "private" if turn.is_private else "group",
            config.get("session_timeout", 60),
        )
        if not turn.is_private:
            session.reply_cooldown = config.get("reply_cooldown", 5)
        await asyncio.to_thread(session_mgr.restore_session_from_archive, session, turn.session_id)
        return session

    # 群聊冷却：非 @ 且不在冷却窗口内 → 本轮不触发（与旧 generate_response 一致）
    if not turn.is_private and not session.can_reply() and not (ctx is not None and ctx.state.get("is_at")):
        return None
    session.add_participant(turn.user_id)
    return session


async def record_user_turn(
    runtime,
    event,
    turn: TurnInput,
    config,
    session_mgr: SessionManager,
    *,
    nickname: str,
) -> None:
    """把这条用户消息写进会话历史（图片先落盘，正文与元信息分离）。"""
    from app.agent.loop.context_blocks import _segments_as_dicts

    body_text, meta_lines = split_user_context(turn.user_text)
    event_segments = _segments_as_dicts(getattr(event, "message", None))

    # 图片：**收到即落盘**。历史里只留本地指向（sha），远端直链不写进历史——QQ 图片链接
    # 带 rkey 会过期，留着只会让下一轮请求被网关 400。
    if event_segments and cache_enabled(config):
        try:
            await persist_images(
                event_segments,
                bot_id=getattr(runtime, "bot_id", ""),
                bot=getattr(event, "bot", None),
                max_bytes=max_image_bytes(config),
            )
        except Exception as e:  # noqa: BLE001 —— 落盘失败只降级为占位，不能拖垮本轮对话
            logger.add_info(f"#{runtime.bot_id}").warning(f"图片落盘失败，按占位处理: {e}")

    # 纯图片/表情消息没有正文：**不能**把元信息（"(QQ: …)"/时间行）冻成正文，
    # 否则历史里这条会显示成一串元信息，图片段（base.segments）反倒被无视。
    stored_text = body_text if event_segments else (body_text or turn.user_text)
    session_mgr.add_message(
        turn.session_id,
        "user",
        stored_text,
        turn.user_id,
        nickname=nickname,
        message_id=getattr(event, "message_id", None),
        timestamp=getattr(event, "time", None),
        segments=event_segments,
        group_id=turn.group_id if not turn.is_private else None,
        is_private=turn.is_private,
        meta_lines=meta_lines,
    )
