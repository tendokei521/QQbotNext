"""核心聊天逻辑（框架级 MainAgent 流程）。

指令系统（#chat 开头）：
  #chat task / export / list / load / new / exit / stop / proactive / schedule
"""


import asyncio
import json
import time
from typing import Any

from app.llm import logger
from app.llm.group_context import (
    build_group_env_text,
    fetch_group_online_history,
    fetch_private_online_history,
    format_history_for_llm,
)
from app.llm.session import SessionManager
from app.llm.prompt import LEGACY_MESSAGE_META_INSTRUCTION, MESSAGE_META_INSTRUCTION, build_messages
from app.llm.providers import chat_with_fallback, iter_stream_with_fallback
from app.llm.tags import maybe_strip_parentheses, strip_all_tags
from app.llm.splitter import split_sentences, strip_stream_artifacts
from app.llm.trigger import check_trigger, extract_text
from app.llm.tool import ToolContext, build_tools, make_executor
from app.llm.tool_loop import normalize_and_execute_tool_calls

import re


def _event_nickname(event) -> str:
    """取发送者群名片/昵称，用于会话历史保留发送者身份。"""
    user = getattr(event, "user", None)
    if user is None:
        return ""
    return getattr(user, "card", "") or getattr(user, "nickname", "") or ""


def _format_session_history(
    history: list[dict],
    is_private: bool,
    *,
    normalize_enhanced: bool = False,
    mask_nickname: bool = False,
) -> list[dict]:
    """兼容包装：委托给 group_context 的共享渲染函数。"""
    return format_history_for_llm(
        history,
        is_private=is_private,
        normalize_enhanced=normalize_enhanced,
        mask_nickname=mask_nickname,
    )


async def _build_group_pre_history(
    event,
    group_id: str,
    count: int,
    *,
    normalize_enhanced: bool = False,
    mask_nickname: bool = False,
) -> str:
    """根据 include_pre_history 配置，拉取并组装群聊环境背景块。

    不拉取在线历史时返回空字符串；本函数不改变“非 @ 不入会话历史”的策略。
    """
    history_text = await fetch_group_online_history(
        event.bot,
        group_id,
        count=count,
        self_ids={str(event.self_id), str(getattr(event, "bot_id", "") or "")},
        normalize_enhanced=normalize_enhanced,
        mask_nickname=mask_nickname,
    )
    if not history_text:
        return ""
    return build_group_env_text(
        group_id=group_id,
        group_name=getattr(event.group, "group_name", "") or "",
        history_text=history_text,
    )


def _collect_llm_ext(runtime, event, session_id: str, is_private: bool, schedule_enable: bool):
    """收集本次请求的模块工具 + 技能 + ToolContext。

    返回 (specs, skill_blocks, ctx)；specs 已包含内置 schedule_task。
    """
    specs = []
    if schedule_enable:
        from app.llm.scheduler import build_schedule_tool

        specs.append(build_schedule_tool(runtime, session_id, is_private))

    if getattr(runtime, "llm_tools", None) is not None:
        specs.extend(runtime.llm_tools.enabled_specs())

    skill_blocks = []
    if getattr(runtime, "skills", None) is not None:
        skill_blocks = runtime.skills.prompt_blocks()

    ctx = ToolContext(
        module=runtime,
        bot=event.bot,
        session_id=session_id,
        event=event,
        runtime=runtime,
        user_id=getattr(event, "user_id", None),
        group_id=getattr(getattr(event, "group", None), "group_id", None),
    )

    # 长期记忆原生工具：memory_save / recall / delete / correct / deny
    memory = getattr(runtime, "memory", None)
    if memory is not None and memory.enabled():
        from app.llm.memory import build_memory_tools

        memory_user_id = str(getattr(event, "user_id", "") or "")
        specs.extend(build_memory_tools(runtime, session_id, memory_user_id, is_private))

    return specs, skill_blocks, ctx


async def _memory_block(runtime, session_id: str, user_id: Any, user_text: str, bot) -> str:
    """召回长期记忆块；未启用或异常时返回空串。"""
    memory = getattr(runtime, "memory", None)
    if memory is None or not memory.enabled():
        return ""
    try:
        return await memory.recall_block_async(session_id, user_id, user_text, bot=bot)
    except Exception:
        return ""


