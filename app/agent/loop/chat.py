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
import json
import time
from typing import Any

from app.agent import logger
from app.agent.assembly import assembler as assembly
from app.agent.assembly.assembler import PromptRequest
from app.agent.assembly.compress import maybe_compress_context
from app.agent.assembly.token_budget import estimate_tokens
from app.agent.commands import (
    build_help_nodes,
    build_help_text,
    chunk_text,
    is_help_action,
    parse_action,
)
from app.agent.context.group import (
    build_group_env_text,
    fetch_group_online_history,
    fetch_private_online_history,
    format_history_for_llm,
)
from app.agent.delivery.splitter import split_sentences, strip_stream_artifacts
from app.agent.loop.tool_loop import normalize_and_execute_tool_calls
from app.agent.media.image import (
    IMAGE_LIKE_SEGMENTS,
    backfill_history_media,
    cache_enabled,
    collect_image_segments,
    inline_budget,
    max_image_bytes,
    persist_images,
)
from app.agent.persona.tags import maybe_strip_parentheses, strip_all_tags
from app.agent.proactivity.trigger import extract_text
from app.agent.providers import SERVED_MODEL_KEY, log_token_usage
from app.agent.providers.invoker import resolve_invoker
from app.agent.providers.modalities import normalize_modalities, supports_image, supports_tool_use
from app.agent.providers.policy import TurnBudget, retry_profile_of
from app.agent.session.manager import SessionManager
from app.agent.tools.spec import ToolContext, build_tools, make_executor


def _event_nickname(event) -> str:
    """取发送者群名片/昵称，用于会话历史保留发送者身份。"""
    user = getattr(event, "user", None)
    if user is None:
        return ""
    return getattr(user, "card", "") or getattr(user, "nickname", "") or ""


def _served_model_label(usage_sink: dict | None, fallback: Any) -> str:
    """本次请求实际服务的模型名（回退后由 provider 层写进 usage_sink）。

    取不到（调用方被插件整体替换、没走 provider 层）时退回配置里的主模型名。
    """
    served = str((usage_sink or {}).get(SERVED_MODEL_KEY) or "").strip()
    return served or str(fallback or "").strip()


def _segments_as_dicts(message: Any) -> list[dict]:
    """把事件里的消息段统一成 ``[{"type": ..., "data": {...}}]``（存历史用）。"""
    segments: list[dict] = []
    for seg in message or []:
        if isinstance(seg, dict):
            segments.append({"type": str(seg.get("type", "")), "data": dict(seg.get("data", {}) or {})})
            continue
        stype = getattr(seg, "type", "")
        if stype:
            segments.append({"type": str(stype), "data": dict(getattr(seg, "data", {}) or {})})
    return segments


def _images_enabled(config) -> bool:
    """图片是否随请求传给模型（默认开；仅对声明了 image 模态的模型生效）。"""
    try:
        return bool(config.get("image_understanding_enable", True))
    except Exception:  # noqa: BLE001
        return True


def _commit_history_enrichment(runtime, session_id: str, ctx=None) -> int:
    """请求收尾：把本轮工具取回的内容写进「补全登记」（失败不影响回复）。"""
    try:
        from app.agent.session.enrich import commit

        trigger = ""
        if ctx is not None:
            trigger = str(getattr(ctx.event, "message_id", "") or "")
        return commit(getattr(runtime, "bot_id", ""), session_id, trigger_message_id=trigger)
    except Exception as e:  # noqa: BLE001
        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(
            f"[HistoryEnrich] 回写失败（已忽略）: {e}"
        )
        return 0


def _append_round_images(messages: list[dict], tool_ctx, modalities) -> bool:
    """把 ``expand_image`` 取回的图片追加为一条 user 消息（仅视觉模型）。

    实现在 ``context_tools.append_round_images``（主动消息/定时任务的工具轮也要用，
    而它们不能 import 本模块）。见该函数的说明与时机问题。
    """
    from app.agent.tools.context_tools import append_round_images

    return append_round_images(messages, tool_ctx, modalities)


def _format_session_history(
    history: list[dict],
    is_private: bool,
    *,
    normalize_enhanced: bool = False,
    mask_nickname: bool = False,
    bot_id: Any = "",
    session_id: Any = "",
    entry_media=None,
) -> list[dict]:
    """兼容包装：委托给 group_context 的共享渲染函数。

    传入 ``bot_id`` / ``session_id`` 时接入补全登记（取回过的内容留在历史里）。
    ``entry_media`` 见 :func:`_history_media_hook`。
    """
    return format_history_for_llm(
        history,
        is_private=is_private,
        normalize_enhanced=normalize_enhanced,
        mask_nickname=mask_nickname,
        bot_id=bot_id,
        session_id=session_id,
        entry_media=entry_media,
    )


def _history_media_hook(
    entries: list,
    *,
    include_images: bool,
    bot_id: Any = "",
    max_images: int = 4,
    max_bytes: int | None = None,
    max_mb: float | None = None,
    max_edge: int | None = None,
):
    """会话历史图片的渲染钩子（配合 ``history_model.render_history`` 的 ``entry_media``）。

    - **文本模型**：不动正文（渲染器已给出 ``[图片#<消息id>]`` 占位），
      模型可 ``expand_image`` 按需取回；
    - **视觉模型**：把历史里的**本地指向**（``data.media`` 的 sha）内联成 ``image_url`` 块，
      模型下一轮仍看得见上一轮发的图。**远端直链永远不进请求**——QQ 图片链接会过期，
      过期后不是"少一张图"，而是网关拉不到 → 整条请求 400；文件已丢时自然退化为占位。

    内联预算按**由新到旧**分配：最多 ``max_images`` 张（与单轮图片上限同一把尺子），
    避免长会话把上下文撑爆。
    """
    from app.agent.media.image import DEFAULT_MAX_IMAGE_BYTES, history_image_parts, history_image_segments

    if max_bytes is None:
        max_bytes = DEFAULT_MAX_IMAGE_BYTES

    allowed: set[int] = set()
    if include_images:
        left = max(1, int(max_images or 1))
        for entry in reversed(list(entries or [])):
            count = len(history_image_segments(entry))
            if count and left > 0:
                allowed.add(id(entry))
                left -= count

    def hook(entry: Any, *, text: str = ""):
        if not history_image_segments(entry) or id(entry) not in allowed:
            return None
        # 正文保持渲染器结果（占位/正文），只把图片块接在后面
        return None, history_image_parts(
            entry,
            bot_id=bot_id,
            max_images=max_images,
            max_bytes=max_bytes,
            max_mb=max_mb,
            max_edge=max_edge,
        )

    return hook


