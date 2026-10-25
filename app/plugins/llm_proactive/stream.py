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

from app.agent.delivery.pool import StreamSendPool
from app.agent.loop.provider_view import _modalities_for_chain
from app.agent.loop.tool_step import StepRequest, run_stream_step
from app.agent.providers import SERVED_MODEL_KEY, log_token_usage
from app.core.logger import account_label
from app.domain.message import Message

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
    """已下沉到 ``loop/tool_step.accumulate_tool_call``。

    保留这个名字只为外部脚本/插件的历史 import 不至于直接炸（没人调用它）。
    """
    from app.agent.loop.tool_step import accumulate_tool_call

    accumulate_tool_call(slot_map, tc)


async def stream_send_initiative(*args, **kwargs):
    """主动消息流式生成（公开入口，返回累计文本）。

    包一层 `usage_scope`：让 provider 层的统计把这次消耗记到 `llm_proactive` 名下，
    而不是 `unknown`。内层函数体保持原样（重排缩进风险太大）。
    """
    from app.agent.usage.context import usage_scope

    runtime = args[0] if args else kwargs.get("runtime")
    session_id = args[2] if len(args) > 2 else kwargs.get("session_id", "")
    with usage_scope(
        bot_id=getattr(runtime, "bot_id", ""),
        source="llm_proactive",
        session_id=str(session_id or ""),
    ):
        return await _stream_send_initiative_inner(*args, **kwargs)



async def _stream_send_initiative_inner(
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
    tool_ctx=None,
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
    from app.agent.policy import policy_view

    policy_cfg = policy_view(runtime) or config
    max_len = int(
        policy_cfg.get("stream_sentence_max_length")
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
    # 本次请求（含工具多轮/空回复重试）的 token 消耗：结束后 info 一次
    usage_sink: dict = {}

    async def _emit(sentence: str) -> None:
        """一句话产出的唯一出口：累积全文 + 进消息池（与普通消息流式同口径）。"""
        full_text_parts.append(sentence)
        await pool.put(Message.from_text(sentence))

    async def _on_round_end(round_summary) -> None:
        if round_summary.images_appended:
            from app.agent import logger

            logger.add_info(account_label(getattr(runtime, 'bot_id', '?'))).info(
                "[ExpandImage] 主动/定时任务工具轮取回的图片已追加到下一轮请求"
            )

    try:
        step = await run_stream_step(
            StepRequest(
                runtime=runtime,
                chain=chain,
                messages=messages,
                params={
                    "model": model or config.get("model", "deepseek-chat"),
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "tools": tools if use_tools else None,
                    # 执行器只给 runner 用：它会从 params 里取走、绝不继续传给 provider
                    # （provider 一看到 tool_executor 就自己再跑一遍循环，P1 要消灭的正是这个）
                    "tool_executor": tool_executor if use_tools else None,
                    # 主动消息/定时任务同样吃「空回复重试」：零产出会让本轮静默不发
                    "max_empty_retries": _max_empty_retries(config),
                    "usage_sink": usage_sink,
                },
                max_rounds=rounds,
                tool_ctx=tool_ctx,
                modalities=_modalities_for_chain(chain),
                on_text=_emit,
                on_round_end=_on_round_end,
                max_msg_len=max_len,
            )
        )

        # 一句话没发不等于"模型没干活"：把思考字数留下来，避免日志里看不出真实原因
        if step.reasoning_chars and not full_text_parts:
            from app.agent import logger

            logger.add_info(account_label(getattr(runtime, 'bot_id', '?'))).warning(
                f"[LLM] 主动消息只产出思考、无正文（reasoning {step.reasoning_chars} 字符，"
                f"多半是思考吃满 max_tokens={max_tokens}）：本轮静默"
            )

        await pool.finish()
        await pool.wait_drained()
    finally:
        # 无论正常结束还是中途异常，都只 info 一次本次请求的 token 消耗；
        # 模型名取实际服务者（回退后不是主模型），与 chat.stream_response 同源
        log_token_usage(
            usage_sink,
            model=str(usage_sink.get(SERVED_MODEL_KEY) or model or config.get("model", "") or ""),
            chars=len("".join(full_text_parts)),
            stream=True,
        )
        await pool.shutdown()

    return "".join(full_text_parts)
