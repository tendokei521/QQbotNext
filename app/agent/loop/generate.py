"""非流式生成：一次请求拿到完整回复，写历史并返回文本。

## 本模块只负责

`generate_response`：调 `prepare_prompt` 拿装配结果 → 走 provider 回退链请求 →
把回复写进会话历史 → 返回纯文本。它是**流式路径的对照组**：同一条 prompt 前提，
一次拿全，便于主动消息、定时任务、测试替身这类「不需要边生成边发」的调用。

## 为什么单独成文件

它原本和 `stream_response`（397 行）并排在 `chat.py` 里。两条路径的收尾口径必须一致
（谁写历史、写什么、失败怎么记），分开后对照读比上下翻 600 行容易得多。
"""

from __future__ import annotations

import asyncio
import time

from app.agent import logger
from app.agent.assembly import assembler as assembly
from app.agent.loop.assistant_output import _clean_output_for_history
from app.agent.loop.context_blocks import (
    _append_round_images,
    _commit_history_enrichment,
    _memory_consolidate,
)
from app.agent.loop.prompt_prep import prepare_prompt
from app.agent.loop.turn_metrics import (
    REASONING_OVERFLOW_NUDGE,
    _effective_max_tokens,
    _input_tokens_estimate,
    _log_debug_prompt,
    _log_request_line,
    _max_empty_retries,
    _max_tokens_override_note,
    _max_tool_rounds,
    _reasoning_overflow_retries,
    _turn_budget,
)
from app.agent.providers.invoker import resolve_invoker
from app.agent.tools.spec import build_tools, make_executor
from app.domain.modalities import supports_tool_use
from app.domain.splitter import strip_stream_artifacts
from app.domain.tags import strip_all_tags


