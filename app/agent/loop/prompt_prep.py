"""把事件装配成一次请求：`prepare_prompt` 是整个 Agent 的唯一「取值」入口。

## 本模块只负责

`prepare_prompt`（301 -> 现在这条路径的全部取值逻辑）与它的群聊前置历史抓取
`_fetch_pre_history`：模型参数、前置背景、去重、渲染、压缩、技能与工具、记忆检索、
指代块、图片解析都在这里完成；之后的装配（顺序 / 清洗）交给 `assembly.assembler`。

## 为什么单独成文件

它同时被**两条生成路径**用：`loop/generate.py`（非流式）与 `loop/chat.py`
（流式 + 指令 + 兜底）。放在 chat 里时 `generate` 必须反向 import chat 才能拿到它，
拆开后依赖是单向的：两边都 -> `prompt_prep`。

历史上的教训：这些步骤曾在 4 条路径里各写一遍，导致「记忆检索用什么文本」
「去重比哪个字符串」这类分歧。**只准有一个取值入口**，就是这个函数。
"""

"""核心聊天逻辑（框架级 MainAgent 流程）。

职责已收敛为三块：

1. **取值**：``prepare_prompt`` 把事件归一化成 ``assembly.PromptRequest``
   （会话/历史/背景/工具/记忆/指代/图片）；
2. **请求**：``generate_response``（非流式）与 ``stream_response``（流式），
   两者只差 provider 调用方式；
3. **指令**：``#llm`` 指令入口 ``handle`` → ``handle_commands``（总表与帮助见 ``app.agent.commands``）。

消息的**顺序与清洗**不在本模块，见 ``app.agent.assembly.assembler``（块表）。
"""



from app.agent import logger
from app.agent.assembly.assembler import PromptRequest
from app.agent.assembly.compress import maybe_compress_context
from app.agent.context.group import (
    fetch_private_online_history,
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
    _memory_autosave,
    _memory_block,
    _memory_consolidate,
    _referent_block,
    _segments_as_dicts,
)
from app.agent.loop.provider_view import _modalities_for_chain, _provider_chain_for
from app.agent.loop.tool_source import (  # noqa: F401 - 重导出保持 chat.* API
    _collect_llm_ext,
    _context_expand_flags,
    _images_enabled,
)
from app.agent.loop.turn_input import (
    get_or_create_session,
    normalize_event,
    record_user_turn,
)
from app.agent.loop.turn_metrics import (
    _history_meta_flags,
)
from app.agent.media.image import (
    IMAGE_LIKE_SEGMENTS,
    backfill_history_media,
    cache_enabled,
    inline_budget,
    max_image_bytes,
)
from app.agent.session.manager import SessionManager
from app.domain.modalities import supports_image

#: 思考吃满预算后追加的收敛提示（只在本轮确实想过、却一个正文字都没有时追加一次）。


#: 已经提醒过的 (bot_id, agent 值, 生效值)，避免每条消息刷同一条告警