def _memory_autosave(runtime, session_id: str, user_id: Any, text: str) -> None:
    """确定性“记住…”兜底写入；未启用或异常时静默跳过。"""
    memory = getattr(runtime, "memory", None)
    if memory is None or not memory.enabled():
        return
    try:
        memory.autosave(session_id, user_id, text)
    except Exception:
        pass


def _memory_consolidate(runtime, session_id: str, is_private: bool, session_mgr) -> None:
    """回复后触发限频隐式蒸馏；未启用或异常时静默跳过。"""
    memory = getattr(runtime, "memory", None)
    if memory is None or not memory.enabled():
        return
    try:
        history = session_mgr.get_history(session_id, limit=20)
        memory.maybe_consolidate(session_id, not is_private, history, source="chat")
    except Exception:
        pass


def _message_meta_instruction(runtime, ctx) -> str | None:
    """根据 meta_instruction_mode 选择消息元信息消歧说明文本。

    off=不注入；legacy=旧版说明（默认）；new=新版单行脱敏说明。
    """
    if ctx is None or not ctx.state.get("message_meta_injected"):
        return None
    mode = str(runtime.config.get("meta_instruction_mode", "legacy") or "legacy").lower()
    if mode == "off":
        return None
    if mode == "new":
        return MESSAGE_META_INSTRUCTION
    return LEGACY_MESSAGE_META_INSTRUCTION


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


async def handle(module, event):
    """唯一入口：api_key 校验 + 消息类型开关过滤后分发。"""
    config = module.config
    api_key = _provider_config_for(module).get("api_key", "")
    if not api_key:
        logger.add_info(f"#{module.bot_id}").error(f"[{module.name}] API 密钥未配置，请先在 Provider 预设中选择连接配置")
        return

    message_type = event.message_type
    if message_type not in ("group", "private"):
        return
    if message_type == "private":
        session_id = f"private_{event.user_id}"
    else:
        session_id = f"group_{event.group.group_id}"
    config = module.config
    config.set_session(session_id)
    try:
        if message_type == "private":
            if not config.get("private_enable", True):
                return
            await handle_private(module, event, config)
        else:
            if not config.get("group_enable", False):
                return
            await handle_group(module, event, config)
    finally:
        config.clear_session()


async def handle_group(module, event, config):
    session_mgr = SessionManager(str(module.bot_id))
    group_id = str(event.group.group_id)
    user_id = str(event.user_id)
    self_id = str(event.self_id)
    message_data = event.message
    session_id = f"group_{group_id}"
    raw_text = extract_text(message_data).strip()
    is_admin = event.is_admin

    if raw_text.startswith("#chat "):
        await handle_commands(
            module, session_mgr, session_id, group_id, user_id,
            raw_text, is_admin, is_private=False, event=event,
        )
        return

    trigger_at = config.get("trigger_at", True)
    trigger_keyword = config.get("trigger_keyword", [])
    triggered, is_at, user_text = check_trigger(message_data, self_id, trigger_at, trigger_keyword)
    if not triggered:
        return

    if is_at:
        user_text = re.sub(r"\[CQ:at,qq=\d+\]", "", user_text).strip()
        user_text = re.sub(r"@\S+\s*", "", user_text).strip()

    max_msg_len = config.get("max_message_length", 200)
    if not user_text:
        return
    if len(user_text) > max_msg_len:
        user_text = user_text[:max_msg_len]

    # 只有真正触发 LLM 的消息才更新主动消息状态
    pm = getattr(module, "proactive", None)
    if pm is not None:
        await pm.on_message(session_id, True, is_self=(event.user_id == event.self_id))

    session = session_mgr.get_session(session_id)
    if not session:
        session = session_mgr.create_session(session_id, "group", config.get("session_timeout", 60))
        session.reply_cooldown = config.get("reply_cooldown", 5)
        await asyncio.to_thread(session_mgr.restore_session_from_archive, session, session_id)
    else:
        if not session.can_reply() and not is_at:
            return
        session.add_participant(user_id)

    session_mgr.add_message(
        session_id,
        "user",
        user_text,
        user_id,
        nickname=_event_nickname(event),
    )
    await call_llm_and_reply(
        module, event, session_mgr, config,
        session_id, user_id, group_id, is_private=False,
        include_pre_history=config.get("include_pre_history", False),
    )


