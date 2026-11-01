"""历史渲染接管实现（模块能力）：把历史条目渲染成占位符结构化格式。

契约：``renderer(entry, **kwargs) -> str | None``，entry 是 HistoryEntry 或旧式 dict。
框架在 ``runtime.history_renderer`` 被设置时用它渲染历史；未设置时用原生单行格式。
"""

from __future__ import annotations

from typing import Any

from .render import _head_marker, _sender_marker
from .segments import render_segments
from .spec import escape_body, format_time, marker, quote


def _reply_id_from_segments(segments: Any) -> str:
    """从消息段里取引用 id（reply 是消息内容的一部分，不是外挂字段）。"""
    for seg in segments or []:
        stype = seg.get("type") if isinstance(seg, dict) else getattr(seg, "type", "")
        if stype == "reply":
            data = (seg.get("data") if isinstance(seg, dict) else getattr(seg, "data", {})) or {}
            return str(data.get("id", "") or "")
    return ""


def _reply_id_from_meta(meta_lines: Any) -> str:
    """兜底：旧条目可能把引用写成了附注行。"""
    for meta in meta_lines or []:
        text = str(meta or "").strip()
        if text.startswith("[reply_to:id="):
            return text[len("[reply_to:id="):].rstrip("]").strip()
    return ""


def render_history_entry(entry: Any, **kwargs) -> str | None:
    from app.agent.session.model import HistoryEntry

    parsed = entry if isinstance(entry, HistoryEntry) else HistoryEntry.from_dict(entry)
    base = parsed.base
    self_ids = {str(x) for x in (kwargs.get("self_ids") or set())}
    mask = bool(kwargs.get("mask_nickname", False))

    # bot 自己的回复（assistant）**不加任何组装标记**：原样输出正文。
    # 否则模型会照抄自己的格式（历史里出现 [time][sender] 的 assistant 行），
    # 与框架原生口径一致（框架对 assistant 从不加时间/发送者前缀）。
    if parsed.role == "assistant":
        if base.segments:
            return render_segments(base.segments) or ""
        return str(base.text or parsed.legacy_text or "")

    is_me = bool(base.sender_id and str(base.sender_id) in self_ids)

    reply_id = _reply_id_from_segments(base.segments)
    if base.segments:
        body = render_segments(base.segments, message_id=parsed.message_id)
    else:
        body = escape_body(base.text or parsed.legacy_text or "")
    if not reply_id:
        reply_id = _reply_id_from_meta(base.meta_lines)

    time_str = format_time(base.time)
    head = f"time={time_str}"
    if base.group_id not in (None, "", 0, "0"):
        head += f",group_id={base.group_id}"
    name = "我" if is_me else (f"用户<{base.sender_id}>" if mask else base.sender_name)
    line = f"[{head}]{_sender_marker(base.sender_id, name)}{body}"
    if reply_id:
        line += f"[reply_to:id={reply_id}]"
    for meta in base.meta_lines or []:
        if str(meta or "").strip().startswith("[reply_to:"):
            continue  # 已由消息段推导出来，避免重复
        text = str(meta or "").strip()
        if not text:
            continue
        line += text if text.startswith("[") else "\n" + text
    return line


def render_current_turn(ctx: Any, **kwargs) -> str | None:
    """可选：本轮也由模块接管（消息段直出占位符格式）。"""
    from .render import render_turn

    event = getattr(ctx, "event", None)
    if event is None or not getattr(event, "message", None):
        return None
    group = getattr(event, "group", None)
    user = getattr(event, "user", None)
    name = ""
    if user is not None:
        name = getattr(user, "card", "") or getattr(user, "nickname", "") or ""
    reply = None
    for seg in event.message or []:
        if getattr(seg, "type", "") == "reply":
            mid = str(getattr(seg, "data", {}).get("id", "") or "")
            if mid:
                reply = {"id": mid, "message_id": mid}
            break
    return render_turn(
        message=event.message,
        sender_id=getattr(event, "user_id", ""),
        sender_name=name,
        group_id=getattr(group, "group_id", None),
        group_name=getattr(group, "group_name", "") or "",
        time_str=format_time(getattr(event, "time", None)),
        reply=reply,
    )


__all__ = ["render_current_turn", "render_history_entry"]
