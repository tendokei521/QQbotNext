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
    """
    from app.llm.group_context import safe_sender_label
    from app.llm.history_model import build_current_turn_text

    info = ctx.state.get("user_context")
    if not info:
        return

    event = ctx.event
    is_group = event.event_type == "message_group"
    sender_style = str(_ctx_cfg(ctx, "meta_sender_style", "legacy") or "legacy").lower()
    sent_style = str(_ctx_cfg(ctx, "meta_sent_style", "legacy") or "legacy").lower()

    def _render_sender(s: str) -> str:
        return safe_sender_label(s)

    head_lines: list[str] = []
    tail_lines: list[str] = []
    sender_label = ""

    if _ctx_enabled(ctx, "include_time", True):
        head_lines.append(f"(时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")

    # 私信：独立注入对方 QQ（类似时间行）
    if not is_group and _ctx_enabled(ctx, "include_private_qq", True):
        user_id = getattr(event, "user_id", None)
        if user_id:
            head_lines.append(f"(QQ: {user_id})")

    # 群聊：注入当前群号，避免调用 OneBot send_poke 等工具时遗漏 group_id
    if is_group:
        group_id = getattr(getattr(event, "group", None), "group_id", None)
        if group_id:
            head_lines.append(f"(当前群号: {group_id})")

    if is_group:
        if _ctx_enabled(ctx, "include_sender", True) and info.get("sender"):
            sender = _render_sender(info["sender"])
            if sender_style == "single":
                sender_label = sender
            elif sender_style == "new":
                head_lines.append(f"发送者昵称：{sender}")
            else:
                head_lines.append(f"发送者：{sender}")
        if _ctx_enabled(ctx, "include_mentioned", True) and info.get("mentioned"):
            mentioned = [_render_sender(m) for m in info["mentioned"]]
            tail_lines.append("提到了(用户名)：" + "、".join(mentioned))

    if _ctx_enabled(ctx, "include_quote", True) and info.get("quote"):
        if is_group and _ctx_enabled(ctx, "include_quote_sender", True) and info.get("quote_sender"):
            quote_sender = _render_sender(info["quote_sender"])
            tail_lines.append(f"引用了：{quote_sender}发送的引用消息：“{info['quote']}”")
        else:
            tail_lines.append(f"引用了：{info['quote']}")

    sent_text = ctx.user_text.strip() or (info.get("sent_text") or "").strip()
    if not _ctx_enabled(ctx, "include_sent", True):
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
            "sender_nickname": sender_nickname or str(sender_id),
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