async def _build_group_pre_history(
    event,
    group_id: str,
    count: int,
    *,
    normalize_enhanced: bool = False,
    mask_nickname: bool = False,
    resolve_at: bool = True,
    mark_unresolved: bool = False,
    bot_id: Any = "",
    session_id: Any = "",
) -> str:
    """根据 include_pre_history 配置，拉取并组装群聊环境背景块。

    不拉取在线历史时返回空字符串；本函数不改变“非 @ 不入会话历史”的策略。

    ``resolve_at`` 会把背景块里的 ``@123`` 预展开成 ``@三哥(123)``（复用
    ``app.agent.context.nicknames`` 与其缓存），``mark_unresolved`` 让取不到的部分输出
    ``【未展开:...】`` 标记——背景块是"群里其他人聊了什么"的唯一可见窗口，
    此前这里只有裸 ``@123``，模型既不知道是谁、也没有"这里缺东西"的感知。
    ``bot_id``/``session_id`` 用于查"已展开"登记：本会话取回过的 id 渲染成
    ``【已展开:... → 摘要】``，不再重复标记为缺失。
    """
    history_text = await fetch_group_online_history(
        event.bot,
        group_id,
        count=count,
        self_ids={str(event.self_id), str(getattr(event, "bot_id", "") or "")},
        normalize_enhanced=normalize_enhanced,
        mask_nickname=mask_nickname,
        resolve_at=resolve_at,
        mark_unresolved=mark_unresolved,
        bot_id=bot_id or getattr(event, "bot_id", "") or "",
        session_id=session_id,
    )
    if not history_text:
        return ""
    return build_group_env_text(
        group_id=group_id,
        group_name=getattr(event.group, "group_name", "") or "",
        history_text=history_text,
    )


async def _referent_block(runtime, session_id: str, user_text: str, ctx) -> str:
    """指代消解块：「当前对话焦点 + 指代解析 + 已预取内容」（见 referent.py）。

    **用用户原始文本判定**（不含 ``发送者：``/``引用了：`` 之类的元信息前缀）——否则
    元信息里的 QQ 号会被误当成用户给出的消息 id。失败不影响回复主流程。

    有 event 时**只认原始文本**（哪怕为空）：图片/语音/表情这类没有文字的回合，
    原始文本就是空串，此时**不能**回退到带 ``(QQ: …)`` 的 ``user_text``——那串数字会被
    指代判定当成"用户给出的消息 id"，进而 ``get_msg(QQ号)`` 空转一次（真实日志
    private_1901691195 / 2026-10-18 22:49：``get_msg 未取到消息 1901691195: 100 None``）。
    没有正文就没有可判定的指代；焦点行仍由 ``build_block`` 给出。
    """
    try:
        from app.agent.assembly.enhance import _raw_user_text
        from app.agent.context.referent import build_block

        event = getattr(ctx, "event", None)
        if event is not None:
            return await build_block(runtime, session_id, _raw_user_text(event), ctx)
        return await build_block(runtime, session_id, user_text, ctx)
    except Exception as e:
        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(
            f"[Referent] 构建指代块失败（已忽略）: {e}"
        )
        return ""


def _context_expand_flags(config) -> dict:
    """渲染层「骨架预展开」开关。

    - ``resolve_at``：复用既有的 ``fetch_at_nickname``（@ 昵称反查总开关）；
    - ``mark_unresolved``：``context_expand_enable``，控制 ``【未展开:...】`` 标记
      （与按需展开能力同开同关：标记必须可被解决，否则只会诱导模型空转）。
    """
    flags = {"resolve_at": True, "mark_unresolved": True}
    for key, name in (("fetch_at_nickname", "resolve_at"), ("context_expand_enable", "mark_unresolved")):
        try:
            flags[name] = bool(config.get(key, True))
        except Exception as e:
            # 配置读取异常不能影响回复主流程，退回默认（开启）并留痕
            logger.add_info("Chat").debug(f"[Context] 读取配置 {key} 失败，按默认开启处理: {e}")
    return flags


