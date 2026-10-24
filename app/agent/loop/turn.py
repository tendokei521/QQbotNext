"""无事件的完整一轮对话：``run_turn``。

## 为什么有这个模块

框架此前只有三个"跑一轮"的入口，且**签名全部硬绑真实事件对象**：

| 入口 | 位置 |
|---|---|
| `prepare_prompt(runtime, event, ctx)` | `loop/prompt_prep.py` |
| `generate_response(runtime, event, ctx)` | `loop/generate.py` |
| `stream_response(runtime, event, ctx)` | `loop/chat.py` |

于是"没有事件"的发起者（主动消息 / 定时任务 / 任何外部触发）只能各抄一套
取值 + 组装 + 请求 + 落库（`proactivity/manager.py`、`scheduler.py`、`stream.py` 三份）。

`run_turn` 把这件事收敛成一个**公开契约**：

```python
result = await run_turn(runtime, TurnRequest(
    session_id="group_123", is_private=False, user_text="总结一下今天群里聊了什么",
))
print(result.text, len(result.tool_results))
```

## 契约边界（刻意收窄）

- **负责**：取值 → 组装 → `post_assemble` → 请求（含工具循环）→ 可选写历史 → 遥测；
- **不负责**：发送（交付层 `delivery.channel.send_item` 的事）、`pre_send`/`post_send`
  （流水线的事）、发言通道与部分历史 upsert（`stream_response` 专有语义，本函数不搬）。

因此 `run_turn` 可以被模块直接调用而不产生"和主流水线抢发送"的问题。
"""

from __future__ import annotations

from app.core.logger import account_label

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.agent import logger
from app.agent.assembly import assembler as assembly
from app.agent.loop.assistant_output import _clean_output_for_history
from app.agent.loop.context_blocks import (
    _append_round_images,
    _commit_history_enrichment,
)
from app.agent.loop.prompt_prep import prepare_prompt
from app.agent.loop.turn_input import from_request
from app.agent.loop.turn_metrics import (
    REASONING_OVERFLOW_NUDGE,
    _effective_max_tokens,
    _input_tokens_estimate,
    _max_empty_retries,
    _max_tool_rounds,
    _reasoning_overflow_retries,
    _turn_budget,
)
from app.agent.providers.invoker import resolve_invoker
from app.agent.tools.spec import build_tools, make_executor
from app.domain.modalities import supports_tool_use
from app.domain.splitter import split_sentences, strip_stream_artifacts
from app.domain.tags import strip_all_tags


@dataclass
class TurnRequest:
    """一轮对话的全部输入（替代"必须先有事件"这个前置条件）。

    Attributes:
        session_id: ``group_<id>`` / ``private_<id>``。
        is_private: 是否私聊（决定历史/工具/权限作用域）。
        user_text: 本轮用户内容（可为空：纯图片或纯指令场景）。
        user_id / group_id: 触发者与群（无事件场景可为空）。
        kind: 请求性质，写进 ``PromptRequest.kind``（chat / proactive / schedule / module）。
        event: 可选的真实事件（有则用于图片解析、引用指令、`message_id` 锚点）。
        bot: 无事件时的连接（前置背景/图片需要）；缺省取 ``event.bot``。
        stream: ``True`` 流式（逐句回调 ``on_sentence``）；``None`` 按 ``config.stream_output``。
        record_history: 是否把本轮 assistant 文本写进会话历史。
        system_prompt: 覆盖人设（None = 用配置）。
        extra_user_content_parts: 追加到 user 消息的额外文本块（如 system_reminder）。
    """

    session_id: str
    is_private: bool
    user_text: str = ""
    user_id: str = ""
    group_id: str | None = None
    kind: str = "module"
    event: Any = None
    bot: Any = None
    stream: bool | None = None
    record_history: bool = True
    system_prompt: str | None = None
    extra_user_content_parts: list[str] = field(default_factory=list)


