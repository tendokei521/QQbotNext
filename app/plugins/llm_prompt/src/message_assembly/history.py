"""历史渲染接管实现（模块能力）：把历史条目渲染成占位符结构化格式。

契约：``renderer(entry, **kwargs) -> str | None``，entry 是 HistoryEntry 或旧式 dict。
框架在 ``runtime.history_renderer`` 被设置时用它渲染历史；未设置时用原生单行格式。
"""

from __future__ import annotations

from typing import Any

from .render import _head_marker, _sender_marker
from .segments import render_segments
from .spec import escape_body, format_time, marker, quote


def render_history_entry(entry: Any, **kwargs) -> str | None:
    from app.agent.session.model import HistoryEntry

    parsed = entry if isinstance(entry, HistoryEntry) else HistoryEntry.from_dict(entry)
    base = parsed.base
    self_ids = {str(x) for x in (kwargs.get("self_ids") or set())}
    mask = bool(kwargs.get("mask_nickname", False))
    is_me = parsed.role == "assistant" or (base.sender_id and str(base.sender_id) in self_ids)

    if base.segments:
        body = render_segments(base.segments)
    else:
        body = escape_body(base.text or parsed.legacy_text or "")

    time_str = format_time(base.time)
    head = f"time={time_str}"
    if base.group_id not in (None, "", 0, "0"):
        head += f",group_id={base.group_id}"
    name = "我" if is_me else (f"用户<{base.sender_id}>" if mask else base.sender_name)
    return f"[{head}]{_sender_marker(base.sender_id, name)}{body}"


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