async def _collect_llm_ext(
    runtime,
    event,
    session_id: str,
    is_private: bool,
    schedule_enable: bool,
    *,
    bot=None,
    user_id=None,
    group_id=None,
):
    """收集本次请求的模块工具 + 技能 + 知识库 + MCP + ToolContext。

    返回 (specs, skill_blocks, ctx)；specs 已包含内置 schedule_task。

    ``event`` 允许为 None（主动消息 / 定时任务路径没有触发事件）。此时会话目标必须由调用方
    用 ``bot`` / ``user_id`` / ``group_id`` 显式给出——会话类工具（get_current_session /
    get_chat_history / expand_context）本来就是从 ToolContext 推导当前会话，不依赖事件。
    """
    if bot is None and event is not None:
        bot = getattr(event, "bot", None)
    if user_id is None and event is not None:
        user_id = getattr(event, "user_id", None)
    if group_id is None and event is not None:
        group = getattr(event, "group", None)
        group_id = getattr(group, "group_id", None) if group is not None else None

    specs = []
    if schedule_enable:
        from app.agent.proactivity.scheduler import build_schedule_tool

        specs.append(build_schedule_tool(runtime, session_id, is_private))

    # 进入新一轮：焦点表按轮次衰减显著性（见 docs/referent-resolution-design.md）
    from app.agent.context import focus

    focus.begin_turn(getattr(runtime, "bot_id", ""), session_id)

    if getattr(runtime, "llm_tools", None) is not None:
        specs.extend(runtime.llm_tools.enabled_specs())

    skill_blocks = []
    if getattr(runtime, "skills", None) is not None:
        skill_blocks = runtime.skills.prompt_blocks()

    ctx = ToolContext(
        module=runtime,
        bot=bot,
        session_id=session_id,
        event=event,
        runtime=runtime,
        user_id=user_id,
        group_id=group_id,
    )

    # 系统级会话上下文工具（不展示在 OneBot 前端清单）
    from app.agent.tools.session_tools import build_session_tools

    specs.extend(build_session_tools(runtime, ctx))

    # 按需展开上下文（@ 对象 / 被引用消息 / 合并转发）——与渲染层的未展开标记同开同关：
    # 标记必须可被解决，否则只会诱导模型空转或谎称无法回答。
    if bool(runtime.config.get("context_expand_enable", True)):
        from app.agent.tools.context_tools import build_context_tools

        specs.extend(build_context_tools(runtime, ctx))

    # Tavily 联网搜索（系统级工具，不进入 OneBot 前端清单）
    if bool(runtime.config.get("tavily_enable", False)):
        tavily_api_key = str(runtime.config.get("tavily_api_key", "") or "").strip()
        if tavily_api_key:
            from app.agent.tools.web_search import build_tavily_tool

            specs.append(build_tavily_tool(runtime))

    # 长期记忆原生工具：memory_save / recall / delete / correct / deny
    memory = getattr(runtime, "memory", None)
    if memory is not None and memory.enabled():
        from app.agent.memory import build_memory_tools

        memory_user_id = str(user_id or "")
        specs.extend(build_memory_tools(runtime, session_id, memory_user_id, is_private))

    # 知识库原生工具
    knowledge = getattr(runtime, "knowledge", None)
    if knowledge is not None and knowledge.enabled():
        from app.agent.knowledge import build_knowledge_tools

        specs.extend(build_knowledge_tools(runtime))

    # MCP 工具（异步连接后暴露）
    mcp = getattr(runtime, "mcp_manager", None)
    if mcp is not None and mcp.enabled():
        await mcp.ensure_ready()
        specs.extend(mcp.build_tools())

    # OneBot 通用工具（数据驱动清单，按当前会话作用域过滤）
    if bool(runtime.config.get("onebot_tools_enable", False)):
        from app.agent.tools.onebot import build_onebot_tools

        specs.extend(build_onebot_tools(runtime, ctx))

    # 统一按四类工具的用户开关过滤：
    # - 系统工具由 system_tools_enabled + 前置功能开关共同决定
    # - 模块工具 / MCP 工具由各自 enabled map 单独控制
    # - OneBot 工具已在上方由 security.resolve_tool_policy 过滤，这里直接放行
    from app.agent.tools.policy import is_system_tool_enabled

    system_enabled = runtime.config.get("system_tools_enabled", {}) or {}
    module_enabled = runtime.config.get("module_tools_enabled", {}) or {}
    mcp_enabled = runtime.config.get("mcp_tools_enabled", {}) or {}
    filtered: list = []
    for spec in specs:
        if spec.source == "onebot":
            filtered.append(spec)
        elif spec.source == "mcp":
            if not (isinstance(mcp_enabled, dict) and not mcp_enabled.get(spec.name, True)):
                filtered.append(spec)
        elif spec.source == "module":
            if not (isinstance(module_enabled, dict) and not module_enabled.get(spec.name, True)):
                filtered.append(spec)
        elif spec.source == "system":
            if is_system_tool_enabled(runtime, spec):
                filtered.append(spec)
        else:
            filtered.append(spec)

    return filtered, skill_blocks, ctx


async def _memory_block(runtime, session_id: str, user_id: Any, user_text: str, bot) -> str:
    """召回长期记忆块；未启用或异常时返回空串。"""
    memory = getattr(runtime, "memory", None)
    if memory is None or not memory.enabled():
        return ""
    try:
        return await memory.recall_block_async(session_id, user_id, user_text, bot=bot)
    except Exception as e:
        # 记忆召回属增强能力：失败不阻断回复，但需留痕，否则表现为"记忆突然失效"
        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(f"[Memory] 召回失败（已忽略）: {e}")
        return ""


def _memory_autosave(runtime, session_id: str, user_id: Any, text: str) -> None:
    """确定性“记住…”兜底写入；未启用或异常时静默跳过。"""
    memory = getattr(runtime, "memory", None)
    if memory is None or not memory.enabled():
        return
    try:
        memory.autosave(session_id, user_id, text)
    except Exception as e:
        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(f"[Memory] 兜底写入失败（已忽略）: {e}")


def _memory_consolidate(runtime, session_id: str, is_private: bool, session_mgr) -> None:
    """回复后触发限频隐式蒸馏；未启用或异常时静默跳过。"""
    memory = getattr(runtime, "memory", None)
    if memory is None or not memory.enabled():
        return
    try:
        history = session_mgr.get_history(session_id, limit=20)
        memory.maybe_consolidate(session_id, not is_private, history, source="chat")
    except Exception as e:
        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(f"[Memory] 隐式蒸馏触发失败（已忽略）: {e}")





DEFAULT_MAX_TOOL_ROUNDS = 5


def _max_tool_rounds(config, default: int = DEFAULT_MAX_TOOL_ROUNDS) -> int:
    """工具循环轮数上限（配置 ``max_tool_rounds``），流式与非流式共用同一值。

    以前非流式走 Provider 默认值、流式在 ``stream_response`` 里硬编码 ``range(5)``，
    两处不一致且不可配置。而“展开缺失的聊天环境 → 再判断是否还缺 → 再展开”本身就是
    多段链条，轮数被硬切就表现为模型“半途而废”直接作答。
    """
    try:
        value = int(config.get("max_tool_rounds", default) or default)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, 20))


DEFAULT_EMPTY_REPLY_RETRIES = 1


def _max_empty_retries(config, default: int = DEFAULT_EMPTY_REPLY_RETRIES) -> int:
    """空回复重试次数（配置 ``empty_reply_retries``），流式与非流式共用。

    偶现问题：请求成功结束却既无文本也无工具调用（网关提前断流 / 只回思考内容），
    表现就是用户收到兜底文本「抱歉，我暂时无法回答」。这里给一次确定性重试的机会，
    上限 3 次，避免真正故障时空跑 LLM 请求。0 = 关闭。
    """
    try:
        value = int(config.get("empty_reply_retries", default))
    except (TypeError, ValueError):
        return default
    return max(0, min(value, 3))


def _usage_snapshot(usage: dict | None) -> dict:
    """usage 累计快照（用来算"这一轮请求自己花了多少"）。"""
    if not usage:
        return {}
    return {k: v for k, v in usage.items() if isinstance(v, (int, float))}


