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
    _force_speak_round,
    _should_force_speak,
    _speech_fallback_enabled,
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
from app.agent.loop.tool_loop import normalize_and_execute_tool_calls
from app.agent.loop.tool_source import (  # noqa: F401 - 重导出保持 chat.* API
    _collect_llm_ext,
    _context_expand_flags,
    _images_enabled,
)
from app.agent.loop.turn_metrics import (  # noqa: F401 - 重导出保持 chat.* API
    _MAX_TOKENS_WARNED,
    DEFAULT_EMPTY_REPLY_RETRIES,
    DEFAULT_MAX_TOOL_ROUNDS,
    DEFAULT_REASONING_OVERFLOW_RETRIES,
    REASONING_OVERFLOW_NUDGE,
    _effective_max_tokens,
    _history_meta_flags,
    _input_tokens_estimate,
    _log_debug_prompt,
    _log_request_line,
    _max_empty_retries,
    _max_tokens_override_note,
    _max_tool_rounds,
    _reasoning_overflow_retries,
    _record_stream_telemetry,
    _served_model_label,
    _turn_budget,
    _usage_delta,
    _usage_snapshot,
)
from app.agent.providers import log_token_usage
from app.agent.providers.invoker import resolve_invoker
from app.agent.session.manager import SessionManager
from app.agent.tools.spec import build_tools, make_executor
from app.domain.modalities import supports_tool_use
from app.domain.splitter import split_sentences, strip_stream_artifacts
from app.domain.trigger import extract_text

#: 思考吃满预算后追加的收敛提示（只在本轮确实想过、却一个正文字都没有时追加一次）。


#: 已经提醒过的 (bot_id, agent 值, 生效值)，避免每条消息刷同一条告警