@dataclass
class TurnResult:
    """一轮对话的结果。

    Attributes:
        text: 最终回复正文（流式时是所有句子的拼接）。
        messages: 本轮**最终请求体**（对账/调试用，等价于 prompt dump 的内容）。
        tool_results: 工具执行记录 ``[{name, args, result}]``。
        usage: token 用量（上游没报则为空 dict）。
        blocked: 本轮被钩子拦下（`post_assemble` 置空或 `job.skip`），没有请求模型。
        error: 失败原因（空串 = 正常）。
        session_id: 会话 id 回显。
    """

    text: str = ""
    messages: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    blocked: bool = False
    error: str = ""
    session_id: str = ""


async def run_turn(
    runtime: Any,
    req: TurnRequest,
    *,
    ctx: Any = None,
    on_sentence: Callable[[str], Awaitable[None]] | None = None,
) -> TurnResult:
    """跑一轮完整对话。

    Args:
        runtime: ``AgentRuntime``。
        req: 见 `TurnRequest`。
        ctx: 可选 ``LlmContext``（钩子共享状态）；None 时以 req 合成一个。
        on_sentence: 流式模式下每产出一个完整句子回调一次（发送与否由调用方决定）。

    Returns:
        `TurnResult`。**任何失败都不抛异常**（返回 ``error`` 非空的结果），
        与 `BaseModule.llm_chat` 的降级口径一致。
    """
    result = TurnResult(session_id=req.session_id)
    config = getattr(runtime, "config", None)
    if config is None:
        result.error = "no_runtime"
        return result

    # 归属上下文：provider 层记账时据此写 source / session（拿不到就是 unknown）
    from app.agent.usage.context import usage_scope
    from app.agent.usage.labels import label_from_session_id

    label = ""
    if ctx is not None:
        label = str(getattr(ctx, "session_label", "") or "")
    if not label:
        label = label_from_session_id(req.session_id)
    scope = usage_scope(
        bot_id=getattr(runtime, "bot_id", ""),
        source=req.kind or "module",
        session_id=req.session_id,
        session_label=label,
        group_id=req.group_id or "",
        user_id=req.user_id or "",
    )

    try:
        with scope:
            return await _run_inner(runtime, req, result, ctx=ctx, on_sentence=on_sentence)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 —— 模块调用不该因为一轮失败而炸掉
        logger.add_info(account_label(getattr(runtime, 'bot_id', '?'))).exception(
            f"[Turn] run_turn 失败 ({req.session_id}): {e}"
        )
        result.error = str(e)
        return result


async def _run_inner(
    runtime: Any,
    req: TurnRequest,
    result: TurnResult,
    *,
    ctx: Any,
    on_sentence: Callable[[str], Awaitable[None]] | None,
) -> TurnResult:
    """``run_turn`` 的主体：取值 → 组装 → 请求 → 收尾。"""
    config = runtime.config
    turn = from_request(
        config,
        session_id=req.session_id,
        is_private=req.is_private,
        user_text=req.user_text,
        user_id=req.user_id,
        group_id=req.group_id,
    )
    if turn is None:
        result.blocked = True
        return result

    ctx = ctx or _make_ctx(runtime, req)
    prompt_req, session, meta = await prepare_prompt(
        runtime, req.event, ctx, turn=turn, bot=req.bot
    )
    if prompt_req is None or meta is None:
        result.blocked = True
        return result

    # 覆盖人设 / 追加额外 user 内容块（只在调用方显式要求时）
    if req.system_prompt is not None:
        prompt_req.config = _override_system_prompt(prompt_req, req.system_prompt)
    if req.extra_user_content_parts:
        prompt_req.extra_user_content_parts = list(req.extra_user_content_parts)
    if req.kind:
        prompt_req.kind = req.kind

    session_id = meta["session_id"]
    provider_chain = meta["provider_chain"]
    modalities = meta["modalities"]
    all_specs = meta["all_specs"]
    tool_ctx = meta["tool_ctx"]
    session_mgr = meta["session_mgr"]
    is_private = meta["is_private"]

    messages = assembly.build_for(runtime, prompt_req)
    _append_round_images(messages, tool_ctx, modalities)
    messages = await assembly.apply_post_assemble(runtime, ctx, messages, req=prompt_req)
    if messages is None:
        result.blocked = True
        return result
    result.messages = list(messages)

    use_tools = bool(all_specs) and supports_tool_use(modalities)
    from app.agent.policy import policy_view

    policy_cfg = policy_view(runtime) or config
    stream = (
        bool(policy_cfg.get("stream_output", False)) if req.stream is None else bool(req.stream)
    )
    started = time.monotonic()
    sink: dict = {}

    if stream:
        text, tool_results = await _stream_once(
            runtime,
            provider_chain=provider_chain,
            messages=messages,
            meta=meta,
            all_specs=all_specs,
            tool_ctx=tool_ctx,
            use_tools=use_tools,
            config=config,
            on_sentence=on_sentence,
            usage_sink=sink,
        )
    else:
        text, tool_results = await _chat_once(
            runtime,
            provider_chain=provider_chain,
            messages=messages,
            meta=meta,
            all_specs=all_specs,
            tool_ctx=tool_ctx,
            use_tools=use_tools,
            config=config,
            usage_sink=sink,
        )

    result.text = text
    result.tool_results = tool_results
    result.usage = dict(sink)
    _record_telemetry(
        runtime,
        session_id=session_id,
        provider_chain=provider_chain,
        model=meta["model"],
        messages=messages,
        text=text,
        tool_results=tool_results,
        started=started,
        usage=sink,
        stream=stream,
    )

    if req.record_history and text.strip() and session is not None:
        await _write_history(runtime, session_mgr, session, session_id, text, is_private, ctx)
    _commit_history_enrichment(runtime, session_id, ctx)
    # 一轮结束：广播给订阅者（记忆蒸馏、统计、审计等业务自己接）
    from app.agent.hooks import run_stage

    await run_stage(runtime, "turn_completed", ctx)
    return result


