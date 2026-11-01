"""本轮消息渲染：结构化格式（粗版）。

`structured_assembly=true` 时用 `assembly.structured.render_turn` 渲染：
`[time=..][sender:..]正文[at:..][reply_to:..]`。
开关关闭时只注入原始正文（不渲染任何元信息）。

引用正文、@ 昵称的取回逻辑保留在这里（框架只提供 `pre_request` 钩子）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.agent import logger
from app.agent.assembly.structured import render_turn
from app.agent.context.group import UNRESOLVED_REPLY

#: 引用 / @ 的占位与展开语义（与 tools 侧对齐）
UNRESOLVED_FORWARD = "【未展开:合并转发{id}】"


def _cfg(ctx, key: str, default: Any):
    from app.agent.policy import policy_config

    cfg = policy_config(getattr(ctx, "runtime", None), "meta")
    if cfg is not None and hasattr(cfg, "get"):
        try:
            return cfg.get(key, default)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[Enhance] 读取配置 {key} 失败，使用默认值 {default!r}: {e}")
    return default


def _time_str() -> str:
    return datetime.now().strftime("%H:%M")


async def collect_user_context(ctx) -> None:
    """收集本轮渲染需要的原料（发送者 / @ / 引用）。"""
    event = ctx.event
    info: dict[str, Any] = {
        "sender": "",
        "sent_text": "",
        "reply": None,
    }
    if event.event_type == "message_group":
        nickname = event.user.card or event.user.nickname or ""
        info["sender"] = nickname or str(event.user_id)
    else:
        info["sender"] = str(event.user_id)
    info["reply"] = await _collect_reply(ctx)
    ctx.state["user_context"] = info


async def format_user_context(ctx) -> None:
    """把本轮消息渲染成结构化文本（开关关闭时保持原样）。"""
    if not bool(_cfg(ctx, "structured_assembly", True)):
        return
    info = ctx.state.get("user_context") or {}
    reply = info.get("reply") or {}
    event = ctx.event
    group = getattr(event, "group", None)
    rendered = render_turn(
        message=event.message,
        time_str=_time_str(),
        sender_id=event.user_id,
        sender_name=info.get("sender") or "",
        group_id=getattr(group, "group_id", None),
        group_name=getattr(group, "group_name", "") or "",
        reply=reply or None,
    )
    if rendered and rendered != ctx.user_text:
        ctx.user_text = rendered
        ctx.state["message_meta_injected"] = True


# ---------- 引用 ----------


def _reply_id(event) -> str:
    for seg in event.message or []:
        if seg.type == "reply":
            return str(seg.data.get("id", "") or "")
    return ""


async def _collect_reply(ctx) -> dict | None:
    event = ctx.event
    reply_id = _reply_id(event)
    if not reply_id:
        return None
    if not event.bot:
        return {"id": reply_id, "message_id": reply_id, "name": "", "text": UNRESOLVED_REPLY.format(id=reply_id)}
    try:
        resp = await event.bot.get_msg(reply_id)
        data = (resp or {}).get("data", {}) or {}
        sender = data.get("sender", {}) or {}
        name = sender.get("card") or sender.get("nickname") or ""
        text = _segments_to_text(data.get("message"), message_id=reply_id)
        return {"id": reply_id, "message_id": reply_id, "name": name, "text": text}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[Enhance] 获取引用消息失败（reply_id={reply_id}）: {e}")
        return {"id": reply_id, "message_id": reply_id, "name": "", "text": UNRESOLVED_REPLY.format(id=reply_id)}


def _segments_to_text(message, message_id: Any = None) -> str:
    """被引用消息的正文：复用群聊背景块的渲染（含占位符）。"""
    from app.domain.message import Message

    if isinstance(message, str):
        return message
    msg = Message.from_onebot(message)
    parts: list[str] = []
    for seg in msg.segments:
        if seg.type == "text":
            parts.append(str(seg.data.get("text", "")))
        elif seg.type in ("image", "forward", "record", "video", "file"):
            parts.append(f"[{seg.type}:id={seg.data.get('file') or seg.data.get('id') or ''}]")
        elif seg.type == "at":
            parts.append(f"[at:id={seg.data.get('qq', '')}]")
        else:
            parts.append(f"[{seg.type}]")
    text = "".join(parts).strip()
    return text or UNRESOLVED_REPLY.format(id=message_id or "")
