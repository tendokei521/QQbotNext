"""核心聊天逻辑（框架级 MainAgent 流程）。

职责已收敛为三块：

1. **取值**：``prepare_prompt`` 把事件归一化成 ``assembly.PromptRequest``
   （会话/历史/背景/工具/记忆/指代/图片）；
2. **请求**：``generate_response``（非流式）与 ``stream_response``（流式），
   两者只差 provider 调用方式；
3. **指令**：``#llm`` 指令入口 ``handle`` → ``handle_commands``（总表与帮助见 ``app.agent.commands``）。

消息的**顺序与清洗**不在本模块，见 ``app.agent.assembly.assembler``（块表）。
"""


import asyncio
import time

from app.agent import logger
from app.agent.assembly import assembler as assembly
from app.agent.commands import (
    is_help_action,
    parse_action,
)
from app.agent.loop.assistant_output import (  # noqa: F401 - 重导出保持 chat.* API
    _WARNED_SPEECH_INTENT,
    _build_speech,
    _clean_output_for_history,
    _flush_speech,
    _speech_intent,
)
from app.agent.loop.command_dispatch import (  # noqa: F401 - 重导出保持 chat.* API
    _availability,
    _send_help,
    handle_commands,
)
from app.agent.loop.context_blocks import (  # noqa: F401 - 重导出保持 chat.* API
    _append_round_images,
    _build_group_pre_history,
    _commit_history_enrichment,
    _event_nickname,
    _format_session_history,
    _history_media_hook,
    _segments_as_dicts,
)
from app.agent.loop.generate import generate_response  # noqa: F401 - 保持 chat.* API
from app.agent.loop.prompt_prep import (  # noqa: F401 - 重导出保持 chat.* API
    _fetch_pre_history,
    prepare_prompt,
)
from app.agent.loop.provider_view import (  # noqa: F401 - 保持 chat.* API
    _modalities_for_chain,
    _provider_chain_for,
    _provider_config_for,
)
from app.agent.loop.tool_source import (  # noqa: F401 - 重导出保持 chat.* API
    _collect_llm_ext,
    _context_expand_flags,
    _images_enabled,
)
from app.agent.loop.tool_step import (
    KIND_ERROR,
    KIND_MAX_ROUNDS,
    STEP_TEXT,
    StepRequest,
    StepResult,
    iter_stream_step,
)
from app.agent.loop.turn_metrics import (  # noqa: F401 - 重导出保持 chat.* API
    _MAX_TOKENS_WARNED,
    DEFAULT_EMPTY_REPLY_RETRIES,
    DEFAULT_MAX_TOOL_ROUNDS,
    DEFAULT_REASONING_OVERFLOW_RETRIES,
    _effective_max_tokens,
    _history_meta_flags,
    _input_tokens_estimate,
    _log_request_line,
    _max_empty_retries,
    _max_tool_rounds,
    _reasoning_overflow_retries,
    _record_stream_telemetry,
    _served_model_label,
    _turn_budget,
    _usage_delta,
    _usage_snapshot,
    log_debug_prompt_if_enabled,
    log_debug_response_if_enabled,
)
from app.agent.loop.turn_runner import TurnAborted
from app.agent.prompts.system import REASONING_OVERFLOW_NUDGE
from app.agent.providers import log_token_usage
from app.agent.session.manager import SessionManager
from app.agent.tools.spec import build_tools, make_executor
from app.core.logger import account_label
from app.domain.modalities import supports_tool_use
from app.domain.trigger import extract_text

#: 思考吃满预算后追加的收敛提示（只在本轮确实想过、却一个正文字都没有时追加一次）。


#: 已经提醒过的 (bot_id, agent 值, 生效值)，避免每条消息刷同一条告警


# ==================== 发言通道（文本=思考，发言=send_message） ====================

#: 「配置想开发言通道但没生效」的告警去重（bot_id 集合）：持久性配置矛盾不该每条刷日志