async def handle_private(module, event, config):
    session_mgr = SessionManager(str(module.bot_id))
    user_id = str(event.user_id)
    message_data = event.message
    session_id = f"private_{user_id}"
    raw_text = extract_text(message_data).strip()
    is_admin = event.is_admin

    if raw_text.startswith("#chat "):
        await handle_commands(
            module, session_mgr, session_id, None, user_id,
            raw_text, is_admin, is_private=True, event=event,
        )
        return

    if not raw_text:
        return
    max_msg_len = config.get("max_message_length", 200)
    user_text = raw_text[:max_msg_len]

    # 只有真正进入 LLM 的私信才更新主动消息状态
    pm = getattr(module, "proactive", None)
    if pm is not None:
        await pm.on_message(session_id, False, is_self=(event.user_id == event.self_id))

    session = session_mgr.get_session(session_id)
    if not session:
        session = session_mgr.create_session(session_id, "private", config.get("session_timeout", 60))
        await asyncio.to_thread(session_mgr.restore_session_from_archive, session, session_id)

    session_mgr.add_message(
        session_id,
        "user",
        user_text,
        user_id,
        nickname=_event_nickname(event),
    )
    await call_llm_and_reply(
        module, event, session_mgr, config,
        session_id, user_id, None, is_private=True,
        include_pre_history=config.get("include_private_pre_history", "default"),
    )


async def call_llm_and_reply(module, event, session_mgr, config,
                             session_id, user_id, group_id, is_private, include_pre_history):
    model = config.get("model", "deepseek-chat")
    system_prompt = config.get("system_prompt", "你是一个友好的助手。")
    max_tokens = config.get("max_tokens", 1024)
    temperature = config.get("temperature", 0.7)
    history_rounds = config.get("history_rounds", 50)

    session = session_mgr.get_session(session_id)
    if not session:
        return

    # 前置历史（群聊近期 / 私信近期）。
    # 群聊只有 include_pre_history 开启时才拉在线记录作为背景；
    # 非 @ 群消息仍然不写入会话历史，保持原有“不积累”策略。
    pre_history_text = ""
    _meta_flags = _history_meta_flags(module)
    if group_id:
        if include_pre_history:
            pre_history_text = await _build_group_pre_history(
                event, group_id, count=history_rounds, **_meta_flags
            )
    elif is_private and include_pre_history in ("history", "load"):
        pre_history_text = await fetch_private_online_history(
            event.bot,
            user_id,
            count=history_rounds,
            self_ids={str(event.self_id), str(getattr(event, "bot_id", "") or "")},
        )
        if pre_history_text and include_pre_history == "history":
            pre_history_text = f"近期聊天记录:\n{pre_history_text}"

    session_history = session_mgr.get_history(session_id, limit=history_rounds)
    user_text = session.data.history[-1]["content"] if session.data.history else ""
    # 防重复：history 尾部就是刚追加的当前用户消息，去掉避免同一消息出现两次
    if (session_history and session_history[-1].get("role") == "user"
            and session_history[-1].get("content") == user_text):
        session_history = session_history[:-1]
    session_history = _format_session_history(session_history, is_private, **_meta_flags)

    schedule_enable = config.get("schedule_enable", True)

    all_specs, skill_blocks, tool_ctx = _collect_llm_ext(
        module, event, session_id, is_private, schedule_enable
    )

    _memory_autosave(module, session_id, user_id, user_text)
    memory_text = await _memory_block(module, session_id, user_id, user_text, event.bot)
    messages = build_messages(
        system_prompt=system_prompt,
        pre_history_text=pre_history_text,
        history=session_history,
        user_text=user_text,
        with_schedule_instruction=schedule_enable,
        schedule_nudge=False,
        skills=skill_blocks,
        memory_text=memory_text,
        message_meta_instruction=_message_meta_instruction(module, None),
    )

    logger.add_info(f"#{module.bot_id}").info(
        f"API 请求 -> {session_id} (task: {session.task_id}), 消息数: {len(messages)}"
    )

    response = await chat_with_fallback(
        _provider_chain_for(module),
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        tools=build_tools(all_specs) if all_specs else None,
        tool_executor=make_executor(all_specs, tool_ctx) if all_specs else None,
    )

    if not response.ok:
        clean_response = "抱歉，我暂时无法回答，请稍后再试。"
    else:
        # 工具结果（定时任务已在工具循环中执行，最终回复是 LLM 基于结果的确认）
        if response.tool_results:
            for tr in response.tool_results:
                logger.add_info(f"#{module.bot_id}").info(
                    f"[Tool] {tr['name']} 执行 -> {str(tr['result'])[:80]}"
                )
        # 防御：剥离角色提示词可能输出的 <type=...> 标签，避免漏到客户端
        clean_response = strip_stream_artifacts(strip_all_tags(response.text))

    # 兜底：模型返回空内容时避免“不回复”，给用户一个可见的占位回复
    if not clean_response:
        logger.add_info(f"#{module.bot_id}").warning(
            f"[LLM] 模型返回空回复，使用兜底文本 -> {session_id}"
        )
        clean_response = "抱歉，我暂时无法回答，请稍后再试。"

    session_mgr.add_message(session_id, "assistant", _clean_output_for_history(config, clean_response))
    if not is_private:
        session.mark_replied()
    await asyncio.to_thread(session_mgr.history.save_session, session)

    _memory_consolidate(module, session_id, is_private, session_mgr)

    try:
        if is_private:
            await event.bot.send_private_msg(user_id=int(user_id), message=clean_response)
        else:
            await event.bot.send_group_msg(group_id=int(group_id), message=clean_response)
        # 主动消息观察：Bot 发言后重置群聊沉默计时器（on_bot_sent 入口）
        pm = getattr(module, "proactive", None)
        if pm is not None:
            await pm.on_bot_sent(session_id, not is_private)
        logger.add_info(f"#{module.bot_id}").info(f"回复完成 -> {session_id} (task: {session.task_id})")
    except Exception as e:
        logger.add_info(f"#{module.bot_id}").error(f"消息发送失败: {e}")


