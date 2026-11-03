"""渲染入口（重写版）：本轮、历史条目、群记录事件、工具结果全部走这里。"""

from __future__ import annotations

from typing import Any

from .segments import render_segments
from .spec import escape_body, format_time, marker, quote


def _sender_marker(
    sender_id: Any, sender_name: str, *, is_me: bool = False, include_name: bool = True
) -> str:
    name = "我" if is_me else str(sender_name or "")
    return marker(
        "sender",
        f"id={'' if sender_id in (None, '') else sender_id}",
        f"name={quote(name)}" if include_name and name else "",
    )


def _head_marker(time_str: str, message_id: Any = None) -> str:
    head = f"time={time_str}"
    if str(message_id or "").strip():
        head += f",message_id={message_id}"
    return f"[{head}]"


def render_turn(
    *,
    message: Any,
    sender_id: Any,
    sender_name: str = "",
    time_str: str = "",
    reply: dict | None = None,
    message_id: Any = None,
    include_sender_name: bool = True,
) -> str:
    """本轮消息：头部（time/message_id）+ 内容流 + 引用。

    ``include_sender_name=False``：发送者昵称走 OpenAI message 的 ``name`` 字段，
    正文不再放任何 ``[sender:...]`` 占位符。
    """
    out = [_head_marker(time_str or format_time(), message_id)]
    if include_sender_name:
        out.append(_sender_marker(sender_id, sender_name))
    out.append(render_segments(message, message_id=message_id))
    if reply:
        fields = [
            f"id={reply.get('id', '')}",
            f"name={quote(reply.get('name', ''))}" if reply.get("name") else "",
            f"text={quote(reply.get('text', ''))}" if reply.get("text") else "",
            f"message_id={reply.get('message_id', '')}" if reply.get("message_id") else "",
        ]
        out.append(marker("reply_to", *fields))
    return "".join(out)


def render_history_entry(
    *,
    time: Any,
    sender_id: Any,
    sender_name: str = "",
    content: str = "",
    is_me: bool = False,
    mask_nickname: bool = False,
    message_id: Any = None,
    meta_lines: list[str] | None = None,
    include_sender_name: bool = True,
) -> str:
    """历史条目：``[time=…]正文``；昵称走 OpenAI ``message.name``。

    ``include_sender_name=False`` 时正文里不放任何发送者占位符（新方案直接传 name）。
    """
    name = sender_name
    if mask_nickname:
        name = f"用户<{sender_id}>"
    head = _head_marker(format_time(time), message_id)
    sender = _sender_marker(sender_id, name, is_me=is_me) if include_sender_name else ""
    line = f"{head}{sender}{escape_body(content)}"
    # 附注行：结构化标记（如 [reply_to:...]）接在行尾，其它文本另起一行
    for meta in meta_lines or []:
        text = str(meta or "").strip()
        if not text:
            continue
        line += text if text.startswith("[") else "\n" + text
    return line


def render_group_event(
    *,
    time: Any,
    sender_id: Any,
    sender_name: str = "",
    text: str = "",
    message_id: Any = None,
    is_me: bool = False,
    reply_to: Any = None,
) -> str:
    """群记录事件：一条一行，消息 id / 引用也用标记表达。"""
    head = _head_marker(format_time(time), message_id)
    parts = [head, _sender_marker(sender_id, sender_name, is_me=is_me)]
    parts.append(escape_body(text))
    if reply_to:
        parts.append(marker("reply_to", f"id={reply_to}"))
    return "".join(parts)