async def handle(module, event):
    """``#llm`` 指令入口（普通消息不经此处：由 LlmPipeline 走 generate/stream_response）。

    流水线在 ``commands.is_command(ctx.user_text)`` 时把事件交给本函数；
    因此这里只需要处理指令，不再保留"旧版自己发消息"的完整回复路径。

    ``#llm help`` 是**静态指令表**：不依赖模型，也不检查 API 密钥——密钥没配好时
    正是最需要看指令表的时候。其余指令仍需密钥与场景开关齐备。
    """
    message_type = event.message_type
    if message_type not in ("group", "private"):
        return

    raw_text = extract_text(event.message).strip()
    action = parse_action(raw_text)
    if action is None:
        # 非指令消息由流水线负责；这里静默返回，避免出现两条回复路径
        return

    config = module.config
    if message_type == "private":
        if not config.get("private_enable", True):
            return
    else:
        if not config.get("group_enable", False):
            return

    if not is_help_action(action):
        api_key = _provider_config_for(module).get("api_key", "")
        if not api_key:
            logger.add_info(account_label(module.bot_id)).error(
                f"[{module.name}] API 密钥未配置，请先在 Provider 预设中选择连接配置"
            )
            return
    await _handle_command_event(module, event, config)


async def _handle_command_event(module, event, config) -> None:
    """把 ``#llm`` 指令派发给命令处理器（群/私聊共用）。"""
    session_mgr = SessionManager(str(module.bot_id))
    is_private = event.message_type == "private"
    if is_private:
        session_id = f"private_{event.user_id}"
        group_id = None
    else:
        group_id = str(event.group.group_id)
        session_id = f"group_{group_id}"
    await handle_commands(
        module, session_mgr, session_id, group_id, str(event.user_id),
        extract_text(event.message).strip(), event.is_admin,
        is_private=is_private, event=event,
    )


async def build_initiative_tools(
    runtime,
    session_id: str,
    is_private: bool,
    *,
    bot=None,
    user_id=None,
    group_id=None,
    schedule_enable: bool = False,
):
    """主动消息 / 定时任务路径的工具（没有触发事件）。

    这两条路径此前**完全不传 tools**，也就完全没有主动性：模型只能看到会话历史，
    拿不到群名、成员信息，也无法展开记录里的 @ / 引用。返回
    ``(specs, skill_blocks, tool_ctx)``。

    提示词块不在这里返回：它是「触发与调度」模块自己的能力文案
    （`app/plugins/llm_trigger/src/prompt.py`），由插件自己构建。

    ``schedule_enable`` 默认 False：主动发言/定时任务自身不需要再创建定时任务。
    """
    specs, skill_blocks, tool_ctx = await _collect_llm_ext(
        runtime,
        None,
        session_id,
        is_private,
        schedule_enable,
        bot=bot,
        user_id=user_id,
        group_id=group_id,
    )
    return specs, skill_blocks, tool_ctx