async def prepare_prompt(runtime, event=None, ctx=None, *, session_mgr=None, turn=None, bot=None):
    """把一次请求归一化成 ``assembly.PromptRequest``。

    这是**唯一**的"取值"入口：模型参数、前置背景、去重、渲染、压缩、技能与工具、
    记忆检索、指代块、图片解析都在这里完成，之后的装配（顺序/清洗）交给
    ``assembly.PromptAssembler``。此前这些步骤在 4 条路径里各写一遍，导致
    "记忆检索用什么文本""去重比哪个字符串"这类分歧。

    Args:
        runtime: AgentRuntime。
        event: 触发事件。**可为 None**（主动消息 / 定时任务 / 模块自建循环）——
            此时必须显式给出 ``turn`` 与 ``bot``。
        ctx: ``LlmContext``（钩子状态与 ``ctx.user_text`` 的来源）。
        session_mgr: 会话管理器（缺省按 bot_id 取单例）。
        turn: 已归一化的 ``TurnInput``（给了就跳过事件解析）。
        bot: 无事件时的连接（前置背景与图片解析需要它）。

    Returns:
        ``(req, session, meta)``；``meta`` 供调用方取模型参数与工具集
        （``model`` / ``max_tokens`` / ``temperature`` / ``modalities`` /
        ``all_specs`` / ``tool_ctx`` / ``provider_chain``）。无法请求时全为 None。
    """
    config = runtime.config
    if turn is None:
        turn = normalize_event(config, event, ctx)
        if turn is None:
            return None, None, None
    else:
        # 无事件路径：event 只能从 ctx 里取（可能仍为 None）
        event = event if event is not None else getattr(ctx, "event", None)
    is_private = turn.is_private
    session_id = turn.session_id
    user_id = turn.user_id
    group_id = turn.group_id
    user_text = turn.user_text
    raw_user_text = turn.raw_user_text
    include_pre_history = turn.include_pre_history
    if bot is None:
        bot = getattr(event, "bot", None)

    session_mgr = session_mgr or SessionManager(str(runtime.bot_id))
    session = await get_or_create_session(session_mgr, turn, config, ctx)
    if session is None:
        return None, None, None

    # 用户发言入历史：只有真实事件才写（无事件路径没有"用户发言"这件事）
    if event is not None:
        await record_user_turn(
            runtime, event, turn, config, session_mgr, nickname=_event_nickname(event)
        )


    provider_chain = _provider_chain_for(runtime)
    modalities = _modalities_for_chain(provider_chain)
    history_rounds = config.get("history_rounds", 50)
    _meta_flags = _history_meta_flags(runtime)
    # 内联预算（单张 MB 上限、长边上限）：落盘上限与"能塞进请求体"的上限是两把尺子，
    # 10MB 原图 base64 后接近 13MB，多数网关直接 400，所以这里先缩再发。
    _inline_budget = inline_budget(config)
    # 历史图片回填：改造前存的是远端直链（会过期），只要链接还活着就趁现在落成本地。
    # 必须改**会话里的真实条目**（get_history 返回深拷贝，改它等于没改）；
    # 只在视觉模型上做，且有张数/耗时双闸，不能把一轮请求拖成十几秒。
    if _images_enabled(config) and cache_enabled(config) and supports_image(modalities):
        live_data = getattr(session, "data", None)
        live_entries = getattr(live_data, "history", None)
        if live_entries:
            try:
                await backfill_history_media(
                    live_entries,
                    bot_id=getattr(runtime, "bot_id", ""),
                    bot=bot,
                    max_bytes=max_image_bytes(config),
                    max_entries=int(config.get("image_max_count", 4) or 4),
                )
            except Exception as e:  # noqa: BLE001 —— 回填是增强，失败只降级
                logger.add_info(f"#{runtime.bot_id}").debug(f"历史图片回填失败（已忽略）: {e}")

    pre_history_text = await _fetch_pre_history(
        runtime, event, is_private=is_private, group_id=group_id, user_id=user_id,
        include_pre_history=include_pre_history, history_rounds=history_rounds,
        session_id=session_id, meta_flags=_meta_flags, bot=bot,
    )

    session_history_raw = session_mgr.get_history(session_id, limit=session_mgr.MAX_HISTORY_MESSAGES)
    # 防重复：history 尾部就是刚追加的当前用户消息 → 去掉。
    # 结构化条目存的是**正文**（不是 user_text），所以先按 message_id 比对（唯一锚点），
    # 拿不到 id 时退回旧的文本比对；否则当前这条会在历史里再出现一次
    # （带图的消息还会因此把同一张图传两遍）。
    if session_history_raw and session_history_raw[-1].get("role") == "user":
        tail = session_history_raw[-1]
        tail_mid = str(tail.get("message_id") or "")
        event_mid = str(getattr(event, "message_id", "") or "")
        if (event_mid and tail_mid == event_mid) or (not event_mid and tail.get("content") == user_text):
            session_history_raw = session_history_raw[:-1]
    # 渲染时把 message_id 贴回消息对象：群聊环境记录要靠它判断"这条已在会话历史里"，
    # 否则同一句话会在历史与背景里各出现一次（双源去重失效）。
    from app.agent.context.group import with_message_ids

    session_history = with_message_ids(
        session_history_raw,
        _format_session_history(
            session_history_raw, is_private,
            bot_id=getattr(runtime, "bot_id", ""), session_id=session_id,
            entry_media=_history_media_hook(
                session_history_raw,
                # 历史图片与"本轮图片"同一道门：开关 + 模型声明 image 模态
                include_images=_images_enabled(config) and supports_image(modalities),
                bot_id=getattr(runtime, "bot_id", ""),
                max_images=int(config.get("image_max_count", 4) or 4),
                max_bytes=max_image_bytes(config),
                max_mb=_inline_budget[0],
                max_edge=_inline_budget[1],
            ),
            **_meta_flags,
        ),
    )
    session_history = await maybe_compress_context(
        provider_chain, config, session_history, history_rounds
    )

    from app.agent.policy import policy_get as _policy_get

    schedule_enable = bool(_policy_get(runtime, "schedule", "schedule_enable", True))
    all_specs, skill_blocks, tool_ctx = await _collect_llm_ext(
        runtime, event, session_id, is_private, schedule_enable
    )
    # 发言通道（P2）：文本只当思考，真正的发言走 send_message / finish。
    # 只在「拟人化档位 + speech_via_tool + 流式」三者齐备时挂上；否则保持旧的文本即发言。
    # `speech_via_tool` 与 `assembly_profile` 归模块所有：
    # `llm_output` 接管输出槽时把视图挂 `runtime.output`，`llm_assembly` 挂
    # `runtime.assembly_options`；未接管时回退 agent 配置，行为不变。
    # 发言通道跨模块读键（assembly_profile 归 llm_assembly、speech_via_tool 归 llm_output），
    # 所以用链式视图；未接管任何模块时等价于 agent 配置。
    from app.agent.policy import policy_view

    policy_cfg = policy_view(runtime) or config
    speech = _build_speech(policy_cfg)
    if speech is not None:
        from app.agent.delivery.speech import build_speech_tools, persona_output_conflicts

        all_specs = list(all_specs or []) + build_speech_tools(speech)
        # 人格卡里若还留着「输出：以第一人称…纯文本」这类条款，会和发言通道打架
        # （实测：模型跟着人设的输出格式走，于是只写文本、整轮静默）。运行时不改用户文本，
        # 只提醒一次，把该挪的条款指出来。
        conflicts = persona_output_conflicts(config)
        warn_key = (getattr(runtime, "bot_id", 0), "persona_output")
        if conflicts and warn_key not in _WARNED_SPEECH_INTENT:
            _WARNED_SPEECH_INTENT.add(warn_key)
            logger.add_info(f"#{runtime.bot_id}").warning(
                "[LLM] 人格卡里有「输出格式」条款，会与发言通道（文本=思考）冲突，"
                f"建议移到行为层或删掉：{conflicts}"
            )
    elif _speech_intent(policy_cfg):
        # 只在第一次告警：配置矛盾是持久状态，每条消息刷一遍日志没有信息量
        warn_key = (getattr(runtime, "bot_id", 0), "speech_intent")
        if warn_key not in _WARNED_SPEECH_INTENT:
            _WARNED_SPEECH_INTENT.add(warn_key)
            logger.add_info(f"#{runtime.bot_id}").warning(
                "[LLM] 已开启 speech_via_tool 但当前不是拟人化档位或未开流式输出，"
                "按「文本即发言」处理（发言通道需要轮次边界才能 flush）"
            )
    # 群聊环境记录块：持续记录面（消息 + 表情/戳/撤回 + 我的动作），
    # 与上面的 pre_history（按需拉取在线历史）是两个来源，正文按 message_id 去重。
    from app.agent.group_log.context import build_context_text

    group_log_text = build_context_text(
        runtime,
        session_id,
        history=session_history,
        is_private=is_private,
        group_id=group_id,
        user_id=user_id,
    )
    # 工具取回内容的记账本（补全回写：请求收尾时按本轮 message_id 落进历史）
    from app.agent.session.enrich import ledger_for

    tool_ctx.extra["expansion_ledger"] = ledger_for(getattr(runtime, "bot_id", ""), session_id)
    tool_ctx.extra["trigger_message_id"] = str(getattr(event, "message_id", "") or "")

    _memory_autosave(runtime, session_id, user_id, raw_user_text or user_text)
    memory_text = await _memory_block(runtime, session_id, user_id, raw_user_text or user_text, bot)
    # 指代消解：焦点行 + 明确指向时的确定性预取（放在背景块之前，信息密度更高）
    referent_text = await _referent_block(tool_ctx.runtime, session_id, user_text, tool_ctx)

    # 模块贡献的额外上下文块（记忆/知识库/群记录等做成模块时的接入点）：
    # 按 order 收集，装配器把它们渲染在 group_log 之后、history 之前。
    extra_blocks = await _collect_extra_blocks(
        runtime,
        session_id=session_id,
        is_private=is_private,
        user_id=user_id,
        group_id=group_id,
        user_text=user_text,
        history=session_history,
    )

    # 图片：复用上面**已经落盘**的那批段（一张图只下载一次），渲染成 base64 块；
    # 是否真正传给模型由 assembly 的模态门控决定。
    # 无事件路径没有"本轮图片段"，跳过（等价于"没有图片"分支）。
    user_images: list[dict] = []
    if _images_enabled(config) and event is not None:
        try:
            from app.agent.media.image import resolve_images

            event_segments = _segments_as_dicts(getattr(event, "message", None))
            image_segments = [
                seg for seg in event_segments if seg.get("type") in IMAGE_LIKE_SEGMENTS
            ]
            if image_segments:
                user_images = await resolve_images(
                    event,
                    bot=bot,
                    bot_id=getattr(runtime, "bot_id", ""),
                    max_images=int(config.get("image_max_count", 4) or 4),
                    max_bytes=max_image_bytes(config),
                    config=config,
                    segments=image_segments,
                )
        except Exception as e:  # noqa: BLE001 —— 图片解析失败必须降级为纯文本
            logger.add_info(f"#{runtime.bot_id}").warning(f"图片解析失败，按纯文本处理: {e}")

    req = PromptRequest(
        runtime=runtime,
        event=event,
        ctx=ctx,
        config=config,
        session_id=session_id,
        is_private=is_private,
        user_id=user_id,
        group_id=group_id,
        kind="chat",
        user_text=user_text,
        raw_user_text=raw_user_text,
        memory_source_text=raw_user_text or user_text,
        history=session_history,
        pre_history_text=pre_history_text,
        group_log_text=group_log_text,
        referent_text=referent_text,
        skill_blocks=skill_blocks,
        memory_text=memory_text,
        available_tools={spec.name for spec in (all_specs or [])},
        modalities=modalities,
        schedule_enable=schedule_enable,
        user_images=user_images,
        extra_blocks=extra_blocks,
    )
    meta = {
        "model": config.get("model", "deepseek-chat"),
        "max_tokens": config.get("max_tokens", 1024),
        "temperature": config.get("temperature", 0.7),
        "history_rounds": history_rounds,
        "provider_chain": provider_chain,
        "modalities": modalities,
        "all_specs": all_specs,
        "tool_ctx": tool_ctx,
        "speech": speech,
        "session": session,
        "session_mgr": session_mgr,
        "is_private": is_private,
        "user_id": user_id,
        "group_id": group_id,
        "session_id": session_id,
        "bot": bot,
    }
    return req, session, meta


