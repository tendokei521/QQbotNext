"""上下文取料：从群、历史、记忆里取出这一轮要用的「料」。

## 本模块只负责

`prepare_prompt` 装配 messages 时要去各处取料，这里的每个函数就是一路来源：

- 群侧：`_build_group_pre_history`（群里刚才在聊什么）；
- 指代：**已搬进 `llm_context` 模块**（通过 `context_providers` 贡献块）；
- 记忆：`_memory_block` / `_memory_autosave` / `_memory_consolidate`；
- 会话历史：`_format_session_history` / `_history_media_hook` /
  `_commit_history_enrichment` / `_append_round_images`；
- 小工具：`_event_nickname` / `_segments_as_dicts`。

## 为什么单独成文件

这些函数有两种来源（在线群消息、落盘历史）交汇的细节，是本仓库最容易改出
「同一句话出现两次」的地方。它们和「这一轮怎么请求模型」无关，却挤在 chat.py 里；
搬出来后改取料口径不用翻主流程。

**取料只读不写**：唯一的写入是记忆自动保存与历史富化回写，都带幂等标记。
"""

from __future__ import annotations

from typing import Any

from app.agent import logger
from app.agent.context.group import (
    build_group_env_text,
    fetch_group_online_history,
    format_history_for_llm,
)


def _history_media_hook(
    entries: list,
    *,
    include_images: bool,
    bot_id: Any = "",
    max_images: int = 4,
    max_bytes: int | None = None,
    max_mb: float | None = None,
    max_edge: int | None = None,
):
    """会话历史图片的渲染钩子（配合 ``history_model.render_history`` 的 ``entry_media``）。

    - **文本模型**：不动正文（渲染器已给出 ``[图片#<消息id>]`` 占位），
      模型可 ``expand_image`` 按需取回；
    - **视觉模型**：把历史里的**本地指向**（``data.media`` 的 sha）内联成 ``image_url`` 块，
      模型下一轮仍看得见上一轮发的图。**远端直链永远不进请求**——QQ 图片链接会过期，
      过期后不是"少一张图"，而是网关拉不到 → 整条请求 400；文件已丢时自然退化为占位。

    内联预算按**由新到旧**分配：最多 ``max_images`` 张（与单轮图片上限同一把尺子），
    避免长会话把上下文撑爆。
    """
    from app.agent.media.image import DEFAULT_MAX_IMAGE_BYTES, history_image_parts, history_image_segments

    if max_bytes is None:
        max_bytes = DEFAULT_MAX_IMAGE_BYTES

    allowed: set[int] = set()
    if include_images:
        left = max(1, int(max_images or 1))
        for entry in reversed(list(entries or [])):
            count = len(history_image_segments(entry))
            if count and left > 0:
                allowed.add(id(entry))
                left -= count

    def hook(entry: Any, *, text: str = ""):
        if not history_image_segments(entry) or id(entry) not in allowed:
            return None
        # 正文保持渲染器结果（占位/正文），只把图片块接在后面
        return None, history_image_parts(
            entry,
            bot_id=bot_id,
            max_images=max_images,
            max_bytes=max_bytes,
            max_mb=max_mb,
            max_edge=max_edge,
        )

    return hook


async def _build_group_pre_history(
    event,
    group_id: str,
    count: int,
    *,
    normalize_enhanced: bool = False,
    mask_nickname: bool = False,
    resolve_at: bool = True,
    mark_unresolved: bool = False,
    bot_id: Any = "",
    session_id: Any = "",
    bot: Any = None,
) -> str:
    """根据 include_pre_history 配置，拉取并组装群聊环境背景块。

    不拉取在线历史时返回空字符串；本函数不改变“非 @ 不入会话历史”的策略。

    ``resolve_at`` 会把背景块里的 ``@123`` 预展开成 ``@三哥(123)``（复用
    ``app.agent.context.nicknames`` 与其缓存），``mark_unresolved`` 让取不到的部分输出
    ``【未展开:...】`` 标记——背景块是"群里其他人聊了什么"的唯一可见窗口，
    此前这里只有裸 ``@123``，模型既不知道是谁、也没有"这里缺东西"的感知。
    ``bot_id``/``session_id`` 用于查"已展开"登记：本会话取回过的 id 渲染成
    ``【已展开:... → 摘要】``，不再重复标记为缺失。

    ``bot`` 供无事件路径（主动消息 / 定时任务）显式传入连接；``event`` 为 None 时用它。
    """
    bot = bot if bot is not None else getattr(event, "bot", None)
    history_text = await fetch_group_online_history(
        bot,
        group_id,
        count=count,
        self_ids={
            str(getattr(event, "self_id", "") or ""),
            str(getattr(event, "bot_id", "") or getattr(bot, "bot_id", "") or ""),
        },
        normalize_enhanced=normalize_enhanced,
        mask_nickname=mask_nickname,
        resolve_at=resolve_at,
        mark_unresolved=mark_unresolved,
        bot_id=bot_id or getattr(event, "bot_id", "") or "",
        session_id=session_id,
    )
    if not history_text:
        return ""
    return build_group_env_text(
        group_id=group_id,
        group_name=getattr(getattr(event, "group", None), "group_name", "") or "",
        history_text=history_text,
    )