async def stream_response(runtime, event, ctx=None):
    """流式生成回复：按完整句子产出文本，内部处理多轮工具调用。

    与 ``generate_response`` 的会话/历史逻辑保持一致；
    但每次产出一个完整句子（str），由 LlmPipeline 负责 pre_send / 发送 / post_send。
    """
    config = runtime.config
    if not runtime.provider_config().get("api_key", ""):
        logger.add_info(account_label(runtime.bot_id)).error("[LLM] API 密钥未配置，请在 Provider 预设中选择连接配置")
        return

    message_type = getattr(event, "message_type", "")
    if message_type not in ("group", "private"):
        return

    # 场景开关先挡一层（prepare_prompt 不重复判断），再走统一取值
    if message_type == "private":
        if not config.get("private_enable", True):
            return
    else:
        if not config.get("group_enable", False):
            return

    # 取值收敛在 prepare_prompt（与 generate_response 完全同一套口径）；
    # 流式仅额外需要 stream_sentence_max_length 作为切句长度。
    req, session, meta = await prepare_prompt(runtime, event, ctx)
    if req is None:
        # 以前这里是裸 return：参与度模式下"过完门却整轮消失"完全无迹可查。
        from app.core.skip_log import log_skip

        log_skip(
            getattr(runtime, "bot_id", "?"),
            "prompt_unavailable",
            f"session={getattr(ctx, 'session_id', '?')} · group={getattr(event, 'group_id', '')}",
            hint="本轮无法组装请求（会话冷却 / 空消息），详情见 debug 日志",
            level="debug",
        )
        return
    session_id = meta["session_id"]
    provider_chain = meta["provider_chain"]
    modalities = meta["modalities"]
    all_specs = meta["all_specs"]
    tool_ctx = meta["tool_ctx"]
    session_mgr = meta["session_mgr"]
    # Q3：本轮的会话事件日志（request/header / turn/* / tool/*）；拿不到就是 None（旁路）
    from app.agent.session.session_log import (
        EVENT_REQUEST_HEADER,
        EVENT_TURN_END,
        EVENT_TURN_START,
        log_of,
        request_header,
        safe_append,
    )

    turn_log = log_of(session_mgr, session)
    safe_append(turn_log, EVENT_TURN_START, kind="chat", session_id=session_id, model=meta["model"])
    # 历史清洗开关归 `llm_prompt` 模块所有（未接管时回退 agent 配置）
    from app.agent.policy import policy_config

    meta_cfg = policy_config(runtime, "meta")
    is_private = meta["is_private"]
    model = meta["model"]
    from app.agent.policy import policy_view

    policy_cfg = policy_view(runtime) or config
    max_msg_len = int(
        policy_cfg.get("stream_sentence_max_length")
        or config.get("max_message_length", 200)
        or 200
    )

    # 发言通道（P2）：``speech`` 非 None 时，模型输出的文本只是思考，发言靠 send_message；
    # 本轮一句话没说 = 静默（正常结局），不再用兜底话术填空、也不做催发言/补救轮。
    speech = meta.get("speech")
    speech_by_tool = speech is not None
    spoken_parts: list[str] = []

    messages = assembly.build_for(runtime, req)
    _append_round_images(messages, tool_ctx, modalities)
    # post_assemble：插件可在此读写本轮请求（1:1 复刻第三方组装格式的接入点）
    messages = await assembly.apply_post_assemble(runtime, ctx, messages, req=req)
    if messages is None:
        return

    log_debug_prompt_if_enabled(runtime, session_id, messages, ctx)

    # P4 在线不变式（默认关）：核对"事件日志派生"与"内存历史"是否仍然一致
    if ctx is not None and session is not None:
        try:
            from app.agent.session.verify import verify_history_projection

            verify_history_projection(runtime, session_mgr, session, where="chat.stream_response")
        except Exception as e:  # noqa: BLE001 —— 自检是旁路，绝不影响本轮
            logger.add_info(account_label(runtime.bot_id)).debug(
                f"[Invariant] 自检调用失败（已忽略）: {e}"
            )

    # 本次请求真正生效的 max_tokens（链上 provider 配置赢过 Agent 配置）：打出来，
    # 免得再出现「8192 到底哪设的」这种排查——见 _effective_max_tokens。
    eff_max_tokens, max_tokens_src = _effective_max_tokens(provider_chain, meta["max_tokens"])

    logger.add_info(account_label(runtime.bot_id)).info(
        f"流式 API 请求 -> {session_id} (task: {session.task_id}), 消息数: {len(messages)}, "
        f"max_tokens={eff_max_tokens}（来自{max_tokens_src}）"
    )

    use_tools = bool(all_specs) and supports_tool_use(modalities)
    if speech_by_tool and not use_tools:
        # 发言通道靠工具调用，而本轮拿不到工具（模型/适配器不支持 tool calling）：
        # 模型想说什么都发不出去。这里**不降级为「文本即回复」**——那会把"思考"
        # 当正文发进群里，等于把沉默语义彻底抹掉。直接结束本轮并留痕，让用户换模型。
        logger.add_info(account_label(runtime.bot_id)).error(
            "[LLM] 本轮处于发言通道但工具不可用（模型不支持 tool calling）→ 本轮静默。"
            "请换用支持工具调用的模型，或把回复模式切回条件触发"
        )
        return
    tools = build_tools(all_specs) if use_tools else None
    tool_executor = make_executor(all_specs, tool_ctx) if use_tools else None

    # Q3：把这一轮**最终请求**的指纹记进日志（system hash / 工具定义 / 参数 / 链）
    header_params = {"temperature": meta["temperature"], "max_tokens": meta["max_tokens"]}
    safe_append(
        turn_log,
        EVENT_REQUEST_HEADER,
        **request_header(messages, tools, model=meta["model"], chain=provider_chain, params=header_params),
    )
    # Q3：指纹自检（默认关）——同输入必须同指纹、且确实落进了日志
    try:
        from app.agent.session.verify import verify_request_reproducible

        verify_request_reproducible(
            runtime, session_mgr, session, messages, tools,
            model=meta["model"], chain=provider_chain, params=header_params,
            where="chat.stream_response",
        )
    except Exception as e:  # noqa: BLE001 —— 自检是旁路
        logger.add_info(account_label(runtime.bot_id)).debug(
            f"[Invariant] 请求指纹自检失败（已忽略）: {e}"
        )

    full_text_parts: list[str] = []
    tool_results: list[dict] = []
    # 全程思考字符数（只在诊断日志里用：证明「想过但没正文」而不是空回复）
    reasoning_total = 0
    stream_start = time.monotonic()
    stream_error_text = ""
    # 本次请求（含工具多轮/空回复重试）的 token 消耗：全部结束、发完最后一段后 info 一次
    usage_sink: dict = {}
    # 与非流式共用同一轮数上限（此前这里硬编码 5，两处不一致且不可配）
    max_tool_rounds = _max_tool_rounds(config)
    # 「思考吃满预算」的收敛重试预算（只补一次，避免把轮数全耗在同一个问题上）
    overflow_retries = _reasoning_overflow_retries(config)
    overflow_used = 0
    # 本轮总闸门：工具多轮 + 空回复重试 + 收敛重试都从这一本账上扣
    turn_budget = _turn_budget(config)
    input_tokens_estimate = _input_tokens_estimate(messages)

    def _write_partial_history(text: str) -> bool:
        """流式边发边写历史：先落一条，后续分片只更新它。返回是否写成功。

        pipeline 在**每片发送成功后**调 ``mark_last_assistant_message_id``，它认
        "最近一条 assistant"。如果等全部发完才写历史，那个"最近一条"还是**上一轮**的
        发言 —— message_id 就全补在上一轮上（引用指错、环境记录去重失效）。
        发言通道（speech）本来就是先写后发，这里把纯文本流式路径对齐。
        """
        upsert = getattr(session_mgr, "upsert_streaming_assistant", None)
        if not callable(upsert):
            return False
        try:
            return bool(upsert(session_id, text))
        except Exception as e:  # noqa: BLE001 —— 句柄回填是增强信息，不该中断回复
            logger.add_info(account_label(runtime.bot_id)).debug(
                f"[LLM] 流式历史预写失败（已忽略）: {e}"
            )
            return False

    # 一次请求的参数集合：工具执行器交给共享运行器（它会取走、绝不继续传给适配器）
    params = {
        "model": meta["model"],
        "temperature": meta["temperature"],
        "max_tokens": meta["max_tokens"],
        "tools": tools,
        "tool_executor": tool_executor,
        "max_empty_retries": _max_empty_retries(config),
        "usage_sink": usage_sink,
        "budget": turn_budget,
        "input_tokens_estimate": input_tokens_estimate,
    }
    # 最后一份轮次小结（收尾判断要看"本轮"而不是累计值）
    last_round_text = ""
    # 协作式取消句柄（流水线在"新消息取代本轮"时取消它）；None = 本轮不可取消
    abort = getattr(ctx, "abort", None) if ctx is not None else None
    aborted = False
    rounds_so_far = 0

    # 轮次开始：广播事实（观测方拿得到本轮 model/tools 指纹）
    from app.agent.hooks import run_stage

    if ctx is not None:
        await run_stage(runtime, "turn_started", ctx)

    while True:
        step = StepResult()
        events = iter_stream_step(
            StepRequest(
                runtime=runtime,
                chain=provider_chain,
                messages=messages,
                params=params,
                max_rounds=max_tool_rounds,
                tool_ctx=tool_ctx,
                modalities=modalities,
                max_msg_len=max_msg_len,
                abort=abort,
                tool_log=turn_log,
            ),
            step,
        )
        try:
            async for kind, payload in events:
                if kind == STEP_TEXT:
                    full_text_parts.append(payload)
                    if speech_by_tool:
                        # 思考：只累积不发送（发言靠 send_message）。切句只在真要发文本时才有意义。
                        continue
                    # 先写历史再发送（pipeline 发送成功后会回填 message_id）
                    _write_partial_history("".join(full_text_parts))
                    yield payload
                    continue

                # ---- 一轮结束：记账 + 发言通道 flush + 提醒注入 ----
                summary = payload
                rounds_so_far += 1
                last_round_text = summary.round_text
                reasoning_total += summary.reasoning_chars
                _log_request_line(
                    runtime,
                    session_id=session_id,
                    round_index=summary.index,
                    served_model=_served_model_label(usage_sink, meta["model"]),
                    max_tokens=eff_max_tokens,
                    max_tokens_src=max_tokens_src,
                    input_tokens=_usage_delta(usage_sink, summary.usage_before, "prompt_tokens"),
                    output_tokens=_usage_delta(usage_sink, summary.usage_before, "completion_tokens"),
                    input_estimate=input_tokens_estimate,
                    finish_reason=summary.finish_reason,
                    text_chars=len(summary.round_text),
                    reasoning_chars=summary.reasoning_chars,
                    tools=summary.tool_calls,
                    error=summary.error,
                    budget=turn_budget,
                )
                if summary.error:
                    # 记录失败详情，便于定位（error 事件文本通常含 API 返回的状态码与原因）
                    stream_error_text = summary.error
                    logger.add_info(account_label(runtime.bot_id)).error(
                        f"[LLM] 流式请求失败 -> {session_id} (task: {session.task_id}), "
                        f"round={summary.index}, 已产文本={bool(full_text_parts)}, "
                        f"工具数={summary.tool_calls}: {summary.error}"
                    )

                # 逐轮广播：观测方拿得到本轮耗时/token/工具数（`ctx.state["step"]`）
                if ctx is not None:
                    ctx.state["step"] = summary
                    await run_stage(runtime, "step_completed", ctx)

                if speech_by_tool:
                    # round 边界 flush：模型一旦调 send_message，本轮工具执行完就真发出去，
                    # 它随后还能继续"想下一句"。体感接近真人"想一句说一句"，
                    # 也不至于因为工具循环很久而让群友干等。
                    # 收尾轮也 flush：否则"最后一轮才调 send_message"的话永远不会发出去。
                    #
                    # 注意：这里**不再**注入「剩余轮次催发言」提醒——参与度模式下"不说话"
                    # 是合法结局（沉默即正常结束），催它开口等于把沉默语义又抹掉。
                    async for message in _flush_speech(speech, session_mgr, session_id, meta_cfg, event):
                        spoken_parts.append(message.text)
                        yield message
        except TurnAborted as e:
            # 只在边界被取消：已经 yield 出去的句子、已经 upsert 的历史前缀都算数。
            # 这里**不能 raise**：收尾（历史定稿 / mark_replied / 遥测）必须照常走完，
            # 否则下一轮上下文里"刚说过的话"不存在。
            aborted = True
            logger.add_info(account_label(runtime.bot_id)).info(
                f"[LLM] 本轮被取消（{e.cause}）-> {session_id}，已产出 {len(full_text_parts)} 句"
            )
            if ctx is not None:
                # 告诉流水线：把已经发出去的前缀写进历史（与 should_stop 打断同一口径）
                ctx.state["interrupted"] = True
        finally:
            # 调用方可能在本步骤中途停下（句间打断）：确保事件流被关掉，已产出的事实留在 step 上
            await events.aclose()

        # 本轮步骤执行过的工具（跨收敛轮要合并，遥测与诊断日志都读它）
        tool_results.extend(step.tool_results)

        if aborted:
            break

        if step.kind == KIND_ERROR:
            # 这里**不能 return**：此前直接 return 跳过了写历史 / mark_replied /
            # 记忆蒸馏 / 遥测落库，而用户那边其实已经收到正文（流式是边产边发的）。
            # 后果：下一轮上下文里"我刚说过的话"不存在，用户追问时模型一脸茫然；
            # 群里也因为没有 mark_replied 被当成"没回复过"。统一走下面的收尾逻辑。
            break

        if step.kind == KIND_MAX_ROUNDS:
            logger.add_info(account_label(runtime.bot_id)).warning(
                f"流式工具循环超过 {max_tool_rounds} 轮，强制结束（可调大 max_tool_rounds 配置）"
            )
            break

        # 思考吃满输出预算：模型把 max_tokens 全花在 reasoning 里，正文还没开始就被截断
        # （线上表现：输出 8192 = 上限、回复 0 字符、用户收到兜底话术）。同一条 prompt
        # 原样重试必然再打满一次，所以改成追加一次「直接作答」提醒再要一枪。
        # 这是"改 prompt 再问"，不是循环控制，所以留在调用方（P1 只收口循环）。
        if (
            not last_round_text
            and step.last_round_reasoning
            and overflow_used < overflow_retries
        ):
            overflow_used += 1
            logger.add_info(account_label(runtime.bot_id)).warning(
                f"[LLM] 思考吃满输出预算（reasoning {step.last_round_reasoning} 字符, "
                f"finish={step.finish_reason or '?'}, max_tokens={eff_max_tokens}"
                f"（来自{max_tokens_src}）），追加一次收敛轮而非原样重试 -> {session_id}"
            )
            messages.append({"role": "user", "content": REASONING_OVERFLOW_NUDGE})
            continue

        break

    if speech_by_tool:
        # 参与度模式下**不做"补救轮"**：旧模式里"输出=回复"，@ 了不说话是故障；
        # 新模式里"沉默"是合法结局，再补一轮等于逼它开口，与参与度语义相反。
        full_text = "\n".join(spoken_parts)
        if speech.spoken == 0:
            # 静默：不发、不写 assistant 历史。这是正常结局，不是失败——但必须留痕，
            # 否则"哑火太多"会变成查不到原因的现象。流式失败要说失败，别说静默。
            log = logger.add_info(account_label(runtime.bot_id))
            detail = (
                f"{session_id} (task: {session.task_id}, finished={speech.finished}, "
                f"reason={speech.finish_reason or '-'}"
            )
            if stream_error_text:
                log.error(f"[LLM] 流式失败且未开口 -> {detail}, error={stream_error_text})")
            else:
                log.info(f"[LLM] 静默（看过但没说话）-> {detail})")
            if ctx is not None and not stream_error_text:
                ctx.state["silent"] = True
            # 空回复一律静默：不再补「抱歉，我暂时无法回答」这类兜底话术。
        elif not is_private:
            session.mark_replied()
        await asyncio.to_thread(session_mgr.history.save_session, session)
    else:
        full_text = "".join(full_text_parts)
        # 兜底：流式响应为空时避免“不回复”，给用户一个可见的占位回复
        if not full_text.strip():
            # 若模型其实想过（只是没写出正文），把真实原因和预算来源一并留痕，
            # 否则日志只剩下"空回复"，看不出是「思考吃满 max_tokens」还是网关断流。
            reason = (
                f"，思考 {reasoning_total} 字符但无正文（多半是思考吃满 max_tokens"
                f"={eff_max_tokens}，来自{max_tokens_src}）"
                if reasoning_total
                else ""
            )
            logger.add_info(account_label(runtime.bot_id)).warning(
                f"[LLM] 流式模型返回空回复，按静默处理（不补兜底话术）-> {session_id} "
                f"(task: {session.task_id}, 已执行工具数={len(tool_results)}{reason})"
            )
            if ctx is not None and not stream_error_text:
                ctx.state["silent"] = True

        # 收尾把正文定稿到**同一条**历史（分片发送期间已 upsert 过）。stub/旧
        # session_mgr 没有 upsert 时才退回 add_message，保证行为不回退。
        _cleaned = _clean_output_for_history(meta_cfg, full_text) if full_text.strip() else ""
        # 本轮思考（截断）随这条 assistant 消息一起存：会话数据页可展开溯源。
        # 只写一次、只给"这条正文"，不进模型上下文、也不参与渲染。
        _reasoning = str(getattr(step, "reasoning_text", "") or "").strip()
        if _cleaned:
            if not _write_partial_history(_cleaned):
                session_mgr.add_message(
                    session_id, "assistant", _cleaned, reasoning=_reasoning
                )
            elif _reasoning:
                # 流式分片已 upsert 过正文：补一次思考，别让这条历史缺溯源
                setter = getattr(session_mgr, "set_last_assistant_reasoning", None)
                if callable(setter):
                    setter(session_id, _reasoning)
        if hasattr(session_mgr, "end_streaming_assistant"):
            session_mgr.end_streaming_assistant(session_id)
        if _cleaned and not is_private:
            session.mark_replied()
        await asyncio.to_thread(session_mgr.history.save_session, session)
        # 发言通道：历史条目由 `_flush_speech` 逐气泡写入，那时还没拿到整轮思考，
        # 这里补到最近一条 assistant 上（会话数据页展开"思考过程"用它）。
        if speech_by_tool:
            final_reasoning = str(getattr(step, "reasoning_text", "") or "").strip()
            setter = getattr(session_mgr, "set_last_assistant_reasoning", None)
            if final_reasoning and callable(setter):
                setter(session_id, final_reasoning)
                await asyncio.to_thread(session_mgr.history.save_session, session)

    _commit_history_enrichment(runtime, session_id, ctx)

    # 结构化结局：调用方（遥测/turn 事件/记忆蒸馏）据此判断"这轮到底怎么结束的"，
    # 不再从日志文案里猜——兜底话术与"想过但没写"在 text 上长得一样。
    turn_reason = _stream_turn_reason(
        aborted=aborted,
        stream_error_text=stream_error_text,
        speech_by_tool=speech_by_tool,
        spoken=len(spoken_parts),
        model_text="".join(full_text_parts),
        reasoning_chars=reasoning_total,
    )

    # 成功判定：旧路径看"有没有产出文本"；发言通道下"看过但没说话"同样是成功，
    # 用它去记失败会让"哑火"与"故障"混在一起。
    if speech_by_tool:
        stream_success = not stream_error_text
    else:
        stream_success = bool("".join(full_text_parts).strip()) and not stream_error_text

    # 收尾广播（写历史/遥测之前）：结局已经定了，观测量能读到 reason
    safe_append(
        turn_log,
        EVENT_TURN_END,
        reason=str(turn_reason),
        rounds=rounds_so_far,
        interrupted=aborted,
    )
    if ctx is not None:
        ctx.state["turn_reason"] = str(turn_reason)
        await run_stage(runtime, "turn_stopping", ctx)

    _record_stream_telemetry(
        runtime,
        session_id,
        provider_chain,
        model,
        messages,
        full_text_parts,
        tool_results,
        stream_start,
        success=stream_success,
        error_text=stream_error_text,
        usage=usage_sink,
        reason=str(turn_reason),
    )

    # 本次请求到此完全结束：info 一次 token 消耗（上游没报 usage 就不打）
    log_token_usage(
        usage_sink,
        model=_served_model_label(usage_sink, meta["model"]),
        chars=len("".join(full_text_parts)),
        stream=True,
    )
    # 「LLM调试」的返回日志（与非流式路径同一口径）：整轮正文 + 思考 + 工具调用。
    # 发言通道下"正文只是思考"，模型写了正文却没调发送工具时只有这里能看到原文。
    log_debug_response_if_enabled(
        runtime,
        session_id,
        text="".join(full_text_parts),
        reasoning=str(getattr(step, "reasoning_text", "") or ""),
        tool_calls=tool_results,
        reason=str(turn_reason),
        rounds=rounds_so_far,
        error=stream_error_text,
        usage=usage_sink,
        ctx=ctx,
    )
    # 预算总账只留在 debug.log：逐请求打这个数字对用户没有信息量（见 turn_metrics
    # 里请求行的说明），而每轮都进 user.log 只会把用户日志刷成内部字段清单。
    logger.add_info(account_label(runtime.bot_id)).debug(
        f"[Budget] 本轮 {turn_budget.describe()}"
        f"，max_tokens={eff_max_tokens}（来自{max_tokens_src}）-> {session_id}"
    )

    if ctx is not None:
        ctx.response_text = full_text
        ctx.state["turn_reason"] = str(turn_reason)


def _stream_turn_reason(
    *,
    aborted: bool = False,
    stream_error_text: str,
    speech_by_tool: bool,
    spoken: int,
    model_text: str,
    reasoning_chars: int,
):
    """流式路径的结局判定（与 `turn_runner.classify_step` 同一套口径）。

    - 被取消 → `aborted`（优先级最高：用户看到的只是半句，不能记成完成）；
    - 失败 → `error`；
    - 发言通道：有入稿 = `completed`，没开口 = `silent`；
    - 纯文本：有正文 = `completed`；没正文但想过一大段 = `max_tokens`（思考吃满预算），
      否则 `silent`。
    """
    from app.agent.loop.turn_runner import TurnReason

    if aborted:
        return TurnReason.ABORTED
    if stream_error_text:
        return TurnReason.ERROR
    if speech_by_tool:
        return TurnReason.COMPLETED if spoken else TurnReason.SILENT
    if model_text.strip():
        return TurnReason.COMPLETED
    return TurnReason.MAX_TOKENS if reasoning_chars else TurnReason.SILENT