async def generate_response(runtime, event, ctx=None) -> str | None:
    """LLM 流水线专用生成函数：只生成回复文本，不发送消息。

    与 ``call_llm_and_reply`` 保持相同的会话/历史/定时逻辑，
    但把“发送”留给 LLM 流水线统一处理，以便 post_response / pre_send 钩子介入。
    """
    config = runtime.config
    if not runtime.provider_config().get("api_key", ""):
        logger.add_info(f"#{runtime.bot_id}").error("[LLM] API 密钥未配置，请在 Provider 预设中选择连接配置")
        return None
    message_type = getattr(event, "message_type", "")
    if message_type not in ("group", "private"):
        return None

    if message_type == "private":
        if not config.get("private_enable", True):
            return None
        is_private = True
        session_id = f"private_{event.user_id}"
        user_id = str(event.user_id)
        group_id = None
        include_pre_history = config.get("include_private_pre_history", "default")
    else:
        if not config.get("group_enable", False):
            return None
        is_private = False
        session_id = f"group_{event.group.group_id}"
        user_id = str(event.user_id)
        group_id = str(event.group.group_id)
        include_pre_history = config.get("include_pre_history", False)

    if ctx is not None:
        if ctx.session_id:
            session_id = ctx.session_id
        user_text = (ctx.user_text or "").strip()
    else:
        user_text = extract_text(event.message).strip()

    if not user_text:
        return None
    # 框架用户感知已格式化上下文时，不再截断整个 user_text（原始文本已在 pipeline 截断）
    if not (ctx is not None and ctx.state.get("user_context")):
        max_msg_len = config.get("max_message_length", 200)
        user_text = user_text[:max_msg_len]

    session_mgr = SessionManager(str(runtime.bot_id))
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
        if not is_private and not session.can_reply() and not (ctx is not None and ctx.state.get("is_at")):
            return None
        session.add_participant(user_id)

    session_mgr.add_message(
        session_id,
        "user",
        user_text,
        user_id,
        nickname=_event_nickname(event),
    )

    model = config.get("model", "deepseek-chat")
    system_prompt = config.get("system_prompt", "你是一个友好的助手。")
    max_tokens = config.get("max_tokens", 1024)
    temperature = config.get("temperature", 0.7)
    history_rounds = config.get("history_rounds", 50)

    pre_history_text = ""
    _meta_flags = _history_meta_flags(runtime)
    if group_id:
        if include_pre_history:
            pre_history_text = await _build_group_pre_history(
                event, group_id, count=history_rounds, **_meta_flags
            )
    elif is_private and include_pre_history in ("history", "load"):
        pre_history_text = await fetch_private_online_history(
            event.bot,
            user_id,
            count=history_rounds,
            self_ids={str(event.self_id), str(getattr(event, "bot_id", "") or "")},
        )
        if pre_history_text and include_pre_history == "history":
            pre_history_text = f"近期聊天记录:\n{pre_history_text}"

    session_history = session_mgr.get_history(session_id, limit=history_rounds)
    user_text_current = session.data.history[-1]["content"] if session.data.history else ""
    # 防重复：history 尾部就是刚追加的当前用户消息，避免同一消息出现两次
    if (session_history and session_history[-1].get("role") == "user"
            and session_history[-1].get("content") == user_text_current):
        session_history = session_history[:-1]
    session_history = _format_session_history(session_history, is_private, **_meta_flags)

    schedule_enable = config.get("schedule_enable", True)

    all_specs, skill_blocks, tool_ctx = _collect_llm_ext(
        runtime, event, session_id, is_private, schedule_enable
    )

    # 确定性“记住…”兜底应使用用户原始文本，而不是 llm_enhance 增强后的元信息块
    memory_source_text = user_text
    if ctx is not None:
        info = ctx.state.get("user_context") or {}
        memory_source_text = (info.get("sent_text") or user_text).strip()
    _memory_autosave(runtime, session_id, user_id, memory_source_text)
    memory_text = await _memory_block(runtime, session_id, user_id, memory_source_text, event.bot)
    messages = build_messages(
        system_prompt=system_prompt,
        pre_history_text=pre_history_text,
        history=session_history,
        user_text=user_text,
        with_schedule_instruction=schedule_enable,
        schedule_nudge=False,
        skills=skill_blocks,
        memory_text=memory_text,
        message_meta_instruction=_message_meta_instruction(runtime, ctx),
    )

    _log_debug_prompt(runtime, session_id, messages, debug_enabled=bool(ctx and ctx.state.get("debug_prompt", False)))

    logger.add_info(f"#{runtime.bot_id}").info(
        f"API 请求 -> {session_id} (task: {session.task_id}), 消息数: {len(messages)}"
    )

    response = await chat_with_fallback(
        _provider_chain_for(runtime),
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        tools=build_tools(all_specs) if all_specs else None,
        tool_executor=make_executor(all_specs, tool_ctx) if all_specs else None,
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
        logger.add_info(f"#{runtime.bot_id}").warning(
            f"[LLM] 模型返回空回复，使用兜底文本 -> {session_id}"
        )
        clean_response = "抱歉，我暂时无法回答，请稍后再试。"

    session_mgr.add_message(session_id, "assistant", _clean_output_for_history(config, clean_response))
    if not is_private:
        session.mark_replied()
    await asyncio.to_thread(session_mgr.history.save_session, session)

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

    if message_type == "private":
        if not config.get("private_enable", True):
            return
        is_private = True
        session_id = f"private_{event.user_id}"
        user_id = str(event.user_id)
        group_id = None
        include_pre_history = config.get("include_private_pre_history", "default")
    else:
        if not config.get("group_enable", False):
            return
        is_private = False
        session_id = f"group_{event.group.group_id}"
        user_id = str(event.user_id)
        group_id = str(event.group.group_id)
        include_pre_history = config.get("include_pre_history", False)

    if ctx is not None:
        if ctx.session_id:
            session_id = ctx.session_id
        user_text = (ctx.user_text or "").strip()
    else:
        user_text = extract_text(event.message).strip()

    if not user_text:
        return
    max_msg_len = int(
        config.get("stream_sentence_max_length")
        or config.get("max_message_length", 200)
        or 200
    )
    # 框架用户感知已格式化上下文时，不再截断整个 user_text（原始文本已在 pipeline 截断）
    if not (ctx is not None and ctx.state.get("user_context")):
        user_text = user_text[:max_msg_len]

    session_mgr = SessionManager(str(runtime.bot_id))
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
        if not is_private and not session.can_reply() and not (ctx is not None and ctx.state.get("is_at")):
            return
        session.add_participant(user_id)

    session_mgr.add_message(
        session_id,
        "user",
        user_text,
        user_id,
        nickname=_event_nickname(event),
    )

    model = config.get("model", "deepseek-chat")
    system_prompt = config.get("system_prompt", "你是一个友好的助手。")
    max_tokens = config.get("max_tokens", 1024)
    temperature = config.get("temperature", 0.7)
    history_rounds = config.get("history_rounds", 50)

    pre_history_text = ""
    _meta_flags = _history_meta_flags(runtime)
    if group_id:
        if include_pre_history:
            pre_history_text = await _build_group_pre_history(
                event, group_id, count=history_rounds, **_meta_flags
            )
    elif is_private and include_pre_history in ("history", "load"):
        pre_history_text = await fetch_private_online_history(
            event.bot,
            user_id,
            count=history_rounds,
            self_ids={str(event.self_id), str(getattr(event, "bot_id", "") or "")},
        )
        if pre_history_text and include_pre_history == "history":
            pre_history_text = f"近期聊天记录:\n{pre_history_text}"

    session_history = session_mgr.get_history(session_id, limit=history_rounds)
    user_text_current = session.data.history[-1]["content"] if session.data.history else ""
    if (session_history and session_history[-1].get("role") == "user"
            and session_history[-1].get("content") == user_text_current):
        session_history = session_history[:-1]
    session_history = _format_session_history(session_history, is_private, **_meta_flags)

    schedule_enable = config.get("schedule_enable", True)

    all_specs, skill_blocks, tool_ctx = _collect_llm_ext(
        runtime, event, session_id, is_private, schedule_enable
    )

    # 确定性“记住…”兜底应使用用户原始文本，而不是 llm_enhance 增强后的元信息块
    memory_source_text = user_text
    if ctx is not None:
        info = ctx.state.get("user_context") or {}
        memory_source_text = (info.get("sent_text") or user_text).strip()
    _memory_autosave(runtime, session_id, user_id, memory_source_text)
    memory_text = await _memory_block(runtime, session_id, user_id, memory_source_text, event.bot)
    messages = build_messages(
        system_prompt=system_prompt,
        pre_history_text=pre_history_text,
        history=session_history,
        user_text=user_text,
        with_schedule_instruction=schedule_enable,
        schedule_nudge=False,
        skills=skill_blocks,
        memory_text=memory_text,
        message_meta_instruction=_message_meta_instruction(runtime, ctx),
    )

    _log_debug_prompt(runtime, session_id, messages, debug_enabled=bool(ctx and ctx.state.get("debug_prompt", False)))

    logger.add_info(f"#{runtime.bot_id}").info(
        f"流式 API 请求 -> {session_id} (task: {session.task_id}), 消息数: {len(messages)}"
    )

    tools = build_tools(all_specs) if all_specs else None
    tool_executor = make_executor(all_specs, tool_ctx) if all_specs else None
    provider_chain = _provider_chain_for(runtime)

    full_text_parts: list[str] = []
    tool_results: list[dict] = []

    for _round in range(5):
        round_text_parts: list[str] = []
        buffer = ""
        tool_calls: dict[int, dict] = {}
        stream_error = ""

        async for ev in iter_stream_with_fallback(
            provider_chain,
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_executor=tool_executor,
        ):
            if ev.type == "text":
                buffer += ev.text
                sentences, buffer = split_sentences(buffer, max_length=max_msg_len)
                for sentence in sentences:
                    clean = strip_stream_artifacts(sentence)
                    if clean:
                        round_text_parts.append(clean)
                        full_text_parts.append(clean)
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

        tail = strip_stream_artifacts(buffer.strip())
        if tail:
            round_text_parts.append(tail)
            full_text_parts.append(tail)
            yield tail
        buffer = ""

        round_text = "".join(round_text_parts)
        full_text = "".join(full_text_parts)

        if stream_error:
            # 记录失败详情，便于定位（error 事件文本通常含 API 返回的状态码与原因）
            logger.add_info(f"#{runtime.bot_id}").error(
                f"[LLM] 流式请求失败 -> {session_id} (task: {session.task_id}), "
                f"round={_round}, 已产文本={bool(full_text_parts)}, 工具数={len(tool_calls)}: {stream_error}"
            )
            if not full_text:
                yield "抱歉，我暂时无法回答，请稍后再试。"
            return

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
    else:
        logger.add_info(f"#{runtime.bot_id}").warning("流式工具循环超过 5 轮，强制结束")

    # 兜底：流式响应为空时避免“不回复”，给用户一个可见的占位回复
    if not full_text.strip():
        logger.add_info(f"#{runtime.bot_id}").warning(
            f"[LLM] 流式模型返回空回复，使用兜底文本 -> {session_id} "
            f"(task: {session.task_id}, 已执行工具数={len(tool_results)})"
        )
        full_text = "抱歉，我暂时无法回答，请稍后再试。"
        yield full_text

    session_mgr.add_message(session_id, "assistant", _clean_output_for_history(config, full_text))
    if not is_private:
        session.mark_replied()
    await asyncio.to_thread(session_mgr.history.save_session, session)

    _memory_consolidate(runtime, session_id, is_private, session_mgr)

    if ctx is not None:
        ctx.response_text = full_text


async def handle_commands(module, session_mgr, session_id, group_id, user_id,
                          raw_text, is_admin, is_private, event=None) -> bool:
    cmd = raw_text.strip()
    if not cmd.startswith("#chat "):
        return False
    parts = cmd.split(maxsplit=1)
    if len(parts) < 2:
        return False

    action = parts[1].strip()
    history_mgr = session_mgr.history
    bot = event.bot

    async def send(msg):
        text = f"# {msg}"
        if is_private:
            await bot.send_private_msg(user_id=int(user_id), message=text)
        else:
            await bot.send_group_msg(group_id=int(group_id), message=text)

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
        lines = [f"当前会话 {len(convs)} 个对话（#chat switch <id> 切换）:"]
        for c in convs[:10]:
            mark = " *" if c["id"] == session.active_id else ""
            lines.append(f"  {c['id'][:8]} | {c['title']} | {c['count']}条{mark}")
        await send("\n".join(lines))
        return True

    elif action.startswith("switch "):
        target = action[7:].strip()
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
                except Exception:
                    pass
            await send(f"已开启新对话「{created['title']}」(task: {created['task_id']})")
        else:
            await send("创建失败")
        return True

    elif action.startswith("load "):
        load_task_id = action[5:].strip()
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
        sub = raw_text[len("#chat export "):].strip()
        export_task_id = sub if sub and " " not in sub else (session.task_id if session else "")
        if not export_task_id:
            await send("当前没有活跃会话可导出，请指定任务ID: #chat export <task_id>")
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
            await send("暂无定时任务（对话中提出定时请求，或用 #chat schedule add 手动添加）")
            return True
        lines = ["定时任务:"]
        for r in rows:
            next_s = time.strftime("%m-%d %H:%M", time.localtime(r["next_trigger_time"]))
            lines.append(
                f"  {r['task_id'][:8]} | {r['session_id']} | {r['repeat']} | "
                f"下次 {next_s} | {r['content'][:20]}"
            )
        lines.append("#chat schedule cancel <id> 取消；页面也可管理")
        await send("\n".join(lines))
        return True

    elif action.startswith("memory"):
        from app.llm.memory import handle_memory_command

        return await handle_memory_command(module, session_id, user_id, is_admin, is_private, action, send)

    elif action == "exit":
        memory = getattr(module, "memory", None)
        if memory is not None:
            try:
                memory.on_session_reset(session_id, user_id)
            except Exception:
                pass
        if session:
            session_mgr.add_message(session_id, "assistant", "#chat exit")
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
            except Exception:
                pass
        if session:
            session_mgr.add_message(session_id, "assistant", "#chat stop")
            history_mgr.save_session(session)
        session_mgr.destroy_session(session_id)
        await send("会话已强制结束")
        return True

    return False