def _format_session_history(
    history: list[dict],
    is_private: bool,
    *,
    normalize_enhanced: bool = False,
    mask_nickname: bool = False,
    bot_id: Any = "",
    session_id: Any = "",
    entry_media=None,
) -> list[dict]:
    """兼容包装：委托给 group_context 的共享渲染函数。

    传入 ``bot_id`` / ``session_id`` 时接入补全登记（取回过的内容留在历史里）。
    ``entry_media`` 见 :func:`_history_media_hook`。
    """
    return format_history_for_llm(
        history,
        is_private=is_private,
        normalize_enhanced=normalize_enhanced,
        mask_nickname=mask_nickname,
        bot_id=bot_id,
        session_id=session_id,
        entry_media=entry_media,
    )


def _commit_history_enrichment(runtime, session_id: str, ctx=None) -> int:
    """请求收尾：把本轮工具取回的内容写进「补全登记」（失败不影响回复）。"""
    try:
        from app.agent.session.enrich import commit

        trigger = ""
        if ctx is not None:
            trigger = str(getattr(ctx.event, "message_id", "") or "")
        return commit(getattr(runtime, "bot_id", ""), session_id, trigger_message_id=trigger)
    except Exception as e:  # noqa: BLE001
        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(
            f"[HistoryEnrich] 回写失败（已忽略）: {e}"
        )
        return 0


async def _memory_block(runtime, session_id: str, user_id: Any, user_text: str, bot) -> str:
    """召回长期记忆块；未启用或异常时返回空串。"""
    memory = getattr(runtime, "memory", None)
    if memory is None or not memory.enabled():
        return ""
    try:
        return await memory.recall_block_async(session_id, user_id, user_text, bot=bot)
    except Exception as e:
        # 记忆召回属增强能力：失败不阻断回复，但需留痕，否则表现为"记忆突然失效"
        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(f"[Memory] 召回失败（已忽略）: {e}")
        return ""


def _memory_consolidate(runtime, session_id: str, is_private: bool, session_mgr) -> None:
    """回复后触发限频隐式蒸馏；未启用或异常时静默跳过。"""
    memory = getattr(runtime, "memory", None)
    if memory is None or not memory.enabled():
        return
    try:
        history = session_mgr.get_history(session_id, limit=20)
        memory.maybe_consolidate(session_id, not is_private, history, source="chat")
    except Exception as e:
        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(f"[Memory] 隐式蒸馏触发失败（已忽略）: {e}")


def _segments_as_dicts(message: Any) -> list[dict]:
    """把事件里的消息段统一成 ``[{"type": ..., "data": {...}}]``（存历史用）。"""
    segments: list[dict] = []
    for seg in message or []:
        if isinstance(seg, dict):
            segments.append({"type": str(seg.get("type", "")), "data": dict(seg.get("data", {}) or {})})
            continue
        stype = getattr(seg, "type", "")
        if stype:
            segments.append({"type": str(stype), "data": dict(getattr(seg, "data", {}) or {})})
    return segments


def _memory_autosave(runtime, session_id: str, user_id: Any, text: str) -> None:
    """确定性“记住…”兜底写入；未启用或异常时静默跳过。"""
    memory = getattr(runtime, "memory", None)
    if memory is None or not memory.enabled():
        return
    try:
        memory.autosave(session_id, user_id, text)
    except Exception as e:
        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(f"[Memory] 兜底写入失败（已忽略）: {e}")


def _append_round_images(messages: list[dict], tool_ctx, modalities) -> bool:
    """把 ``expand_image`` 取回的图片追加为一条 user 消息（仅视觉模型）。

    实现在 ``context_tools.append_round_images``（主动消息/定时任务的工具轮也要用，
    而它们不能 import 本模块）。见该函数的说明与时机问题。
    """
    from app.plugins.llm_context.context_tools import append_round_images

    return append_round_images(messages, tool_ctx, modalities)


def _event_nickname(event) -> str:
    """取发送者群名片/昵称，用于会话历史保留发送者身份。"""
    user = getattr(event, "user", None)
    if user is None:
        return ""
    return getattr(user, "card", "") or getattr(user, "nickname", "") or ""
