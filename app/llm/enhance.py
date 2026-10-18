"""框架级 LLM 上下文增强：用户信息感知 / 私信 QQ / 时间 / 回复打断 / 调试。

从 module/llm_enhance 迁移到框架层，直接挂在 AgentRuntime 的 LlmHookRegistry 上。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.llm import logger
from app.llm.group_context import UNRESOLVED_REPLY, extract_msg_text, has_real_content


def _ctx_cfg(ctx, key: str, default: Any):
    cfg = getattr(getattr(ctx, "runtime", None), "config", None)
    if cfg is not None and hasattr(cfg, "get"):
        try:
            return cfg.get(key, default)
        except Exception as e:
            # 配置读取失败时退回默认值，避免钩子因个别配置异常整体中断
            logger.debug(f"[Enhance] 读取配置 {key} 失败，使用默认值 {default!r}: {e}")
    return default


def _ctx_enabled(ctx, key: str, default: bool) -> bool:
    return bool(_ctx_cfg(ctx, key, default))


def _raw_user_text(event) -> str:
    parts: list[str] = []
    for seg in getattr(event, "message", []) or []:
        if isinstance(seg, dict):
            if seg.get("type") == "text":
                data = seg.get("data", {}) or {}
                parts.append(data.get("text", ""))
        else:
            if getattr(seg, "type", "") == "text":
                data = getattr(seg, "data", {}) or {}
                parts.append(data.get("text", ""))
    return "".join(parts).strip()


async def collect_user_context(ctx):
    """收集上下文信息，暂存到 ctx.state。"""
    event = ctx.event
    info = {
        "sender": None,
        "mentioned": [],
        "quote": None,
        "quote_sender": None,
        "sent_text": _raw_user_text(event),
    }

    if event.event_type == "message_group":
        if _ctx_enabled(ctx, "include_sender", True):
            nickname = event.user.card or event.user.nickname or ""
            info["sender"] = f"{nickname}({event.user_id})" if nickname else str(event.user_id)

        if _ctx_enabled(ctx, "include_mentioned", True):
            info["mentioned"] = await _collect_at_info(ctx)

    if _ctx_enabled(ctx, "include_quote", True):
        quote = await _collect_quote_info(ctx)
        if quote:
            info["quote"] = quote["text"]
            info["quote_sender"] = f"{quote['sender_nickname']}({quote['sender_id']})"

    ctx.state["user_context"] = info


async def format_user_context(ctx):
    """把上下文格式化为最终 user_text：时间 / 私信 QQ / 发送者 / 发送正文。

    组装规则复用 ``history_model.build_current_turn_text``——与历史条目同一套字段语义
    （头部行 + 正文 + 附注行），避免"本轮"和"历史"两套格式各自漂移。

    ``assembly_profile=qq_agent`` + ``user_message_format=single_line`` 时改成**单行**形态：
    本轮消息和历史条目长得一样（``昵称(QQ): 正文``），时间挪到末尾，六行元信息块消失。
    旧形态下"本轮是六行块、历史是单行"本身就是模型混乱的来源之一。
    """
    from app.llm.group_context import safe_sender_label
    from app.llm.history_model import build_current_turn_text

    info = ctx.state.get("user_context")
    if not info:
        return

    cfg = getattr(getattr(ctx, "runtime", None), "config", None)

    def _on(key: str, default: bool) -> bool:
        return bool(_ctx_cfg(ctx, key, default))

    from app.llm.prompt import prompt_time_position, user_message_format

    event = ctx.event
    is_group = event.event_type == "message_group"
    sender_style = str(_ctx_cfg(ctx, "meta_sender_style", "legacy") or "legacy").lower()
    sent_style = str(_ctx_cfg(ctx, "meta_sent_style", "legacy") or "legacy").lower()

    # 档位开关：legacy 档两个判定都退回旧行为（时间在头、六行块）
    single_line = user_message_format(cfg) == "single_line"
    time_position = prompt_time_position(cfg)
    if single_line:
        sender_style = "single"

    def _render_sender(s: str) -> str:
        return safe_sender_label(s)

    head_lines: list[str] = []
    tail_lines: list[str] = []
    sender_label = ""

    time_line = ""
    if _on("include_time", True):
        time_line = f"(时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"
        if time_position == "head":
            head_lines.append(time_line)

    # 私信：独立注入对方 QQ（类似时间行）
    if not is_group and _on("include_private_qq", True):
        user_id = getattr(event, "user_id", None)
        if user_id:
            head_lines.append(f"(QQ: {user_id})")

    # 群聊：注入当前群号，避免调用 OneBot send_poke 等工具时遗漏 group_id。
    # 单行形态下它仍留在头部：这是**逐轮稳定**的一行，放前面才能吃到前缀缓存。
    if is_group:
        group_id = getattr(getattr(event, "group", None), "group_id", None)
        if group_id:
            head_lines.append(f"(当前群号: {group_id})")

    if is_group:
        if _on("include_sender", True) and info.get("sender"):
            sender = _render_sender(info["sender"])
            if sender_style == "single":
                sender_label = sender
            elif sender_style == "new":
                head_lines.append(f"发送者昵称：{sender}")
            else:
                head_lines.append(f"发送者：{sender}")
        if _on("include_mentioned", True) and info.get("mentioned"):
            mentioned = [_render_sender(m) for m in info["mentioned"]]
            tail_lines.append("提到了(用户名)：" + "、".join(mentioned))

    if _on("include_quote", True) and info.get("quote"):
        if is_group and _on("include_quote_sender", True) and info.get("quote_sender"):
            quote_sender = _render_sender(info["quote_sender"])
            tail_lines.append(f"引用了：{quote_sender}发送的引用消息：“{info['quote']}”")
        else:
            tail_lines.append(f"引用了：{info['quote']}")
    if time_position == "tail" and time_line:
        tail_lines.append(time_line)

    sent_text = ctx.user_text.strip() or (info.get("sent_text") or "").strip()
    if not _on("include_sent", True):
        sent_text = ""
    # single 风格把发送者塞进正文行；此时不再单独输出「发送者：」行
    if sender_style == "single":
        head_lines = [line for line in head_lines if not line.startswith(("发送者：", "发送者昵称："))]

    formatted = build_current_turn_text(
        sent_text=sent_text,
        current_text=ctx.user_text,
        sender_label=sender_label,
        head_lines=head_lines,
        tail_lines=tail_lines,
        sender_style=sender_style,
        sent_style=sent_style,
    )
    ctx.user_text = formatted
    if len(formatted.splitlines()) > 1 or formatted != (info.get("sent_text") or "").strip():
        ctx.state["message_meta_injected"] = True


async def interrupt_config_hook(ctx):
    ctx.runtime.interrupt_enabled = _ctx_enabled(ctx, "interrupt_enable", False)
    ctx.runtime.interrupt_save_sent = _ctx_enabled(ctx, "interrupt_save_sent", True)
    if _ctx_enabled(ctx, "interrupt_debug", False):
        logger.add_info(f"#{ctx.runtime.bot_id}").info(
            f"[打断] {ctx.session_id} interrupt_enabled={ctx.runtime.interrupt_enabled}"
        )


# ---------- 群成员昵称 / 引用辅助 ----------


async def _collect_at_info(ctx) -> list[str]:
    event = ctx.event
    if not event.message:
        return []
    result: list[str] = []
    for seg in event.message:
        if seg.type != "at":
            continue
        qq = str(seg.data.get("qq", "") or "")
        if not qq:
            continue
        if qq in (str(event.self_id), str(getattr(event, "bot_id", "") or "")):
            continue
        if qq in ("all", "0"):
            result.append("全体成员")
            continue
        nickname = qq
        if _ctx_enabled(ctx, "fetch_at_nickname", True):
            fetched = await _fetch_group_member_nickname(ctx, qq)
            if fetched:
                nickname = fetched
        result.append(f"{nickname}({qq})")
    return result


async def _fetch_group_member_nickname(ctx, qq: str) -> str:
    """反查群成员昵称；实现与缓存已抽到 ``app.llm.nicknames``（与背景块渲染共用）。

    共用缓存意味着：本轮触发消息里展开过的 @ 对象，背景块渲染时直接命中，不重复请求。
    """
    from app.llm.nicknames import fetch_nickname

    event = ctx.event
    group_id = getattr(event.group, "group_id", None)
    if not group_id or not event.bot:
        return ""
    return await fetch_nickname(
        event.bot, group_id, qq, bot_id=str(getattr(event, "bot_id", "") or "")
    )


def _display_sender(ctx, sender: dict) -> str:
    """引用行的发送者显示名（句子型昵称 → ``用户<QQ>``）。

    取不到 runtime 时映射层会退化成安全名，不影响渲染。
    """
    from app.llm import display_names

    runtime = getattr(ctx, "runtime", None)
    event = getattr(ctx, "event", None)
    return display_names.display_for_sender(
        sender,
        bot_id=str(getattr(runtime, "bot_id", "") or getattr(event, "bot_id", "") or ""),
        group_id=str(getattr(getattr(event, "group", None), "group_id", "") or ""),
        runtime=runtime,
    )


async def _collect_quote_info(ctx) -> dict | None:
    event = ctx.event
    if not event.bot or not _ctx_enabled(ctx, "fetch_quote_content", True):
        return None
    reply_id = None
    for seg in event.message:
        if seg.type == "reply":
            reply_id = str(seg.data.get("id", "") or "")
            break
    if not reply_id:
        return None
    try:
        resp = await event.bot.get_msg(reply_id)
        data = (resp or {}).get("data", {}) or {}
        sender = data.get("sender", {}) or {}
        sender_nickname = sender.get("card") or sender.get("nickname") or ""
        sender_id = sender.get("user_id", "")
        text = _segments_to_text(data.get("message"), message_id=reply_id)
        if not text or not has_real_content(text):
            # 被引用的消息本身没有正文（合并转发 / 图片 / 已过期）：只回一个 "[forward]"
            # 之类段名对模型毫无用处——它既不知道那是什么、也拿不到 id 去展开。
            # 这里改用可解决的缺口标记，把 reply_id 交给模型（expand_context 能据此取回内容）。
            text = UNRESOLVED_REPLY.format(id=reply_id)
        return {
            "text": text,
            # 引用行的发送者也要走显示名映射：句子型昵称混进"引用了"这一行，
            # 模型会把它当成某人说的话（与工具结果同一类泄漏）。
            "sender_nickname": _display_sender(ctx, sender) or sender_nickname or str(sender_id),
            "sender_id": sender_id,
        }
    except Exception as e:
        # 引用内容属增强能力：失败不应阻断回复，但必须留痕（否则表现为"引用突然看不见了"）
        logger.debug(f"[Enhance] 获取引用消息内容失败（reply_id={reply_id}）: {e}")
        return {
            "text": UNRESOLVED_REPLY.format(id=reply_id),
            "sender_nickname": "",
            "sender_id": "",
        }


def _segments_to_text(message, message_id: Any = None) -> str:
    """消息段 → 可读文本（与群聊背景块同一套渲染：@ 展开、非文本占位、缺口标记）。

    ``message_id`` 是承载这些段的整条消息 id：合并转发标记用它（展开入口要的是这个 id，
    不是转发段内部的 ``data.id``——后者超长且超出 int32/JS 安全整数范围）。

    历史问题：这里此前只拼段类型名，合并转发会渲染成英文 ``[forward]``——既不是内容也不
    含可用的 id，模型拿着它什么也做不了。现在委托给 ``group_context.extract_msg_text``
    （标记为未展开），无法识别的段类型再退回段名列表兜底。
    """
    from app.domain.message import Message

    if isinstance(message, str):
        return message
    rendered = extract_msg_text(message, mark_unresolved=True, message_id=message_id)
    if has_real_content(rendered):
        return rendered
    msg = Message.from_onebot(message)
    fallback = "[" + ",".join(s.type for s in msg.segments) + "]" if msg.segments else ""
    return rendered or fallback


def install_framework_hooks(runtime) -> None:
    """在 AgentRuntime 上注册框架级 LLM 钩子。"""
    registry = getattr(runtime, "llm_hooks", None)
    if registry is None:
        return
    registry.register(stage="pre_request", event_type="*", order=-100, handler=collect_user_context)
    registry.register(stage="pre_request", event_type="*", order=20, handler=format_user_context)
    registry.register(stage="pre_request", event_type="*", order=25, handler=interrupt_config_hook)