def _usage_delta(usage: dict | None, before: dict, key: str) -> int:
    """本次请求的单项 token 增量（上游没报就返回 0，绝不编）。"""
    if not usage:
        return 0
    try:
        return max(0, int(usage.get(key, 0) or 0) - int(before.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _log_request_line(
    runtime,
    *,
    session_id: str,
    round_index: int,
    served_model: str,
    max_tokens: int,
    max_tokens_src: str,
    input_tokens: int,
    output_tokens: int,
    input_estimate: int,
    finish_reason: str,
    text_chars: int,
    reasoning_chars: int,
    tools: int,
    error: str,
    budget: TurnBudget,
) -> None:
    """**每一次**真实请求打一行结构化记录（流式路径）。

    为什么值得单独一行：出问题时大家看的就是日志，而此前一次请求的信息散在四五处
    （模型名在一行、token 在另一行、max_tokens 只在开头可能出现、重试次数只体现在
    多条"重试"警告里）。这里一行给全，并且用固定字段名 ``key=value`` 便于 grep：

        model= in= out= max_tokens=(来源) finish= text= reasoning= tools= retry=n/m

    上游没回报 token 时写 ``in=?`` ``out=?`` 而不是 0 —— 0 会被误读成"没花 token"。
    """
    from app.agent.providers.base import has_token_usage

    reported = has_token_usage(
        {"prompt_tokens": input_tokens, "completion_tokens": output_tokens}
    )
    in_txt = str(input_tokens) if reported else f"?≈{input_estimate}"
    out_txt = str(output_tokens) if reported else "?"
    parts = [
        f"model={served_model or '?'}",
        f"round={round_index + 1}",
        f"in={in_txt}",
        f"out={out_txt}",
        f"max_tokens={max_tokens}({max_tokens_src})",
        f"finish={finish_reason or '-'}",
        f"text={text_chars}字",
    ]
    if reasoning_chars:
        parts.append(f"reasoning={reasoning_chars}字")
    if tools:
        parts.append(f"tools={tools}")
    parts.append(f"budget={budget.requests}/{budget.max_requests}")
    log = logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}")
    if error:
        parts.append(f"error={error}")
        log.warning("[LLM] 请求 " + " ".join(parts) + f" -> {session_id}")
    else:
        log.info("[LLM] 请求 " + " ".join(parts) + f" -> {session_id}")


def _turn_budget(config) -> TurnBudget:
    """本轮请求的总预算闸门（请求次数 + 累计输入 token）。

    为什么需要它：重试散在三层（HTTP 适配器 ``retry_attempts``、回退层空回复重试、
    调用方收敛重试），每层单独看都"只多一次"，乘起来却是十几次，而且**每次都重发
    全量上下文**（线上为此把 16.7 万 token 发了三遍还没出头）。这里把总账收在一处：
    档位决定最多几次请求、累计输入 token 到线就停，停了如实写日志。

    注意它只是**闸门**，不改变重试策略本身（谁该重试仍由失败分类决定）。
    """
    try:
        return TurnBudget.from_config(config)
    except Exception as e:  # noqa: BLE001 —— 预算读不出来不该拖垮整轮回复
        logger.add_info("LLM").warning(f"[Budget] 预算配置解析失败，改用默认档位: {e}")
        return TurnBudget(profile=retry_profile_of(None))


def _input_tokens_estimate(messages: list[dict]) -> int:
    """这份上下文大概多少输入 token（用于预算累计；上游回了真实 usage 就以真实值为准）。"""
    try:
        return int(estimate_tokens(messages))
    except Exception:  # noqa: BLE001 —— 估算失败就当 0，闸门退化为只按次数算
        return 0


DEFAULT_REASONING_OVERFLOW_RETRIES = 1

#: 思考吃满预算后追加的收敛提示（只在本轮确实想过、却一个正文字都没有时追加一次）。
REASONING_OVERFLOW_NUDGE = (
    "【系统提醒】上一轮你的思考把输出预算用满了，正文还没开始就被截断，"
    "对方什么都没收到。这一轮不要复述思考过程，直接给最终回答；"
    "如果要看图，就只说你看到的关键内容，别长篇推理。"
)


def _reasoning_overflow_retries(config, default: int = DEFAULT_REASONING_OVERFLOW_RETRIES) -> int:
    """思考吃满 ``max_tokens`` 时的收敛重试次数（配置 ``reasoning_overflow_retries``）。

    思考模型（deepseek-v4-flash / reasoner 系）把全部输出预算花在 ``reasoning_content``
    里时，正文一个字节都没有。同一条 prompt 原样重试必然同样打满（纯烧钱），所以改成
    追加一次「直接作答」提醒再要一枪。0 = 关闭。
    """
    try:
        value = int(config.get("reasoning_overflow_retries", default))
    except (TypeError, ValueError):
        return default
    return max(0, min(value, 2))


def _effective_max_tokens(provider_chain, fallback) -> tuple[int, str]:
    """本次请求**真正生效**的 ``max_tokens``，以及它来自哪一层配置。

    取值优先级（高 → 低）：

    1. ``provider_models.<id>.config_json.max_tokens``（模型实例，WebUI「Provider 预设 → 模型」页）
    2. ``provider_presets.<id>.config_json.max_tokens``（连接预设，同一页的预设表单）
    3. Agent 配置 ``max_tokens``（WebUI「Agent → 最大输出 Tokens」）

    前两层在 ``AgentRuntime._config_for_model_id`` 里合并进链上每个 cfg，而
    ``chat_with_fallback`` 用 ``cfg.get("max_tokens", max_tokens)`` 取值 —— 所以
    **链上配置赢**，Agent 页那个数字只在链上没写时才生效。

    来源由 ``_config_for_model_id`` 写进 ``_max_tokens_origin``（合并后的 cfg 恒带该键，
    光看值是分辨不出三层的）；拿不到标记时才退回「链上 = provider、否则 agent」的粗判。
    """
    for cfg in provider_chain or []:
        value = (cfg or {}).get("max_tokens")
        if value in (None, ""):
            continue
        try:
            origin = str((cfg or {}).get("_max_tokens_origin") or "provider 模型/预设")
            return int(value), origin
        except (TypeError, ValueError):
            break
    try:
        return int(fallback or 0), "agent 配置"
    except (TypeError, ValueError):
        return 0, "agent 配置"


#: 已经提醒过的 (bot_id, agent 值, 生效值)，避免每条消息刷同一条告警
_MAX_TOKENS_WARNED: set[tuple] = set()


def _max_tokens_override_note(runtime, effective: int, origin: str) -> str:
    """Agent 页显式设了 max_tokens 却被 provider 层覆盖时，给一句可操作的提示。

    这是「配置假象」的显式化：用户以为改的是 Agent 页，实际生效的是「Provider 预设 →
    模型」里那个值，而此前没有任何地方告诉他。只在**用户显式设过**且值确实不同时提示，
    否则只是默认值差异，属于噪音。
    """
    if origin == "agent 配置":
        return ""
    config = getattr(runtime, "config", None)
    checker = getattr(config, "has_explicit", None)
    if not callable(checker):
        return ""
    try:
        if not checker("max_tokens"):
            return ""
        agent_value = int(config.get("max_tokens", 0) or 0)
    except Exception:  # noqa: BLE001 —— 诊断辅助，失败不影响主流程
        return ""
    if not agent_value or agent_value == effective:
        return ""
    marker = (getattr(runtime, "bot_id", ""), agent_value, effective, origin)
    if marker in _MAX_TOKENS_WARNED:
        return ""
    _MAX_TOKENS_WARNED.add(marker)
    return (
        f"Agent 页的 max_tokens={agent_value} 未生效，本次实际用 {effective}"
        f"（来自{origin}）—— 要改「Provider 预设 → 模型」里该模型的「最大输出 Tokens」"
    )


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


def _history_meta_flags(runtime) -> dict:
    """计算历史渲染标志：是否归一化单行、是否脱敏昵称。

    mask_nickname 恒为 True：句子型/超长昵称一律脱敏，避免昵称内容泄漏进 LLM 上下文。
    """
    return {
        "normalize_enhanced": bool(runtime.config.get("experimental_long_term_memory", False)),
        "mask_nickname": True,
    }


def _log_debug_prompt(runtime, session_id: str, messages: list[dict], debug_enabled: bool = False) -> None:
    """调试开关开启时，打印本轮完整 prompt。"""
    if not debug_enabled:
        return
    try:
        text = json.dumps(messages, ensure_ascii=False, indent=2)
    except Exception:
        text = str(messages)
    logger.add_info(f"#{runtime.bot_id}").info(f"[Prompt] {session_id}\n{text}")


def _provider_config_for(module) -> dict:
    """取传给 Provider 的配置：优先走运行时合并（Provider 预设），兼容旧对象。"""
    if hasattr(module, "provider_config"):
        return module.provider_config()
    config = getattr(module, "config", None)
    if hasattr(config, "raw_config"):
        return dict(config.raw_config)
    return dict(config or {})


def _provider_chain_for(module) -> list[dict]:
    """取按顺序尝试的 provider 配置链（主模型 + 回退模型）；旧对象退化为单条配置。"""
    if hasattr(module, "provider_chain"):
        return module.provider_chain()
    return [_provider_config_for(module)]


def _modalities_for_chain(provider_chain: list[dict]) -> list[str] | None:
    """取主模型声明的模态能力；未配置时返回 None（兼容旧行为=全部放行）。"""
    if not provider_chain:
        return None
    return normalize_modalities((provider_chain[0] or {}).get("modalities"))


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


# ==================== 发言通道（文本=思考，发言=send_message） ====================

#: 「配置想开发言通道但没生效」的告警去重（bot_id 集合）：持久性配置矛盾不该每条刷日志
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


def _speech_intent(config) -> bool:
    """配置**想**开发言通道吗（不看流式这一条）——用于"配置了但没生效"的告警。"""
    from app.agent.assembly.prompt import is_qq_agent

    try:
        return bool(config.get("speech_via_tool", False)) and is_qq_agent(config)
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
        logger.add_info(f"#{runtime.bot_id}").warning(f"[LLM] 强制发言补救轮失败（按静默处理）: {e}")
    if speech.spoken == 0 and speech.queued == 0:
        logger.add_info(f"#{runtime.bot_id}").warning(
            "[LLM] 被直接点名但补一轮后仍未开口（模型可能没调用 send_message）"
        )


def _record_stream_telemetry(
    runtime,
    session_id: str,
    provider_chain: list[dict],
    model: str,
    messages: list[dict],
    full_text_parts: list[str],
    tool_results: list[dict],
    stream_start: float,
    success: bool,
    error_text: str = "",
    usage: dict | None = None,
) -> None:
    """流式生成的统一遥测落点。"""
    telemetry = getattr(runtime, "telemetry", None)
    if telemetry is None:
        return
    full_text = "".join(full_text_parts)
    first_cfg = provider_chain[0] if provider_chain else {}
    tokens = usage or {}
    telemetry.record_call_simple(
        bot_id=runtime.bot_id,
        session_id=session_id,
        provider=str(first_cfg.get("provider", "openai") or "openai"),
        # 实际服务者（回退后不是 chain[0]）：与 token 消耗那一行同源，避免遥测也记错模型
        model=_served_model_label(tokens, first_cfg.get("model", model)),
        stream=True,
        success=success,
        latency_ms=(time.monotonic() - stream_start) * 1000,
        input_tokens=int(tokens.get("prompt_tokens") or tokens.get("input_tokens") or 0),
        output_tokens=int(tokens.get("completion_tokens") or tokens.get("output_tokens") or 0),
        message_count=len(messages),
        tool_calls=len(tool_results),
        characters=len(full_text),
        error=error_text,
    )


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


async def prepare_prompt(runtime, event, ctx=None, *, session_mgr=None):
    """把一个事件（或定时/主动请求）归一化成 ``assembly.PromptRequest``。

    这是**唯一**的"取值"入口：模型参数、前置背景、去重、渲染、压缩、技能与工具、
    记忆检索、指代块、图片解析都在这里完成，之后的装配（顺序/清洗）交给
    ``assembly.PromptAssembler``。此前这些步骤在 4 条路径里各写一遍，导致
    "记忆检索用什么文本""去重比哪个字符串"这类分歧。

    返回 ``(req, session, meta)``；``meta`` 供调用方取模型参数与工具集
    （``model`` / ``max_tokens`` / ``temperature`` / ``modalities`` /
    ``all_specs`` / ``tool_ctx`` / ``provider_chain``）。
    """
    config = runtime.config
    message_type = getattr(event, "message_type", "")
    if message_type not in ("group", "private"):
        return None, None, None

    if message_type == "private":
        is_private = True
        session_id = f"private_{event.user_id}"
        user_id = str(event.user_id)
        group_id = None
        include_pre_history = config.get("include_private_pre_history", "default")
    else:
        is_private = False
        session_id = f"group_{event.group.group_id}"
        user_id = str(event.user_id)
        group_id = str(event.group.group_id)
        include_pre_history = config.get("include_pre_history", False)

    if ctx is not None and ctx.session_id:
        session_id = ctx.session_id

    # 用户原始正文（未包裹元信息）：记忆检索与意图判定都用它，避免元信息里的
    # QQ 号被当成消息 id、昵称污染检索。事件可能没有 message（主动/测试替身）。
    from app.agent.assembly.enhance import _raw_user_text

    segment_text = _raw_user_text(event).strip()
    raw_user_text = ""
    if ctx is not None:
        info = ctx.state.get("user_context") or {}
        raw_user_text = (info.get("sent_text") or "").strip()
    if not raw_user_text:
        raw_user_text = segment_text

    # 本轮 user 内容：流水线已注入元信息时用注入后的文本，否则用原始正文
    if ctx is not None and ctx.user_text:
        user_text = ctx.user_text
    else:
        user_text = raw_user_text
    if not user_text.strip() and not collect_image_segments(event):
        return None, None, None

    # 截断：单把尺子（max_message_length）；原始正文已由流水线截过，这里只兜底
    max_msg_len = int(config.get("max_message_length", 200) or 200)
    user_text = user_text[:max_msg_len]
    raw_user_text = raw_user_text[:max_msg_len]

    session_mgr = session_mgr or SessionManager(str(runtime.bot_id))
    session = session_mgr.get_session(session_id)
    if not session:
        session = session_mgr.create_session(
            session_id,
            "private" if is_private else "group",
            config.get("session_timeout", 60),
        )
        if not is_private:
            session.reply_cooldown = config.get("reply_cooldown", 5)
        await asyncio.to_thread(session_mgr.restore_session_from_archive, session, session_id)
    else:
        # 群聊冷却：非 @ 且不在冷却窗口内 → 本轮不触发（与旧 generate_response 一致）
        if not is_private and not session.can_reply() and not (ctx is not None and ctx.state.get("is_at")):
            return None, None, None
        session.add_participant(user_id)

    # 写历史：结构化（基础信息 + 原始段 + message_id）。
    # 正文与元信息分离存放——时间/群号/发送者由渲染器按当前配置重排，
    # "提到了/引用了"这类附注随正文回放；message_id 是后续"按需补全"的锚点。
    from app.agent.session.model import split_user_context

    body_text, meta_lines = split_user_context(user_text)
    event_segments = _segments_as_dicts(getattr(event, "message", None))
    # 图片：**收到即落盘**。历史里只留本地指向（sha），远端直链不写进历史——QQ 图片链接
    # 带 rkey 会过期，留着只会让下一轮请求被网关 400（详见 app/llm/image_media.py）。
    # 这里跑在写历史之前，所以落盘结果随本条一起进归档；本轮请求也复用同一批已落盘的段，
    # 一张图只下载一次。
    if event_segments and _images_enabled(config) and cache_enabled(config):
        try:
            await persist_images(
                event_segments,
                bot_id=getattr(runtime, "bot_id", ""),
                bot=getattr(event, "bot", None),
                max_bytes=max_image_bytes(config),
            )
        except Exception as e:  # noqa: BLE001 —— 落盘失败只降级为占位，不能拖垮本轮对话
            logger.add_info(f"#{runtime.bot_id}").warning(f"图片落盘失败，按占位处理: {e}")
    # 纯图片/表情消息没有正文：**不能**把元信息（"(QQ: …)"/时间行）冻成正文，
    # 否则历史里这条会显示成一串元信息，图片段（base.segments）反倒被无视。
    # 留空正文 + 保留 segments，渲染器会给出 [图片#id] 占位。
    stored_text = body_text if event_segments else (body_text or user_text)
    session_mgr.add_message(
        session_id,
        "user",
        stored_text,
        user_id,
        nickname=_event_nickname(event),
        message_id=getattr(event, "message_id", None),
        timestamp=getattr(event, "time", None),
        segments=event_segments,
        group_id=group_id if not is_private else None,
        is_private=is_private,
        meta_lines=meta_lines,
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
                    bot=getattr(event, "bot", None),
                    max_bytes=max_image_bytes(config),
                    max_entries=int(config.get("image_max_count", 4) or 4),
                )
            except Exception as e:  # noqa: BLE001 —— 回填是增强，失败只降级
                logger.add_info(f"#{runtime.bot_id}").debug(f"历史图片回填失败（已忽略）: {e}")

    pre_history_text = await _fetch_pre_history(
        runtime, event, is_private=is_private, group_id=group_id, user_id=user_id,
        include_pre_history=include_pre_history, history_rounds=history_rounds,
        session_id=session_id, meta_flags=_meta_flags,
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

    schedule_enable = config.get("schedule_enable", True)
    all_specs, skill_blocks, tool_ctx = await _collect_llm_ext(
        runtime, event, session_id, is_private, schedule_enable
    )
    # 发言通道（P2）：文本只当思考，真正的发言走 send_message / finish。
    # 只在「拟人化档位 + speech_via_tool + 流式」三者齐备时挂上；否则保持旧的文本即发言。
    speech = _build_speech(config)
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
    elif _speech_intent(config):
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
    memory_text = await _memory_block(runtime, session_id, user_id, raw_user_text or user_text, event.bot)
    # 指代消解：焦点行 + 明确指向时的确定性预取（放在背景块之前，信息密度更高）
    referent_text = await _referent_block(tool_ctx.runtime, session_id, user_text, tool_ctx)

    # 图片：复用上面**已经落盘**的那批段（一张图只下载一次），渲染成 base64 块；
    # 是否真正传给模型由 assembly 的模态门控决定。
    user_images: list[dict] = []
    if _images_enabled(config):
        try:
            from app.agent.media.image import resolve_images

            image_segments = [
                seg for seg in event_segments if seg.get("type") in IMAGE_LIKE_SEGMENTS
            ]
            if image_segments:
                user_images = await resolve_images(
                    event,
                    bot=getattr(event, "bot", None),
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
    }
    return req, session, meta


async def _fetch_pre_history(
    runtime, event, *, is_private, group_id, user_id, include_pre_history,
    history_rounds, session_id, meta_flags,
) -> str:
    """前置背景：群聊在线记录 / 私聊近期记录（与既有触发条件完全一致）。"""
    if group_id:
        if not include_pre_history:
            return ""
        return await _build_group_pre_history(
            event, group_id, count=history_rounds, **meta_flags,
            **_context_expand_flags(runtime.config),
            bot_id=getattr(event, "bot_id", "") or "",
            session_id=session_id,
        )
    if is_private and include_pre_history in ("history", "load"):
        text = await fetch_private_online_history(
            event.bot,
            user_id,
            count=history_rounds,
            self_ids={str(event.self_id), str(getattr(event, "bot_id", "") or "")},
            bot_id=getattr(event, "bot_id", "") or "",
            session_id=session_id,
        )
        if text and include_pre_history == "history":
            text = f"近期聊天记录:\n{text}"
        return text
    return ""


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
    logger.add_info(f"#{runtime.bot_id}").info(
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

    session_mgr.add_message(session_id, "assistant", _clean_output_for_history(config, clean_response))
    if not is_private:
        session.mark_replied()
    await asyncio.to_thread(session_mgr.history.save_session, session)

    _commit_history_enrichment(runtime, session_id, ctx)
    _memory_consolidate(runtime, session_id, is_private, session_mgr)
    return clean_response


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
    is_private = meta["is_private"]
    user_id = meta["user_id"]
    model = meta["model"]
    max_msg_len = int(
        config.get("stream_sentence_max_length")
        or config.get("max_message_length", 200)
        or 200
    )

    # 发言通道（P2）：``speech`` 非 None 时，模型输出的文本只是思考，发言靠 send_message；
    # 本轮一句话没说 = 静默（正常结局），不再用兜底话术填空。
    from app.agent.delivery.speech import DEFAULT_REMINDER_ROUNDS, SPEAK_REMINDER

    speech = meta.get("speech")
    speech_by_tool = speech is not None
    reminder_rounds = int(config.get("speech_reminder_rounds", DEFAULT_REMINDER_ROUNDS) or 0)
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
            async for message in _flush_speech(speech, session_mgr, session_id, config, event):
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
            async for message in _flush_speech(speech, session_mgr, session_id, config, event):
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
                    session_id, "assistant", _clean_output_for_history(config, fallback)
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
        _cleaned = _clean_output_for_history(config, full_text)
        if not _write_partial_history(_cleaned):
            session_mgr.add_message(session_id, "assistant", _cleaned)
        if hasattr(session_mgr, "end_streaming_assistant"):
            session_mgr.end_streaming_assistant(session_id)
        if not is_private:
            session.mark_replied()
        await asyncio.to_thread(session_mgr.history.save_session, session)

    _commit_history_enrichment(runtime, session_id, ctx)
    _memory_consolidate(runtime, session_id, is_private, session_mgr)

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
    logger.add_info(f"#{runtime.bot_id}").info(
        f"[Budget] 本轮 {turn_budget.describe()}"
        f"，max_tokens={eff_max_tokens}（来自{max_tokens_src}）-> {session_id}"
    )

    if ctx is not None:
        ctx.response_text = full_text


async def handle_commands(module, session_mgr, session_id, group_id, user_id,
                          raw_text, is_admin, is_private, event=None) -> bool:
    cmd = raw_text.strip()
    action = parse_action(cmd)
    if action is None:
        return False

    history_mgr = session_mgr.history
    bot = event.bot

    async def send(msg):
        text = f"# {msg}"
        if is_private:
            await bot.send_private_msg(user_id=int(user_id), message=text)
        else:
            await bot.send_group_msg(group_id=int(group_id), message=text)

    if is_help_action(action):
        await _send_help(
            module, event, bot, is_private=is_private,
            group_id=group_id, user_id=user_id, send=send,
        )
        return True

    session = session_mgr.get_session(session_id)

    if action == "task":
        if session:
            await send(f"当前任务ID: {session.task_id}（对话: {session.active.title if session.active else '?'}）")
        else:
            await send("当前没有活跃会话")
        return True

    elif action == "list":
        if not session:
            await send("当前没有活跃会话")
            return True
        convs = session.list_conversations()
        if not convs:
            await send("当前会话暂无对话记录")
            return True
        lines = [f"当前会话 {len(convs)} 个对话（#llm switch <id> 切换）:"]
        for c in convs[:10]:
            mark = " *" if c["id"] == session.active_id else ""
            lines.append(f"  {c['id'][:8]} | {c['title']} | {c['count']}条{mark}")
        await send("\n".join(lines))
        return True

    elif action == "switch" or action.startswith("switch "):
        target = action[len("switch"):].strip()
        if not target:
            await send("用法：#llm switch <conv_id|task_id>（可用 #llm list 查看）")
            return True
        if not session:
            await send("当前没有活跃会话")
            return True
        # 支持按 conv_id 或 task_id 切换
        hit = None
        for c in session.conversations.values():
            if c.id == target or c.task_id == target:
                hit = c
                break
        if hit and session.switch_conversation(hit.id):
            session_mgr.history.save_session(session)
            await send(f"已切换到对话「{hit.title}」({len(hit.data.history)} 条)")
        else:
            await send(f"未找到对话: {target}")
        return True

    elif action == "new" or action.startswith("new "):
        title = action[4:].strip()
        if session:
            session_mgr.history.save_session(session)
        created = session_mgr.new_conversation(session_id, title)
        if created:
            memory = getattr(module, "memory", None)
            if memory is not None:
                try:
                    memory.on_session_reset(session_id, user_id)
                except Exception as e:
                    logger.add_info(f"#{getattr(module, 'bot_id', '?')}").debug(
                        f"[Memory] 会话重置钩子失败（已忽略）: {e}"
                    )
            await send(f"已开启新对话「{created['title']}」(task: {created['task_id']})")
        else:
            await send("创建失败")
        return True

    elif action == "load" or action.startswith("load "):
        load_task_id = action[len("load"):].strip()
        if not load_task_id:
            await send("用法：#llm load <task_id>")
            return True
        data = history_mgr.load_history(load_task_id)
        if not data:
            await send(f"未找到任务: {load_task_id}")
            return True
        session = session or session_mgr.create_session(session_id, "private" if is_private else "group", 60)
        # 新建会话自带 1 个空对话 → 复用而非再建，避免残留空对话
        fresh = len(session.conversations) == 1 and not any(c.data.history for c in session.conversations.values())
        if fresh:
            conv = next(iter(session.conversations.values()))
            session.active_id = conv.id
        else:
            conv = session.new_conversation(title=data.get("title", "导入"))
        conv.title = data.get("title", "导入")
        conv.task_id = data.get("task_id", conv.task_id)
        conv.data.history = data.get("messages", []) or []
        session.touch()
        history_mgr.save_session(session)
        await send(f"已加载历史: {load_task_id} -> 新对话「{conv.title}」({len(conv.data.history)} 条)")
        return True

    elif action == "export" or action.startswith("export "):
        sub = action[len("export"):].strip()
        export_task_id = sub if sub and " " not in sub else (session.task_id if session else "")
        if not export_task_id:
            await send("当前没有活跃会话可导出，请指定任务ID: #llm export <task_id>")
            return True
        text = history_mgr.export_text(export_task_id)
        if text:
            lines = text.split("\n")
            for i in range(0, len(lines), 10):
                await send(f"导出 ({export_task_id}):\n{chr(10).join(lines[i:i+10])}")
        else:
            await send(f"未找到任务: {export_task_id}")
        return True

    elif action == "proactive" or action.startswith("proactive "):
        pm = getattr(module, "proactive", None)
        if pm is None:
            await send("本模块未启用主动消息")
            return True
        target = action[len("proactive "):].strip() if action.startswith("proactive ") else ""
        if target:
            ok = await pm.manual_trigger(target)
            await send(f"已触发 {target} 主动发言" if ok else f"会话 {target} 未启用或不在主动列表")
        else:
            rows = pm.status()
            if not rows:
                await send("暂无已配置的主动会话")
                return True
            lines = ["主动消息状态:"]
            for s in rows:
                mark = "🟢" if s["enabled"] else "⚪"
                next_s = f"，下次 {time.strftime('%m-%d %H:%M', time.localtime(s['next_trigger_time']))}" if s["next_trigger_time"] else ""
                lines.append(f"  {mark} {s['session_id']} | 未回复{s['unanswered']} | {s['timer'] or '未计时'}{next_s}")
            await send("\n".join(lines))
        return True

    elif action == "schedule" or action.startswith("schedule "):
        scheduler = getattr(module, "scheduler", None)
        if scheduler is None:
            await send("本模块未启用定时任务")
            return True
        sub = action[len("schedule "):].strip() if action.startswith("schedule ") else ""
        if sub.startswith("cancel "):
            tid = sub[len("cancel "):].strip()
            ok = scheduler.cancel(tid)
            await send(f"已取消定时任务 {tid}" if ok else f"未找到定时任务 {tid}")
            return True
        rows = scheduler.status()
        if not rows:
            await send("暂无定时任务（可在对话中直接提出定时请求，或用 WebUI 的「定时任务」页面添加）")
            return True
        lines = ["定时任务:"]
        for r in rows:
            next_s = time.strftime("%m-%d %H:%M", time.localtime(r["next_trigger_time"]))
            lines.append(
                f"  {r['task_id'][:8]} | {r['session_id']} | {r['repeat']} | "
                f"下次 {next_s} | {r['content'][:20]}"
            )
        lines.append("#llm schedule cancel <id> 取消；页面也可管理")
        await send("\n".join(lines))
        return True

    elif action.startswith("memory"):
        from app.agent.memory import handle_memory_command

        return await handle_memory_command(module, session_id, user_id, is_admin, is_private, action, send)

    elif action == "exit":
        memory = getattr(module, "memory", None)
        if memory is not None:
            try:
                memory.on_session_reset(session_id, user_id)
            except Exception as e:
                logger.add_info(f"#{getattr(module, 'bot_id', '?')}").debug(
                    f"[Memory] 会话重置钩子失败（已忽略）: {e}"
                )
        if session:
            session_mgr.add_message(session_id, "assistant", "#llm exit")
            history_mgr.save_session(session)
        session_mgr.destroy_session(session_id)
        await send("已退出会话")
        return True

    elif action == "stop":
        if not is_admin:
            await send("权限不足，无法执行此命令")
            return True
        memory = getattr(module, "memory", None)
        if memory is not None:
            try:
                memory.on_session_reset(session_id, user_id)
            except Exception as e:
                logger.add_info(f"#{getattr(module, 'bot_id', '?')}").debug(
                    f"[Memory] 会话重置钩子失败（已忽略）: {e}"
                )
        if session:
            session_mgr.add_message(session_id, "assistant", "#llm stop")
            history_mgr.save_session(session)
        session_mgr.destroy_session(session_id)
        await send("会话已强制结束")
        return True

    # 未知动作：明确回一句（此前静默返回，用户会以为机器人坏了）
    await send(f"未知指令：{action}\n发送 #llm help 查看全部指令")
    return True


def _availability(module) -> dict:
    """当前 bot 实际启用的能力（帮助里标注「本 bot 未启用」，避免照着表试却没反应）。"""
    memory = getattr(module, "memory", None)
    memory_on = False
    if memory is not None:
        try:
            memory_on = bool(memory.enabled())
        except Exception as e:  # noqa: BLE001 —— 帮助渲染不能因为探测失败而中断
            logger.add_info(f"#{getattr(module, 'bot_id', '?')}").debug(
                f"[#llm help] 记忆启用状态探测失败（按未启用展示）: {e}"
            )
    return {
        "schedule": getattr(module, "scheduler", None) is not None,
        "proactive": getattr(module, "proactive", None) is not None,
        "memory": memory_on,
    }


async def _send_help(module, event, bot, *, is_private, group_id, user_id, send) -> None:
    """发送 `#llm help`：优先合并转发，失败降级为分段纯文本。

    合并转发把"说明 + 每个分组"拆成独立节点，长指令表在 QQ 里可展开阅读，
    不会像单条长文本那样被折叠成"[合并转发]"或被截断。
    """
    log = logger.add_info(f"#{getattr(module, 'bot_id', '?')}")
    available = _availability(module)
    self_id = getattr(event, "self_id", None) or getattr(module, "bot_id", None)
    nodes = build_help_nodes(uin=self_id, available=available)

    resp: Any = None
    try:
        if is_private:
            resp = await bot.send_forward_msg(user_id=int(user_id), msgdata=nodes)
        else:
            resp = await bot.send_forward_msg(group_id=int(group_id), msgdata=nodes)
        if isinstance(resp, dict) and resp.get("status") == "ok":
            return
        reason = resp.get("message") if isinstance(resp, dict) else resp
        log.debug(f"[#llm help] 合并转发未成功（{reason}），降级为纯文本")
    except Exception as e:  # noqa: BLE001 —— 合并转发不被支持时必须降级而不是静默失败
        log.debug(f"[#llm help] 合并转发异常（{e}），降级为纯文本")

    for chunk in chunk_text(build_help_text(available=available)):
        await send(chunk)



