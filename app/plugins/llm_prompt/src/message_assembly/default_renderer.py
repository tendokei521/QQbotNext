"""框架默认消息渲染器（不依赖任何模块、不发网络请求）。

职责边界：
- **只做基础渲染**：时间/群/sender/正文/at/占位符/引用的 id；
- **不取回内容**：引用正文、@ 昵称、图片描述、转发展开都留给"接管模块"或工具；
- 输入 ``ctx``（有 ``event`` / ``user_text`` / ``runtime``），输出本轮 user 文本；
  返回 ``None`` 表示"没有可渲染的消息"（调用方保持原样）。

模块接管语义：模块可以在 ``on_load`` 里把 ``runtime.message_renderer`` 换成自己的实现
（完全替代格式，或在默认渲染结果上增强）；``on_unload`` 还原为 ``render_from_ctx``。
"""

from __future__ import annotations

from typing import Any

from .render import render_turn
from .spec import format_time


def _reply_marker(event: Any) -> dict | None:
    for seg in getattr(event, "message", None) or []:
        if getattr(seg, "type", "") == "reply":
            mid = str(getattr(seg, "data", {}).get("id", "") or "")
            return {"id": mid, "message_id": mid} if mid else None
    return None


def render_from_ctx(ctx: Any) -> str | None:
    """把本轮上下文渲染成基础结构化消息；无事件时返回 None。"""
    event = getattr(ctx, "event", None)
    if event is None or not getattr(event, "message", None):
        return None
    group = getattr(event, "group", None)
    user = getattr(event, "user", None)
    sender_name = ""
    if user is not None:
        sender_name = getattr(user, "card", "") or getattr(user, "nickname", "") or ""
    return render_turn(
        message=event.message,
        sender_id=getattr(event, "user_id", ""),
        sender_name=sender_name,
        group_id=getattr(group, "group_id", None),
        group_name=getattr(group, "group_name", "") or "",
        time_str=format_time(getattr(event, "time", None)),
        reply=_reply_marker(event),
    )
