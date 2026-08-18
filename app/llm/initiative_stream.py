"""主动消息 / 定时任务流式发送助手。

当开启 ``stream_proactive_enabled`` / ``stream_scheduled_enabled`` 时，
主动消息和定时任务使用与普通消息相同的流式发送配置：
- 流式生成；
- 按句子切分；
- 进入 StreamSendPool；
- 使用相同的发送间隔、前后缀、队列策略。
"""

from __future__ import annotations

from typing import Any

from app.domain.message import Message
from app.llm.providers import iter_stream_with_fallback
from app.llm.send_pool import StreamSendPool
from app.llm.splitter import split_sentences, strip_stream_artifacts


async def stream_send_initiative(
    runtime: Any,
    bot: Any,
    session_id: str,
    is_group: bool,
    target: str | int,
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> str:
    """使用流式 + 消息池发送一段主动/定时内容，返回完整文本。

    发送由消息池完成，调用方不要重复发送。
    """
    config = runtime.config
    if hasattr(runtime, "provider_chain"):
        chain = runtime.provider_chain()
    else:
        chain = [dict(config.raw_config)]
    max_len = int(
        config.get("stream_sentence_max_length")
        or config.get("max_message_length", 200)
        or 200
    )

    async def send_message(msg: Message) -> None:
        if is_group:
            await bot.send_group_msg(int(target), msg)
        else:
            await bot.send_private_msg(int(target), msg)

    pool = StreamSendPool(config, send_message=send_message)

    full_text_parts: list[str] = []
    buffer = ""

    try:
        async for ev in iter_stream_with_fallback(
            chain,
            messages,
            model=model or config.get("model", "deepseek-chat"),
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            if ev.type == "text":
                buffer += ev.text
                sentences, buffer = split_sentences(buffer, max_length=max_len)
                for sentence in sentences:
                    clean = strip_stream_artifacts(sentence)
                    if clean:
                        full_text_parts.append(clean)
                        await pool.put(Message.from_text(clean))
            elif ev.type == "error":
                break

        tail = strip_stream_artifacts(buffer.strip())
        if tail:
            full_text_parts.append(tail)
            await pool.put(Message.from_text(tail))

        await pool.finish()
        await pool.wait_drained()
    finally:
        await pool.shutdown()

    return "".join(full_text_parts)