def _proactive_instruction(config, all_specs, user_text: str, ctx=None) -> str | None:
    """本轮「主动性」协议块：只在本轮确有对应工具时注入（唯一一块 system 提示）。

    用户原始文本优先取 llm_enhance 暂存的 sent_text，避免元信息前缀干扰意图识别。
    """
    from app.agent.assembly.prompt import build_proactive_instruction

    raw_text = user_text
    if ctx is not None:
        info = ctx.state.get("user_context") or {}
        raw_text = (info.get("sent_text") or user_text) or user_text
    return build_proactive_instruction(
        config, raw_text, available_tools={spec.name for spec in (all_specs or [])}
    )


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
    if message_type == "private":
        session_id = f"private_{event.user_id}"
    else:
        session_id = f"group_{event.group.group_id}"

    raw_text = extract_text(event.message).strip()
    action = parse_action(raw_text)
    if action is None:
        # 非指令消息由流水线负责；这里静默返回，避免出现两条回复路径
        return

    config = module.config
    config.set_session(session_id)
    try:
        if message_type == "private":
            if not config.get("private_enable", True):
                return
        else:
            if not config.get("group_enable", False):
                return

        if not is_help_action(action):
            api_key = _provider_config_for(module).get("api_key", "")
            if not api_key:
                logger.add_info(f"#{module.bot_id}").error(
                    f"[{module.name}] API 密钥未配置，请先在 Provider 预设中选择连接配置"
                )
                return
        await _handle_command_event(module, event, config)
    finally:
        config.clear_session()


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
    """主动消息 / 定时任务路径的工具与「主动性」提示块（没有触发事件）。

    这两条路径此前**完全不传 tools**，也就完全没有主动性：模型只能看到会话历史，
    拿不到群名、成员信息，也无法展开记录里的 @ / 引用。返回
    ``(specs, skill_blocks, tool_ctx, proactive_instruction)``，其中提示块同样按本轮
    实际可用工具裁剪（没有能力的行不会出现）。

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
    # 无用户提问 → 不做历史/环境意图补强（user_text=""），只给常驻协议行
    instruction = _proactive_instruction(runtime.config, specs, "")
    return specs, skill_blocks, tool_ctx, instruction


async def stream_response(runtime, event, ctx=None):
    """流式生成回复：按完整句子产出文本，内部处理多轮工具调用。

    与 ``generate_response`` 的会话/历史逻辑保持一致；
    但每次产出一个完整句子（str），由 LlmPipeline 负责 pre_send / 发送 / post_send。
    """
    config = runtime.config
    if not runtime.provider_config().get("api_key", ""):
        logger.add_info(f"#{runtime.bot_id}").error("[LLM] API 密钥未配置，请在 Provider 预设中选择连接配置")
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
        return
    session_id = meta["session_id"]
    provider_chain = meta["provider_chain"]
    modalities = meta["modalities"]
    all_specs = meta["all_specs"]
    tool_ctx = meta["tool_ctx"]
    session_mgr = meta["session_mgr"]
    # 历史清洗开关归 `llm_message_meta` 模块所有（未接管时回退 agent 配置）
    from app.agent.policy import policy_config

    meta_cfg = policy_config(runtime, "meta")
    is_private = meta["is_private"]
    user_id = meta["user_id"]
    model = meta["model"]
    from app.agent.policy import policy_view

    policy_cfg = policy_view(runtime) or config
    max_msg_len = int(
        policy_cfg.get("stream_sentence_max_length")
        or config.get("max_message_length", 200)
        or 200
    )

    # 发言通道（P2）：``speech`` 非 None 时，模型输出的文本只是思考，发言靠 send_message；
    # 本轮一句话没说 = 静默（正常结局），不再用兜底话术填空。
    from app.agent.delivery.speech import DEFAULT_REMINDER_ROUNDS, SPEAK_REMINDER

    speech = meta.get("speech")
    speech_by_tool = speech is not None
    reminder_rounds = int(policy_cfg.get("speech_reminder_rounds", DEFAULT_REMINDER_ROUNDS) or 0)
    spoken_parts: list[str] = []

    messages = assembly.build_for(runtime, req)
    _append_round_images(messages, tool_ctx, modalities)
    # post_assemble：插件可在此读写本轮请求（1:1 复刻第三方组装格式的接入点）
    messages = await assembly.apply_post_assemble(runtime, ctx, messages, req=req)
    if messages is None:
        return

    _log_debug_prompt(runtime, session_id, messages, debug_enabled=bool(ctx and ctx.state.get("debug_prompt", False)))

    # 本次请求真正生效的 max_tokens（链上 provider 配置赢过 Agent 配置）：打出来，
    # 免得再出现「8192 到底哪设的」这种排查——见 _effective_max_tokens。
    eff_max_tokens, max_tokens_src = _effective_max_tokens(provider_chain, meta["max_tokens"])
    _mt_note = _max_tokens_override_note(runtime, eff_max_tokens, max_tokens_src)
    if _mt_note:
        logger.add_info(f"#{runtime.bot_id}").warning(f"[LLM] {_mt_note}")

    logger.add_info(f"#{runtime.bot_id}").info(
        f"流式 API 请求 -> {session_id} (task: {session.task_id}), 消息数: {len(messages)}, "
        f"max_tokens={eff_max_tokens}（来自{max_tokens_src}）"
    )

    use_tools = bool(all_specs) and supports_tool_use(modalities)
    tools = build_tools(all_specs) if use_tools else None
    tool_executor = make_executor(all_specs, tool_ctx) if use_tools else None

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
            logger.add_info(f"#{runtime.bot_id}").debug(
                f"[LLM] 流式历史预写失败（已忽略）: {e}"
            )
            return False

    for _round in range(max_tool_rounds):
        round_text_parts: list[str] = []
        buffer = ""
        tool_calls: dict[int, dict] = {}
        stream_error = ""
        reasoning_chars = 0
        finish_reason = ""
        usage_before = _usage_snapshot(usage_sink)

        async for ev in resolve_invoker(runtime).stream(
            runtime=runtime,
            chain=provider_chain,
            messages=messages,
            params={
                "model": meta["model"],
                "temperature": meta["temperature"],
                "max_tokens": meta["max_tokens"],
                "tools": tools,
                "tool_executor": tool_executor,
                "max_empty_retries": _max_empty_retries(config),
                "usage_sink": usage_sink,
                "budget": turn_budget,
                "input_tokens_estimate": input_tokens_estimate,
            },
        ):
            if ev.type == "text":
                buffer += ev.text
                if speech_by_tool:
                    # 思考：只累积不发送。切句也只在真正要发文本时才有意义。
                    continue
                sentences, buffer = split_sentences(buffer, max_length=max_msg_len)
                for sentence in sentences:
                    clean = strip_stream_artifacts(sentence)
                    if clean:
                        round_text_parts.append(clean)
                        full_text_parts.append(clean)
                        # 先写历史再发送（pipeline 发送成功后会回填 message_id）
                        _write_partial_history("".join(full_text_parts))
                        yield clean
            elif ev.type == "tool_call":
                tc = ev.tool_call or {}
                index = int(tc.get("index", 0) or 0)
                slot = tool_calls.setdefault(index, {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                })
                # 防御：部分模型/中转商的流式工具碎片里 id/name/arguments 可能为 null，
                # 直接拼接会抛 "can only concatenate str (not NoneType) to str"。
                frag = tc.get("function") or {}
                slot["id"] += tc.get("id") or ""
                slot["function"]["name"] += frag.get("name") or ""
                slot["function"]["arguments"] += frag.get("arguments") or ""
            elif ev.type == "error":
                stream_error = ev.text
                break
            elif ev.type == "reasoning":
                # 思考流只计数、绝不发给用户；留着是为了区分「想过但没正文」和真正的空回复
                reasoning_chars += len(ev.text or "")
            elif ev.type == "done":
                finish_reason = ev.finish_reason or finish_reason

        tail = strip_stream_artifacts(buffer.strip())
        if tail:
            if speech_by_tool:
                # 思考尾段同样不发送（但要留在 full_text 里，供遥测/调试看模型想了什么）
                round_text_parts.append(tail)
                full_text_parts.append(tail)
            else:
                round_text_parts.append(tail)
                full_text_parts.append(tail)
                _write_partial_history("".join(full_text_parts))
                yield tail
        buffer = ""

        round_text = "".join(round_text_parts)
        reasoning_total += reasoning_chars
        full_text = "".join(full_text_parts)
        _log_request_line(
            runtime,
            session_id=session_id,
            round_index=_round,
            served_model=_served_model_label(usage_sink, meta["model"]),
            max_tokens=eff_max_tokens,
            max_tokens_src=max_tokens_src,
            input_tokens=_usage_delta(usage_sink, usage_before, "prompt_tokens"),
            output_tokens=_usage_delta(usage_sink, usage_before, "completion_tokens"),
            input_estimate=input_tokens_estimate,
            finish_reason=finish_reason,
            text_chars=len(round_text),
            reasoning_chars=reasoning_chars,
            tools=len(tool_calls),
            error=stream_error,
            budget=turn_budget,
        )

        if stream_error:
            # 记录失败详情，便于定位（error 事件文本通常含 API 返回的状态码与原因）
            stream_error_text = stream_error
            logger.add_info(f"#{runtime.bot_id}").error(
                f"[LLM] 流式请求失败 -> {session_id} (task: {session.task_id}), "
                f"round={_round}, 已产文本={bool(full_text_parts)}, 工具数={len(tool_calls)}: {stream_error}"
            )
            # 这里**不能 return**：此前直接 return 跳过了写历史 / mark_replied /
            # 记忆蒸馏 / 遥测落库，而用户那边其实已经收到正文（流式是边产边发的）。
            # 后果：下一轮上下文里"我刚说过的话"不存在，用户追问时模型一脸茫然；
            # 群里也因为没有 mark_replied 被当成"没回复过"。统一走下面的收尾逻辑。
            break

        # 思考吃满输出预算：模型把 max_tokens 全花在 reasoning 里，正文还没开始就被截断
        # （线上表现：输出 8192 = 上限、回复 0 字符、用户收到兜底话术）。同一条 prompt
        # 原样重试必然再打满一次，所以改成追加一次「直接作答」提醒再要一枪。
        if (
            not round_text
            and not tool_calls
            and reasoning_chars
            and overflow_used < overflow_retries
        ):
            overflow_used += 1
            logger.add_info(f"#{runtime.bot_id}").warning(
                f"[LLM] 思考吃满输出预算（reasoning {reasoning_chars} 字符, "
                f"finish={finish_reason or '?'}, max_tokens={eff_max_tokens}"
                f"（来自{max_tokens_src}）），追加一次收敛轮而非原样重试 -> {session_id}"
            )
            messages.append({"role": "user", "content": REASONING_OVERFLOW_NUDGE})
            continue

        if not tool_calls:
            break

        # 组装完整 tool_calls（碎片按 index 有序）→ 复用共享 helper：
        # id 自洽（缺 id 生成 call_{index}）+ arguments 解析 + 执行 + 构造回传，
        # 与 openai_compat.chat() 的非流式工具循环共用同一套逻辑。
        tool_calls_list = [tool_calls[idx] for idx in sorted(tool_calls)]
        normalized_calls, tool_messages = await normalize_and_execute_tool_calls(
            tool_calls_list, tool_executor, tool_results
        )

        messages.append({
            "role": "assistant",
            "content": round_text or None,
            "tool_calls": normalized_calls,
        })
        messages.extend(tool_messages)

        # 工具轮里 expand_image 取回的图片必须**在本轮**递给模型：此前只在请求开始时
        # 抽一次 round_images，工具执行期间积攒的那些要等下一轮对话才被抽走，
        # 于是模型当轮只能凭工具回执里的文字描述瞎猜（还被告知"你会直接看到"）。
        if _append_round_images(messages, tool_ctx, modalities):
            logger.add_info(f"#{runtime.bot_id}").info(
                f"[ExpandImage] 工具轮取回的图片已追加到下一轮请求 -> {session_id}"
            )

        if speech_by_tool:
            # round 边界 flush：模型一旦调 send_message，本轮工具执行完就真发出去，
            # 它随后还能继续"想下一句"。体感接近真人"想一句说一句"，
            # 也不至于因为工具循环很久而让群友干等。
            async for message in _flush_speech(speech, session_mgr, session_id, meta_cfg, event):
                spoken_parts.append(message.text)
                yield message
            rounds_left = max_tool_rounds - (_round + 1)
            if rounds_left > 0 and speech.spoken == 0 and rounds_left <= reminder_rounds:
                # 剩的轮次不多了还没开口：用 user 角色提醒（中途 system 会被适配器上提到最前）
                messages.append({
                    "role": "user",
                    "content": SPEAK_REMINDER.format(rounds=rounds_left),
                })
    else:
        logger.add_info(f"#{runtime.bot_id}").warning(
            f"流式工具循环超过 {max_tool_rounds} 轮，强制结束（可调大 max_tool_rounds 配置）"
        )

    if speech_by_tool:
        # 被直接点名（@ / 私聊）却一句话没说：再要一次，避免"叫了不理"。
        # 用非流式补一轮是因为这里只关心"有没有入稿"，不需要把文本流给用户。
        # 流式已失败时不再补：错误多半是认证/配额/上下文超长，再打一枪只会再烧一次钱。
        if (
            speech.spoken == 0
            and not stream_error_text
            and _should_force_speak(config, ctx, is_private)
        ):
            await _force_speak_round(
                runtime, speech, messages, provider_chain, meta, tools, tool_executor
            )
            async for message in _flush_speech(speech, session_mgr, session_id, meta_cfg, event):
                spoken_parts.append(message.text)
                yield message

        full_text = "\n".join(spoken_parts)
        if speech.spoken == 0:
            # 静默：不发、不写 assistant 历史。这是正常结局，不是失败——但必须留痕，
            # 否则"哑火太多"会变成查不到原因的现象。流式失败要说失败，别说静默。
            log = logger.add_info(f"#{runtime.bot_id}")
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
            if _speech_fallback_enabled(config):
                fallback = "抱歉，我暂时无法回答，请稍后再试。"
                full_text = fallback
                spoken_parts.append(fallback)
                yield fallback
                # 兜底话术**已经发给用户**了 → 必须进历史，否则下一轮"我没说过"
                session_mgr.add_message(
                    session_id, "assistant", _clean_output_for_history(meta_cfg, fallback)
                )
                if not is_private:
                    session.mark_replied()
        elif not is_private:
            session.mark_replied()
        await asyncio.to_thread(session_mgr.history.save_session, session)
    else:
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
            logger.add_info(f"#{runtime.bot_id}").warning(
                f"[LLM] 流式模型返回空回复，使用兜底文本 -> {session_id} "
                f"(task: {session.task_id}, 已执行工具数={len(tool_results)}{reason})"
            )
            full_text = "抱歉，我暂时无法回答，请稍后再试。"
            yield full_text

        # 收尾把正文定稿到**同一条**历史（分片发送期间已 upsert 过）。stub/旧
        # session_mgr 没有 upsert 时才退回 add_message，保证行为不回退。
        _cleaned = _clean_output_for_history(meta_cfg, full_text)
        if not _write_partial_history(_cleaned):
            session_mgr.add_message(session_id, "assistant", _cleaned)
        if hasattr(session_mgr, "end_streaming_assistant"):
            session_mgr.end_streaming_assistant(session_id)
        if not is_private:
            session.mark_replied()
        await asyncio.to_thread(session_mgr.history.save_session, session)

    _commit_history_enrichment(runtime, session_id, ctx)

    # 成功判定：旧路径看"有没有产出文本"；发言通道下"看过但没说话"同样是成功，
    # 用它去记失败会让"哑火"与"故障"混在一起。
    if speech_by_tool:
        stream_success = not stream_error_text
    else:
        stream_success = bool("".join(full_text_parts).strip()) and not stream_error_text

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
    )

    # 本次请求到此完全结束：info 一次 token 消耗（上游没报 usage 就不打）
    log_token_usage(
        usage_sink,
        model=_served_model_label(usage_sink, meta["model"]),
        chars=len("".join(full_text_parts)),
        stream=True,
    )
    # 预算总账只留在 debug.log：逐请求打这个数字对用户没有信息量（见 turn_metrics
    # 里请求行的说明），而每轮都进 user.log 只会把用户日志刷成内部字段清单。
    logger.add_info(f"#{runtime.bot_id}").debug(
        f"[Budget] 本轮 {turn_budget.describe()}"
        f"，max_tokens={eff_max_tokens}（来自{max_tokens_src}）-> {session_id}"
    )

    if ctx is not None:
        ctx.response_text = full_text


