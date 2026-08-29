"""框架级 LLM 上下文增强：用户信息感知 / 私信 QQ / 时间 / 回复打断 / 调试。

从 module/llm_enhance 迁移到框架层，直接挂在 AgentRuntime 的 LlmHookRegistry 上。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.llm import logger


def _ctx_cfg(ctx, key: str, default: Any):
    cfg = getattr(getattr(ctx, "runtime", None), "config", None)
    if cfg is not None and hasattr(cfg, "get"):
        try:
            return cfg.get(key, default)
        except Exception:
            pass
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
    """把上下文格式化为最终 user_text：时间 / 私信 QQ / 发送者 / 发送正文。"""
    from app.llm.group_context import safe_sender_label

    info = ctx.state.get("user_context")
    if not info:
        return

    event = ctx.event
    is_group = event.event_type == "message_group"
    sender_style = str(_ctx_cfg(ctx, "meta_sender_style", "legacy") or "legacy").lower()
    sent_style = str(_ctx_cfg(ctx, "meta_sent_style", "legacy") or "legacy").lower()

    def _render_sender(s: str) -> str:
        return safe_sender_label(s)

    parts: list[str] = []
    sender_label = ""

    time_line = ""
    if _ctx_enabled(ctx, "include_time", True):
        time_line = f"(时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"

    # 私信：独立注入对方 QQ（类似时间行）
    qq_line = ""
    if not is_group and _ctx_enabled(ctx, "include_private_qq", True):
        user_id = getattr(event, "user_id", None)
        if user_id:
            qq_line = f"(QQ: {user_id})"

    # 群聊：注入当前群号，避免调用 NapCat send_poke 等工具时遗漏 group_id
    group_line = ""
    if is_group:
        group_id = getattr(getattr(event, "group", None), "group_id", None)
        if group_id:
            group_line = f"(当前群号: {group_id})"

    if is_group:
        if _ctx_enabled(ctx, "include_sender", True) and info.get("sender"):
            sender = _render_sender(info["sender"])
            if sender_style == "single":
                sender_label = sender
            elif sender_style == "new":
                parts.append(f"发送者昵称：{sender}")
            else:
                parts.append(f"发送者：{sender}")
        if _ctx_enabled(ctx, "include_mentioned", True) and info.get("mentioned"):
            mentioned = [_render_sender(m) for m in info["mentioned"]]
            parts.append("提到了(用户名)：" + "、".join(mentioned))

    if _ctx_enabled(ctx, "include_quote", True) and info.get("quote"):
        if is_group and _ctx_enabled(ctx, "include_quote_sender", True) and info.get("quote_sender"):
            quote_sender = _render_sender(info["quote_sender"])
            parts.append(f"引用了：{quote_sender}发送的引用消息：“{info['quote']}”")
        else:
            parts.append(f"引用了：{info['quote']}")

    sent_text = ctx.user_text.strip() or (info.get("sent_text") or "").strip()
    if _ctx_enabled(ctx, "include_sent", True) and sent_text:
        if sender_style == "single" and sender_label:
            parts.insert(0, f"{sender_label}: {sent_text}")
        elif sent_style == "new":
            parts.append(f"消息正文：{sent_text}")
        else:
            parts.append(f"发送了：{sent_text}")
    elif sender_style == "single" and sender_label:
        parts.insert(0, sender_label)

    if group_line:
        parts.insert(0, group_line)
    if qq_line:
        parts.insert(0, qq_line)
    if time_line:
        parts.insert(0, time_line)

    if parts:
        ctx.user_text = "\n".join(parts)
        ctx.state["message_meta_injected"] = True
    elif time_line:
        ctx.user_text = f"{time_line}\n{ctx.user_text}".strip()


async def interrupt_config_hook(ctx):
    ctx.runtime.interrupt_enabled = _ctx_enabled(ctx, "interrupt_enable", False)
    ctx.runtime.interrupt_save_sent = _ctx_enabled(ctx, "interrupt_save_sent", True)
    if _ctx_enabled(ctx, "interrupt_debug", False):
        logger.add_info(f"#{ctx.runtime.bot_id}").info(
            f"[打断] {ctx.session_id} interrupt_enabled={ctx.runtime.interrupt_enabled}"
        )


# ---------- 群成员昵称 / 引用辅助 ----------

_NICK_CACHE: dict[str, str] = {}


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
    event = ctx.event
    group_id = getattr(event.group, "group_id", None)
    if not group_id or not event.bot:
        return ""
    cache_key = f"llm_enhance:nick:{event.bot_id}:{group_id}:{qq}"
    if cache_key in _NICK_CACHE:
        return _NICK_CACHE[cache_key]
    try:
        resp = await event.bot.get_group_member_info(group_id=group_id, user_id=int(qq))
        data = (resp or {}).get("data", {}) or {}
        nickname = data.get("card") or data.get("nickname") or ""
        if nickname:
            _NICK_CACHE[cache_key] = nickname
            return nickname
    except Exception:
        pass
    return ""


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
        text = _segments_to_text(data.get("message"))
        if not text:
            return None
        return {
            "text": text,
            "sender_nickname": sender_nickname or str(sender_id),
            "sender_id": sender_id,
        }
    except Exception:
        return None


def _segments_to_text(message) -> str:
    from app.domain.message import Message

    if isinstance(message, str):
        return message
    msg = Message.from_onebot(message)
    text = msg.text
    if text:
        return text
    if msg.segments:
        return "[" + ",".join(s.type for s in msg.segments) + "]"
    return ""


def install_framework_hooks(runtime) -> None:
    """在 AgentRuntime 上注册框架级 LLM 钩子。"""
    registry = getattr(runtime, "llm_hooks", None)
    if registry is None:
        return
    registry.register(stage="pre_request", event_type="*", order=-100, handler=collect_user_context)
    registry.register(stage="pre_request", event_type="*", order=20, handler=format_user_context)
    registry.register(stage="pre_request", event_type="*", order=25, handler=interrupt_config_hook)