# ==================== 请求（非流式 / 流式）====================


def _call_params(meta: dict, all_specs: Any, tool_ctx: Any, use_tools: bool, config: Any) -> dict:
    """一次请求的参数集合（与 `loop/generate.py` 保持同一口径）。"""
    return {
        "model": meta["model"],
        "temperature": meta["temperature"],
        "max_tokens": meta["max_tokens"],
        "tools": build_tools(all_specs) if use_tools else None,
        "tool_executor": make_executor(all_specs, tool_ctx) if use_tools else None,
        "max_tool_rounds": _max_tool_rounds(config),
        "max_empty_retries": _max_empty_retries(config),
        "budget": _turn_budget(config),
        "input_tokens_estimate": _input_tokens_estimate([]),
    }


async def _chat_once(
    runtime: Any,
    *,
    provider_chain: list[dict],
    messages: list[dict],
    meta: dict,
    all_specs: Any,
    tool_ctx: Any,
    use_tools: bool,
    config: Any,
    usage_sink: dict,
) -> tuple[str, list[dict]]:
    """非流式：一次请求（工具循环在 invoker 内部完成）。"""
    params = _call_params(meta, all_specs, tool_ctx, use_tools, config)
    params["input_tokens_estimate"] = _input_tokens_estimate(messages)
    response = await resolve_invoker(runtime).chat(
        runtime=runtime, chain=provider_chain, messages=messages, params=params
    )
    _merge_usage(usage_sink, getattr(response, "usage", None))

    # 思考吃满预算：追加一次收敛提示再要一枪（与 generate_response 同口径）
    overflow_used = 0
    while (
        not response.ok
        and (getattr(response, "reasoning", "") or "").strip()
        and overflow_used < _reasoning_overflow_retries(config)
    ):
        overflow_used += 1
        messages.append({"role": "user", "content": REASONING_OVERFLOW_NUDGE})
        response = await resolve_invoker(runtime).chat(
            runtime=runtime, chain=provider_chain, messages=messages, params=params
        )
        _merge_usage(usage_sink, getattr(response, "usage", None))

    # expand_image 补图轮：非流式工具循环在 provider 内部，中途拿不到 messages，
    # 工具轮取回的图片会留在 `tool_ctx.extra["round_images"]` 里没人追加。
    # 这里补问一轮（上限 1 次，防模型反复取图死循环）。
    if _append_round_images(messages, tool_ctx, meta.get("modalities")):
        logger.add_info(account_label(getattr(runtime, 'bot_id', '?'))).info(
            f"[ExpandImage] 工具轮取回的图片已追加为补全材料，补问一轮 -> {meta.get('session_id', '')}"
        )
        params["input_tokens_estimate"] = _input_tokens_estimate(messages)
        response = await resolve_invoker(runtime).chat(
            runtime=runtime, chain=provider_chain, messages=messages, params=params
        )
        _merge_usage(usage_sink, getattr(response, "usage", None))

    tool_results = list(getattr(response, "tool_results", None) or [])
    if not response.ok:
        return "抱歉，我暂时无法回答，请稍后再试。", tool_results
    text = strip_stream_artifacts(strip_all_tags(getattr(response, "text", "") or ""))
    if not text.strip():
        reasoning = len(getattr(response, "reasoning", "") or "")
        eff, src = _effective_max_tokens(provider_chain, meta["max_tokens"])
        hint = f"，思考 {reasoning} 字符但无正文（max_tokens={eff}，来自{src}）" if reasoning else ""
        logger.add_info(account_label(getattr(runtime, 'bot_id', '?'))).warning(
            f"[Turn] 空回复，使用兜底文本{hint}"
        )
        return "抱歉，我暂时无法回答，请稍后再试。", tool_results
    return text, tool_results