def _context_policy(runtime):
    """上下文增强策略配置（`llm_context` 模块接管时用它的视图）。"""
    from app.agent.policy import policy_config

    return policy_config(runtime, "context")


async def _collect_extra_blocks(
    runtime, *, session_id, is_private, user_id, group_id, user_text, history
) -> list[tuple[str, str]]:
    """收集模块注册的额外上下文块；没有注册者时返回空列表（零开销）。"""
    try:
        from app.agent.loop.providers import TurnScope, collect_context_blocks

        scope = TurnScope(
            runtime=runtime,
            config=getattr(runtime, "config", None),
            session_id=session_id,
            is_private=is_private,
            user_id=str(user_id or ""),
            group_id=group_id,
            user_text=user_text,
            history=tuple(history or ()),
        )
        return await collect_context_blocks(runtime, scope)
    except Exception as e:  # noqa: BLE001 —— 贡献者收集失败不该毁掉整轮
        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(
            f"[Provider] 额外上下文块收集失败（已忽略）: {e}"
        )
        return []


async def _fetch_pre_history(
    runtime, event, *, is_private, group_id, user_id, include_pre_history,
    history_rounds, session_id, meta_flags, bot=None,
) -> str:
    """前置背景：群聊在线记录 / 私聊近期记录（与既有触发条件完全一致）。

    ``event`` 可以为 None（主动消息 / 定时任务）：此时用 ``bot`` 代替 ``event.bot``，
    群号/QQ 由调用方显式给出，取不到连接就直接返回空串（不发请求）。
    """
    bot = bot if bot is not None else getattr(event, "bot", None)
    if group_id:
        if not include_pre_history or bot is None:
            return ""
        return await _build_group_pre_history(
            event, group_id, count=history_rounds, **meta_flags,
            **_context_expand_flags(_context_policy(runtime)),
            bot_id=getattr(event, "bot_id", "") or getattr(bot, "bot_id", "") or "",
            session_id=session_id,
            bot=bot,
        )
    if is_private and include_pre_history in ("history", "load") and bot is not None:
        text = await fetch_private_online_history(
            bot,
            user_id,
            count=history_rounds,
            self_ids={
                str(getattr(event, "self_id", "") or ""),
                str(getattr(event, "bot_id", "") or getattr(bot, "bot_id", "") or ""),
            },
            bot_id=getattr(event, "bot_id", "") or getattr(bot, "bot_id", "") or "",
            session_id=session_id,
        )
        if text and include_pre_history == "history":
            text = f"近期聊天记录:\n{text}"
        return text
    return ""
