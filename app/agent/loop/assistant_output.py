"""模型输出 → 「写进历史的那一句」与「分几条发出去」。

## 本模块只负责

1. `_clean_output_for_history`：落库前把内部标记 / 流式残留清干净；
2. 发言通道：`_build_speech`（文本=思考，`send_message`=真正发出）、`_speech_intent`、
   `_flush_speech`（轮次边界或整轮结束时把气泡变成待发消息）。

## 为什么单独成文件

「模型说了什么」和「怎么把这句话变成对外发言」是两件事。后者有自己一整套开关
（`speech_via_tool` / 强制发言 / 兜底），留在主循环里时，改一句发言策略要跨上百行
找上下文。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger

from app.domain.splitter import strip_stream_artifacts
from app.domain.tags import strip_all_tags


def _clean_output_for_history(config, text: str) -> str:
    """写入会话历史前的守卫（括号清洗开关已删除）。

    仍负责清掉内部标记与流式残留；括号保持原样。
    """
    return text


_WARNED_SPEECH_INTENT: set = set()


def _build_speech(config, *, force: bool = False, immediate: bool = False):
    """按配置构造本轮发言记账本；不该启用时返回 None（= 旧的文本即发言）。

    Args:
        force: 本轮已由回复模式判定为发言通道（`reply_mode=participation`）。
            群级覆盖下 `config` 可能是账号级视图，读不到 `reply_mode`，所以要显式传。
        immediate: 入稿即发送（非流式请求没有轮次边界可 flush，只能由工具自己发）。
    """
    from app.agent.delivery.speech import (
        DEFAULT_MAX_BUBBLE_CHARS,
        DEFAULT_MAX_BUBBLES,
        SpeechOutbox,
        speech_via_tool,
    )

    if not (force or speech_via_tool(config)):
        return None
    try:
        max_bubbles = int(config.get("speech_max_bubbles", DEFAULT_MAX_BUBBLES) or DEFAULT_MAX_BUBBLES)
        max_chars = int(
            config.get("speech_max_bubble_chars", DEFAULT_MAX_BUBBLE_CHARS) or DEFAULT_MAX_BUBBLE_CHARS
        )
    except Exception:  # noqa: BLE001 —— 配置异常时用默认值，不要因此关掉发言通道
        max_bubbles, max_chars = DEFAULT_MAX_BUBBLES, DEFAULT_MAX_BUBBLE_CHARS
    return SpeechOutbox(max_bubbles=max_bubbles, max_chars=max_chars, immediate=immediate)


def _speech_intent(config) -> bool:
    """本轮是否**应该**走发言通道（由回复模式决定，见 delivery/speech.py）。"""
    from app.agent.delivery.speech import speech_via_tool

    try:
        return speech_via_tool(config)
    except Exception:  # noqa: BLE001
        return False


async def _flush_speech(speech, session_mgr, session_id: str, config, event):
    """把 outbox 里的气泡变成待发送消息，并在发送前写好这一条的历史。

    **顺序是有意的**：先写 assistant 历史、再 yield 给 pipeline 发送。pipeline 发送成功后
    会调 ``mark_last_assistant_message_id``，它认"最近一条 assistant"——此时那条必须
    正好是刚发出去的这个气泡，否则 message_id 会落到别的历史上（旧流式路径就是这个顺序
    反了：先发后写，句柄永远补在上一条上）。
    """
    from app.agent.delivery.directives import OutboundDirectives, assemble_message
    from app.domain.events import is_group_event

    is_group = is_group_event(event)
    for bubble in speech.take():
        text = strip_stream_artifacts(strip_all_tags(bubble.text)).strip()
        if not text:
            continue
        directives = OutboundDirectives(reply=bool(bubble.reply_to), ats=list(bubble.at))
        message = assemble_message(
            text,
            directives,
            event,
            is_group=is_group,
            reply_message_id=bubble.reply_to or None,
        )
        session_mgr.add_message(session_id, "assistant", _clean_output_for_history(config, text))
        yield message


#: 公开别名：输出策略（包括非流式的发言通道）flush 气泡时用它。
flush_speech = _flush_speech


async def deliver_now(ctx: Any, speech: Any) -> tuple[int, str]:
    """把 outbox 里的气泡**立刻**发出去（工具调用即发送）。

    用于非流式请求：那条路径没有"轮次边界"可以 flush，气泡只能由工具自己发——
    否则模型调完 ``send_message`` 还要等整轮结束，用户会看到明显的延迟。

    与 `_flush_speech` 的区别：后者产出 ``Message`` 交给流水线发送（会走
    ``pre_send``/``post_send`` 钩子）；这里必须自己发，所以只做"发送 + 写历史 +
    回填 message_id"三件事。

    Returns:
        ``(成功条数, 错误文本)``：错误文本非空时由工具回执告诉模型（它可以重试）。
    """
    from app.agent.delivery.directives import OutboundDirectives, assemble_message
    from app.domain.events import is_group_event
    from app.domain.splitter import strip_stream_artifacts
    from app.domain.tags import strip_all_tags

    bot = getattr(ctx, "bot", None)
    event = getattr(ctx, "event", None)
    runtime = getattr(ctx, "runtime", None)
    if bot is None or event is None or speech is None:
        return 0, "没有可用的发送上下文（主动消息/定时任务请改用普通回复）"

    session_id = str(getattr(ctx, "session_id", "") or "")
    session_mgr = getattr(runtime, "session_mgr", None) if runtime is not None else None
    if session_mgr is None:
        from app.agent.session.manager import SessionManager

        session_mgr = SessionManager(str(getattr(runtime, "bot_id", "") or ""))
    is_group = is_group_event(event)
    sent = 0
    for bubble in speech.take():
        text = strip_stream_artifacts(strip_all_tags(bubble.text)).strip()
        if not text:
            continue
        directives = OutboundDirectives(reply=bool(bubble.reply_to), ats=list(bubble.at))
        message = assemble_message(
            text,
            directives,
            event,
            is_group=is_group,
            reply_message_id=bubble.reply_to or None,
        )
        try:
            if is_group:
                group = getattr(event, "group", None)
                if group is None:
                    return sent, "发送失败: 群消息事件缺少群对象"
                response = await bot.send_group_msg(group.group_id, message)
            else:
                response = await bot.send_private_msg(event.user_id, message)
        except Exception as e:  # noqa: BLE001 —— 交给模型决定是否重试
            return sent, f"发送失败: {e}"
        # OneBot 的失败是「响应里 status != ok」，不是抛异常（连接超时才抛）。
        # 不检查就会把失败当成功：回执模型"已发出"、还写一条用户永远收不到的
        # assistant 历史（线上事故：私聊回复发进群 0，历史里却记着说过了）。
        if not _send_confirmed(response):
            retcode = response.get("retcode") if isinstance(response, dict) else None
            return sent, f"发送失败: OneBot 未确认送达（retcode={retcode}）"
        sent += 1
        session_mgr.add_message(session_id, "assistant", _clean_output_for_history(None, text))
        data = response.get("data") if isinstance(response, dict) else None
        mid = (data or {}).get("message_id") if isinstance(data, dict) else None
        if mid:
            try:
                session_mgr.mark_last_assistant_message_id(session_id, mid)
            except Exception as e:  # noqa: BLE001 —— 句柄回填失败不影响发送
                logger.debug(f"[Speech] 回填 assistant 消息 id 失败（已忽略）: {e}")
    return sent, ""


def _send_confirmed(response: Any) -> bool:
    """OneBot 响应是否确认送达：``status == "ok"``（连接失败是 None）。"""
    return isinstance(response, dict) and str(response.get("status", "")) == "ok"