async def _stream_once(
    runtime: Any,
    *,
    provider_chain: list[dict],
    messages: list[dict],
    meta: dict,
    all_specs: Any,
    tool_ctx: Any,
    use_tools: bool,
    config: Any,
    on_sentence: Callable[[str], Awaitable[None]] | None,
    usage_sink: dict,
) -> tuple[str, list[dict]]:
    """流式：逐句回调 ``on_sentence``，返回累计正文与工具结果。

    工具循环由调用方（本函数）驱动，与 `chat.stream_response` 一致；
    区别只在于产出物**不发送**，交给调用方决定。
    """
    from app.agent.loop.tool_loop import normalize_and_execute_tool_calls

    params = _call_params(meta, all_specs, tool_ctx, use_tools, config)
    params["usage_sink"] = usage_sink
    params["input_tokens_estimate"] = _input_tokens_estimate(messages)
    from app.agent.policy import policy_view

    policy_cfg = policy_view(runtime) or config
    max_msg_len = int(
        policy_cfg.get("stream_sentence_max_length")
        or config.get("max_message_length", 200)
        or 200
    )
    max_rounds = _max_tool_rounds(config)
    tool_results: list[dict] = []
    full_parts: list[str] = []

    for _round in range(max_rounds):
        buffer = ""
        tool_calls: dict[int, dict] = {}
        async for ev in resolve_invoker(runtime).stream(
            runtime=runtime, chain=provider_chain, messages=messages, params=params
        ):
            if ev.type == "text":
                buffer += ev.text
                sentences, buffer = split_sentences(buffer, max_length=max_msg_len)
                for sentence in sentences:
                    clean = strip_stream_artifacts(sentence)
                    if clean:
                        full_parts.append(clean)
                        if on_sentence is not None:
                            await on_sentence(clean)
            elif ev.type == "tool_call":
                tc = ev.tool_call or {}
                index = int(tc.get("index", 0) or 0)
                slot = tool_calls.setdefault(
                    index,
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                frag = tc.get("function") or {}
                slot["id"] += tc.get("id") or ""
                slot["function"]["name"] += frag.get("name") or ""
                slot["function"]["arguments"] += frag.get("arguments") or ""
            elif ev.type == "error":
                logger.add_info(account_label(getattr(runtime, 'bot_id', '?'))).error(
                    f"[Turn] 流式请求失败: {ev.text}"
                )
                break

        tail = strip_stream_artifacts(buffer.strip())
        if tail:
            full_parts.append(tail)
            if on_sentence is not None:
                await on_sentence(tail)
        if not tool_calls:
            break

        tool_calls_list = [tool_calls[idx] for idx in sorted(tool_calls)]
        normalized_calls, tool_messages = await normalize_and_execute_tool_calls(
            tool_calls_list, params.get("tool_executor"), tool_results
        )
        messages.append(
            {
                "role": "assistant",
                "content": "".join(full_parts) or None,
                "tool_calls": normalized_calls,
            }
        )
        messages.extend(tool_messages)
        _append_round_images(messages, tool_ctx, meta["modalities"])

    text = "\n".join(full_parts)
    return text, tool_results


# ==================== 收尾助手 ====================


def _make_ctx(runtime: Any, req: TurnRequest):
    """合成一个最小 ``LlmContext``（钩子需要 ``ctx.state`` 与 ``ctx.messages``）。"""
    from app.agent.loop.context import LlmContext

    return LlmContext(
        event=req.event,
        runtime=runtime,
        bot=req.bot or getattr(req.event, "bot", None),
        session_id=req.session_id,
        user_text=req.user_text,
    )


def _override_system_prompt(prompt_req: Any, system_prompt: str) -> Any:
    """把本轮 system prompt 覆盖成调用方给的文本（不动全局配置）。

    ``PromptRequest.cfg`` 读的是 ``config`` 对象；这里用一个只覆盖
    ``system_prompt`` 的只读视图，避免污染真实配置。
    """

    class _Override:
        def __init__(self, base: Any, prompt: str) -> None:
            self._base = base
            self._prompt = prompt

        def get(self, key: str, default: Any = None) -> Any:
            if key == "system_prompt":
                return self._prompt
            getter = getattr(self._base, "get", None)
            return getter(key, default) if callable(getter) else default

        def __getattr__(self, name: str) -> Any:
            return getattr(self._base, name)

    prompt_req.config = _Override(prompt_req.config, system_prompt)
    return prompt_req.config


def _merge_usage(sink: dict, usage: Any) -> None:
    """把一次响应/流的 usage 累加进本轮账本。"""
    if not isinstance(usage, dict):
        return
    for key, value in usage.items():
        if isinstance(value, (int, float)):
            sink[key] = sink.get(key, 0) + value
        else:
            sink.setdefault(key, value)


def _record_telemetry(
    runtime: Any,
    *,
    session_id: str,
    provider_chain: list[dict],
    model: str,
    messages: list[dict],
    text: str,
    tool_results: list[dict],
    started: float,
    usage: dict,
    stream: bool,
) -> None:
    """写一条 LLM 调用遥测（失败只留痕）。"""
    telemetry = getattr(runtime, "telemetry", None)
    if telemetry is None:
        return
    first = provider_chain[0] if provider_chain else {}
    try:
        telemetry.record_call_simple(
            bot_id=getattr(runtime, "bot_id", None),
            session_id=session_id,
            provider=str(first.get("provider", "openai") or "openai"),
            model=str(first.get("model", model) or model),
            stream=stream,
            success=bool(text.strip()),
            latency_ms=(time.monotonic() - started) * 1000,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            message_count=len(messages),
            tool_calls=len(tool_results),
            characters=len(text),
            error="" if text.strip() else "empty_response",
        )
    except Exception as e:  # noqa: BLE001 —— 遥测不该影响一轮结果
        logger.add_info(account_label(getattr(runtime, 'bot_id', '?'))).debug(
            f"[Turn] 遥测记录失败（已忽略）: {e}"
        )


async def _write_history(
    runtime: Any,
    session_mgr: Any,
    session: Any,
    session_id: str,
    text: str,
    is_private: bool,
    ctx: Any = None,
) -> None:
    """把本轮回复写进会话历史（清洗口径与主路径一致）。"""
    try:
        import asyncio

        from app.agent.policy import policy_config

        session_mgr.add_message(
            session_id, "assistant", _clean_output_for_history(policy_config(runtime, "meta"), text)
        )
        if not is_private:
            session.mark_replied()
        await asyncio.to_thread(session_mgr.history.save_session, session)
        # 本轮工具取回的内容登记进「补全登记」，否则历史里的图片/展开引用会丢
        from app.agent.loop.context_blocks import _commit_history_enrichment

        _commit_history_enrichment(runtime, session_id, ctx)
    except Exception as e:  # noqa: BLE001 —— 写历史失败不该丢掉已生成的回复
        logger.add_info(account_label(getattr(runtime, 'bot_id', '?'))).warning(
            f"[Turn] 写历史失败（已忽略）: {e}"
        )
