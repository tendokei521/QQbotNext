"""主动消息 / 定时任务流式发送助手。

当开启 ``stream_proactive_enabled`` / ``stream_scheduled_enabled`` 时，
主动消息和定时任务使用与普通消息相同的流式发送配置：
- 流式生成；
- 按句子切分；
- 进入 StreamSendPool；
- 使用相同的发送间隔、前后缀、队列策略。

同时也支持工具循环（与 ``chat.stream_response`` 同一套约定）：主动消息 / 定时任务
此前完全不传 tools，等于没有主动性；这里补上后，模型同样可以先取聊天环境信息
（群名、成员、被引用消息）再开口。
"""

from __future__ import annotations

from typing import Any

from app.domain.message import Message
from app.llm.providers import iter_stream_with_fallback, log_token_usage
from app.llm.send_pool import StreamSendPool
from app.llm.splitter import split_sentences, strip_stream_artifacts
from app.llm.tool_loop import normalize_and_execute_tool_calls

# 与 chat.DEFAULT_MAX_TOOL_ROUNDS 保持一致（调用方通常会显式传配置值）
DEFAULT_MAX_TOOL_ROUNDS = 5
# 与 chat.DEFAULT_EMPTY_REPLY_RETRIES 保持一致（对齐不能 import chat：会循环导入）
DEFAULT_EMPTY_REPLY_RETRIES = 1


def _max_empty_retries(config, default: int = DEFAULT_EMPTY_REPLY_RETRIES) -> int:
    """空回复重试次数，取值口径与 ``chat._max_empty_retries`` 一致（0–3）。"""
    try:
        value = int((config or {}).get("empty_reply_retries", default))
    except (TypeError, ValueError):
        return default
    return max(0, min(value, 3))


def _accumulate_tool_call(slot_map: dict, tc: dict) -> None:
    """把流式工具调用碎片按 index 累积（与 chat.stream_response 同一处理）。"""
    index = int(tc.get("index", 0) or 0)
    slot = slot_map.setdefault(index, {
        "id": "",
        "type": "function",
        "function": {"name": "", "arguments": ""},
    })
    frag = tc.get("function") or {}
    # 防御：部分模型/中转商的流式碎片里 id/name/arguments 可能为 null
    slot["id"] += tc.get("id") or ""
    slot["function"]["name"] += frag.get("name") or ""
    slot["function"]["arguments"] += frag.get("arguments") or ""


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
    tools: list[dict] | None = None,
    tool_executor=None,
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
) -> str:
    """使用流式 + 消息池发送一段主动/定时内容，返回完整文本。

    发送由消息池完成，调用方不要重复发送。

    工具轮次里产出的文本同样会即时进入消息池（与普通消息的流式行为一致）：
    模型在调用工具前通常不会先说话，真出现"先声明再查"的少量文本也属自然表达。
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
    use_tools = bool(tools) and tool_executor is not None
    rounds = max(1, int(max_tool_rounds or DEFAULT_MAX_TOOL_ROUNDS)) if use_tools else 1

    async def send_message(msg: Message) -> None:
        if is_group:
            await bot.send_group_msg(int(target), msg)
        else:
            await bot.send_private_msg(int(target), msg)

    pool = StreamSendPool(config, send_message=send_message)

    full_text_parts: list[str] = []
    tool_results: list[dict] = []
    # 本次请求（含工具多轮/空回复重试）的 token 消耗：结束后 info 一次
    usage_sink: dict = {}

    try:
        for _round in range(rounds):
            buffer = ""
            tool_calls: dict[int, dict] = {}

            async for ev in iter_stream_with_fallback(
                chain,
                messages,
                model=model or config.get("model", "deepseek-chat"),
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools if use_tools else None,
                tool_executor=tool_executor if use_tools else None,
                # 主动消息/定时任务同样吃「空回复重试」：零产出会让本轮静默不发
                max_empty_retries=_max_empty_retries(config),
                usage_sink=usage_sink,
            ):
                if ev.type == "text":
                    buffer += ev.text
                    sentences, buffer = split_sentences(buffer, max_length=max_len)
                    for sentence in sentences:
                        clean = strip_stream_artifacts(sentence)
                        if clean:
                            full_text_parts.append(clean)
                            await pool.put(Message.from_text(clean))
                elif ev.type == "tool_call":
                    _accumulate_tool_call(tool_calls, ev.tool_call or {})
                elif ev.type == "error":
                    break

            tail = strip_stream_artifacts(buffer.strip())
            if tail:
                full_text_parts.append(tail)
                await pool.put(Message.from_text(tail))

            if not tool_calls:
                break

            tool_calls_list = [tool_calls[idx] for idx in sorted(tool_calls)]
            normalized_calls, tool_messages = await normalize_and_execute_tool_calls(
                tool_calls_list, tool_executor, tool_results
            )
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": normalized_calls,
            })
            messages.extend(tool_messages)

        await pool.finish()
        await pool.wait_drained()
    finally:
        # 无论正常结束还是中途异常，都只 info 一次本次请求的 token 消耗
        log_token_usage(
            usage_sink,
            model=str(model or config.get("model", "") or ""),
            chars=len("".join(full_text_parts)),
            stream=True,
        )
        await pool.shutdown()

    return "".join(full_text_parts)
