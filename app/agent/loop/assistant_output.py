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

from app.domain.splitter import strip_stream_artifacts
from app.domain.tags import strip_all_tags


def _clean_output_for_history(config, text: str) -> str:
    """写入会话历史前的守卫（括号清洗开关已删除）。

    仍负责清掉内部标记与流式残留；括号保持原样。
    """
    return text


_WARNED_SPEECH_INTENT: set = set()


def _build_speech(config, *, force: bool = False):
    """按配置构造本轮发言记账本；不该启用时返回 None（= 旧的文本即发言）。

    Args:
        force: 本轮已由回复模式判定为发言通道（`reply_mode=participation`）。
            群级覆盖下 `config` 可能是账号级视图，读不到 `reply_mode`，所以要显式传。
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
    return SpeechOutbox(max_bubbles=max_bubbles, max_chars=max_chars)


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

    is_group = (
        getattr(event, "event_type", "") == "message_group"
        or getattr(event, "group", None) is not None
    )
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

