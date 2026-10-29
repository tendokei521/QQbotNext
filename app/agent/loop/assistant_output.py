"""模型输出 → 「写进历史的那一句」与「分几条发出去」。

## 本模块只负责

1. `_clean_output_for_history`：落库前把内部标记 / 流式残留清干净；
2. 发言通道：`_build_speech`（文本=思考，`send_message`=真正发出）、`_speech_intent`、
   `_speech_fallback_enabled`、`_should_force_speak`、`_flush_speech`、`_force_speak_round`。

## 为什么单独成文件

「模型说了什么」和「怎么把这句话变成对外发言」是两件事。后者有自己一整套开关
（`speech_via_tool` / 强制发言 / 兜底），留在主循环里时，改一句发言策略要跨上百行
找上下文。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import account_label

from app.agent import logger
from app.agent.providers.invoker import resolve_invoker
from app.domain.splitter import strip_stream_artifacts
from app.domain.tags import maybe_strip_parentheses, strip_all_tags


def _clean_output_for_history(config, text: str) -> str:
    """写入会话历史前的守卫：按开关清洗助手输出中的（…）/(…)，阻止括号风格自我强化。

    只影响「历史」，不影响本次对用户展示的原文。
    """
    if not text:
        return text
    try:
        enabled = bool(config.get("clean_output_parentheses", True)) if config is not None else True
    except Exception:
        enabled = True
    if not enabled:
        return text
    return maybe_strip_parentheses(text, True)


_WARNED_SPEECH_INTENT: set = set()


def _build_speech(config):
    """按配置构造本轮发言记账本；不该启用时返回 None（= 旧的文本即发言）。"""
    from app.agent.delivery.speech import (
        DEFAULT_MAX_BUBBLE_CHARS,
        DEFAULT_MAX_BUBBLES,
        SpeechOutbox,
        speech_via_tool,
    )

    if not speech_via_tool(config):
        return None
    try:
        max_bubbles = int(config.get("speech_max_bubbles", DEFAULT_MAX_BUBBLES) or DEFAULT_MAX_BUBBLES)
        max_chars = int(
            config.get("speech_max_bubble_chars", DEFAULT_MAX_BUBBLE_CHARS) or DEFAULT_MAX_BUBBLE_CHARS
        )
    except Exception:  # noqa: BLE001 —— 配置异常时用默认值，不要因此关掉发言通道
        max_bubbles, max_chars = DEFAULT_MAX_BUBBLES, DEFAULT_MAX_BUBBLE_CHARS
    return SpeechOutbox(max_bubbles=max_bubbles, max_chars=max_chars)


def speech_needs_text_fallback(speech: Any, use_tools: bool) -> bool:
    """发言通道开着、但本轮工具不可用时需要降级回「文本即发言」。

    发言靠 ``send_message`` 工具；模型/适配器不支持 tool calling 时硬开只会整轮静默。
    """
    return speech is not None and not bool(use_tools)


def _speech_intent(config) -> bool:
    """配置里是否开了发言通道（用于"配置了但没生效"的告警）。"""
    try:
        return bool(config.get("speech_via_tool", False))
    except Exception:  # noqa: BLE001
        return False


def _speech_fallback_enabled(config) -> bool:
    """静默时是否仍要补一句兜底话术（本档位默认关闭：静默比"抱歉我无法回答"更拟人）。"""
    try:
        return bool(config.get("speech_fallback_enable", False))
    except Exception:  # noqa: BLE001
        return False


def _should_force_speak(config, ctx, is_private: bool) -> bool:
    """是否属于"被直接点名，必须回应"的场景（@ / 私聊 / 拍一拍）。

    这类场景一句话不说会被当成掉线，因此允许额外补一轮把话说出来；
    群里没被点名的闲聊则不该强制——那正是静默要保护的部分。
    """
    try:
        if not bool(config.get("speech_force_reply_on_call", True)):
            return False
    except Exception:  # noqa: BLE001
        return False
    if is_private:
        return True
    return bool(ctx is not None and ctx.state.get("is_at"))


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


async def _force_speak_round(
    runtime, speech, messages, provider_chain, meta, tools, tool_executor
) -> None:
    """被点名却没开口时的补救：非流式再要一次（只关心会不会调 send_message）。

    用非流式而不是再来一轮流式：这里不需要把文本流给用户，只需要一次工具调用。
    轮数上限刻意压到 2（一次展开 + 一次发言），失败也只是回到静默，不会拖住会话。
    """
    from app.agent.delivery.speech import FORCE_SPEAK_REMINDER

    messages.append({"role": "user", "content": FORCE_SPEAK_REMINDER})
    try:
        await resolve_invoker(runtime).chat(
            runtime=runtime,
            chain=provider_chain,
            messages=messages,
            params={
                "model": meta["model"],
                "temperature": meta["temperature"],
                "max_tokens": meta["max_tokens"],
                "tools": tools,
                "tool_executor": tool_executor,
                "max_tool_rounds": 2,
                "max_empty_retries": 0,
            },
        )
    except Exception as e:  # noqa: BLE001 —— 补救轮失败就回到静默，不能让整轮炸掉
        logger.add_info(account_label(runtime.bot_id)).warning(f"[LLM] 强制发言补救轮失败（按静默处理）: {e}")
    if speech.spoken == 0 and speech.queued == 0:
        logger.add_info(account_label(runtime.bot_id)).warning(
            "[LLM] 被直接点名但补一轮后仍未开口（模型可能没调用 send_message）"
        )