async def generate_response(runtime, event, ctx=None) -> str | None:
    """LLM 流水线专用生成函数：只生成回复文本，不发送消息。

    会话/历史/背景/工具/记忆/图片的取值全部收敛在 ``prepare_prompt``，
    消息顺序与清洗交给 ``assembly``。
    """
    config = runtime.config
    if not runtime.provider_config().get("api_key", ""):
        logger.add_info(f"#{runtime.bot_id}").error("[LLM] API 密钥未配置，请在 Provider 预设中选择连接配置")
        return None
    message_type = getattr(event, "message_type", "")
    if message_type not in ("group", "private"):
        return None

    # 取值（会话/历史/背景/工具/记忆/指代/图片）全部收敛在 prepare_prompt；
    # 顺序与清洗交给 assembly（PromtRequest → 块表 → 归位 → 按模态清洗）。
    req, session, meta = await prepare_prompt(runtime, event, ctx)
    if req is None:
        return None
    session_id = meta["session_id"]
    provider_chain = meta["provider_chain"]
    modalities = meta["modalities"]
    all_specs = meta["all_specs"]
    tool_ctx = meta["tool_ctx"]
    session_mgr = meta["session_mgr"]
    is_private = meta["is_private"]

    messages = assembly.build_for(runtime, req)
    _append_round_images(messages, tool_ctx, modalities)
    # post_assemble：插件可在此读写本轮请求（1:1 复刻第三方组装格式的接入点）
    messages = await assembly.apply_post_assemble(runtime, ctx, messages, req=req)
    if messages is None:
        return None

    _log_debug_prompt(runtime, session_id, messages, debug_enabled=bool(ctx and ctx.state.get("debug_prompt", False)))

    logger.add_info(f"#{runtime.bot_id}").info(
        f"API 请求 -> {session_id} (task: {session.task_id}), 消息数: {len(messages)}"
    )

    use_tools = bool(all_specs) and supports_tool_use(modalities)
    eff_max_tokens, max_tokens_src = _effective_max_tokens(provider_chain, meta["max_tokens"])
    _mt_note = _max_tokens_override_note(runtime, eff_max_tokens, max_tokens_src)
    if _mt_note:
        logger.add_info(f"#{runtime.bot_id}").warning(f"[LLM] {_mt_note}")
    response_start = time.monotonic()
    # 本轮总闸门：所有重试（HTTP / 空回复 / 收敛 / 回退）都从这一本账上扣
    turn_budget = _turn_budget(config)
    input_tokens_estimate = _input_tokens_estimate(messages)
    call_params = {
        "model": meta["model"],
        "temperature": meta["temperature"],
        "max_tokens": meta["max_tokens"],
        "tools": build_tools(all_specs) if use_tools else None,
        "tool_executor": make_executor(all_specs, tool_ctx) if use_tools else None,
        "max_tool_rounds": _max_tool_rounds(config),
        "max_empty_retries": _max_empty_retries(config),
        "budget": turn_budget,
        "input_tokens_estimate": input_tokens_estimate,
    }

    async def _invoke():
        return await resolve_invoker(runtime).chat(
            runtime=runtime,
            chain=provider_chain,
            messages=messages,
            params=dict(call_params),
        )

    response = await _invoke()
    # 非流式的同款毛病：思考把 max_tokens 吃满时 text 为空、reasoning 有内容。
    # 原样重试没意义，追加一次「直接作答」提醒再要一枪（reasoning 只有部分
    # provider 会填，取不到就不动，保持旧行为）。
    overflow_used = 0
    while (
        not response.ok
        and (getattr(response, "reasoning", "") or "").strip()
        and overflow_used < _reasoning_overflow_retries(config)
    ):
        overflow_used += 1
        logger.add_info(f"#{runtime.bot_id}").warning(
            f"[LLM] 思考吃满输出预算（reasoning {len(response.reasoning)} 字符, "
            f"max_tokens={eff_max_tokens}（来自{max_tokens_src}）），追加一次收敛轮 -> {session_id}"
        )
        messages.append({"role": "user", "content": REASONING_OVERFLOW_NUDGE})
        response = await _invoke()

    # expand_image 补图轮：非流式的工具循环跑在 provider 内部（openai_compat.chat 里
    # while 循环），chat.py 拿不到中间态，于是工具轮里取回的图片**从未**送达模型——
    # 而工具回执却对模型说「下一轮对话里你会直接看到它」。这里把本轮积攒的图片
    # 作为补全材料追加后再问一次（上限 1 次，防模型反复取图造成死循环）。
    if _append_round_images(messages, tool_ctx, modalities):
        logger.add_info(f"#{runtime.bot_id}").info(
            f"[ExpandImage] 工具轮取回的图片已作为补全材料追加，补问一轮 -> {session_id}"
        )
        response = await _invoke()
    response_latency_ms = (time.monotonic() - response_start) * 1000
    _log_request_line(
        runtime,
        session_id=session_id,
        round_index=0,
        served_model=str((response.raw or {}).get("model", "") if isinstance(response.raw, dict) else "")
        or meta["model"],
        max_tokens=eff_max_tokens,
        max_tokens_src=max_tokens_src,
        input_tokens=int((response.usage or {}).get("prompt_tokens", 0) or 0),
        output_tokens=int((response.usage or {}).get("completion_tokens", 0) or 0),
        input_estimate=input_tokens_estimate,
        finish_reason=str(getattr(response, "finish_reason", "") or ""),
        text_chars=len(getattr(response, "text", "") or ""),
        reasoning_chars=len(getattr(response, "reasoning", "") or ""),
        tools=len(response.tool_results or []),
        error="" if response.ok else "未产出可用响应",
        budget=turn_budget,
    )
    # 与流式路径一致：预算总账只进 debug.log（见 turn_metrics 请求行的说明）
    logger.add_info(f"#{runtime.bot_id}").debug(
        f"[Budget] 本轮 {turn_budget.describe()}"
        f"，max_tokens={eff_max_tokens}（来自{max_tokens_src}）-> {session_id}"
    )

    if not response.ok:
        clean_response = "抱歉，我暂时无法回答，请稍后再试。"
    else:
        # 工具结果（定时任务已在工具循环中执行，最终回复是 LLM 基于结果的确认）
        if response.tool_results:
            for tr in response.tool_results:
                logger.add_info(f"#{runtime.bot_id}").info(
                    f"[Tool] {tr['name']} 执行 -> {str(tr['result'])[:80]}"
                )
        # 防御：剥离角色提示词可能输出的 <type=...> 标签，避免漏到客户端
        clean_response = strip_stream_artifacts(strip_all_tags(response.text))

    # 兜底：模型返回空内容时避免“不回复”，给用户一个可见的占位回复
    if not clean_response:
        reasoning_chars = len(getattr(response, "reasoning", "") or "")
        hint = (
            f"，思考 {reasoning_chars} 字符但无正文（多半是思考吃满 max_tokens"
            f"={eff_max_tokens}，来自{max_tokens_src}）"
            if reasoning_chars
            else ""
        )
        logger.add_info(f"#{runtime.bot_id}").warning(
            f"[LLM] 模型返回空回复，使用兜底文本 -> {session_id}{hint}"
        )
        clean_response = "抱歉，我暂时无法回答，请稍后再试。"

    telemetry = getattr(runtime, "telemetry", None)
    if telemetry is not None:
        usage = response.usage or {}
        first_cfg = provider_chain[0] if provider_chain else {}
        telemetry.record_call_simple(
            bot_id=runtime.bot_id,
            session_id=session_id,
            provider=str(first_cfg.get("provider", "openai") or "openai"),
            model=str(first_cfg.get("model", meta["model"]) or meta["model"]),
            stream=False,
            success=bool(response.ok),
            latency_ms=response_latency_ms,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            message_count=len(messages),
            tool_calls=len(response.tool_results or []),
            characters=len(clean_response),
            error="" if response.ok else "empty_response",
        )

    from app.agent.policy import policy_config

    session_mgr.add_message(
        session_id,
        "assistant",
        _clean_output_for_history(policy_config(runtime, "meta"), clean_response),
    )
    if not is_private:
        session.mark_replied()
    await asyncio.to_thread(session_mgr.history.save_session, session)

    _commit_history_enrichment(runtime, session_id, ctx)
    _memory_consolidate(runtime, session_id, is_private, session_mgr)
    return clean_response


