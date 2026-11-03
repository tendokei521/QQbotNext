"""历史渲染：把历史条目渲染成与「本轮消息」同一套结构化格式。

契约：``renderer(entry, **kwargs) -> str | None``，entry 是 HistoryEntry 或旧式 dict。
框架在 ``runtime.history_renderer`` 上挂的就是本函数（不再是"模块接管才生效"）：
历史与当前轮必须同一口径，否则模型会看到两种格式。

## assistant 条目按回复模式渲染（2026-11-03）

- ``reply_mode=participation``（发言通道）：assistant 历史必须表现为**工具调用回复**
  ``[tool_call:send_message]{"messages":"..."}``，不能再是裸正文——否则模型会照抄
  "历史里 assistant 都是直接说话"，于是自己也不调 send_message（线上实测的哑火原因）。
- ``reply_mode=legacy``（文本即回复）：assistant 仍是不带任何标记的裸正文。
"""

from __future__ import annotations

import json
from typing import Any

from .render import _sender_marker
from .segments import render_segments
from .spec import escape_body, format_time

#: 发言通道下 assistant 历史条目的标记（与真实工具调用同名，强化模型的工具使用模式）
SPEECH_TOOL_CALL_MARKER = "[tool_call:send_message]"


def _render_assistant(text: str, reply_mode: str) -> str:
    """assistant 历史正文：参与度模式还原成工具调用形态，legacy 模式保持裸正文。"""
    if str(reply_mode or "").strip().lower() == "participation":
        payload = json.dumps({"messages": text}, ensure_ascii=False)
        return f"{SPEECH_TOOL_CALL_MARKER}{payload}"
    return text



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
    include_time = bool(kwargs.get("include_time", True))
    is_private = bool(kwargs.get("is_private", False))
    include_sender_name = bool(kwargs.get("include_sender_name", True))

    reply_mode = str(kwargs.get("reply_mode", "") or "")

    # bot 自己的回复（assistant）：legacy 模式原样输出正文；参与度模式还原成
    # ``[tool_call:send_message]{...}``，让历史与"发言要调工具"的协议一致。
    if parsed.role == "assistant":
        if base.segments:
            text = render_segments(base.segments) or ""
        else:
            text = str(base.text or parsed.legacy_text or "")
        return _render_assistant(text, reply_mode)

    is_me = bool(base.sender_id and str(base.sender_id) in self_ids)

    reply_id = _reply_id_from_segments(base.segments)
    # 富渲染优先：历史展开（引用/转发/用户名）走 `render_content`，
    # 群聊在线历史走 `content_renderer`（把 OneBot 段转成文本）。
    rich = kwargs.get("render_content") or kwargs.get("content_renderer")
    body: str | None = None
    if callable(rich):
        try:
            rendered = rich(parsed)
        except Exception as e:  # noqa: BLE001 —— 富渲染失败退回结构化段
            from app.core.logger import logger

            logger.debug(f"[消息组装] 历史富渲染失败（退回段渲染）: {e}")
            rendered = None
        if rendered is not None:
            body = str(rendered)
    if body is None:
        if base.segments:
            body = render_segments(base.segments, message_id=parsed.message_id)
        else:
            body = escape_body(base.text or parsed.legacy_text or "")
    if not reply_id:
        reply_id = _reply_id_from_meta(base.meta_lines)

    head = ""
    if include_time:
        head = f"time={format_time(base.time)}"
    mid = str(parsed.message_id or "").strip()
    if mid:
        head += ("," if head else "") + f"message_id={mid}"
    default_name = "对方" if is_private else ""
    name = "我" if is_me else (f"用户<{base.sender_id}>" if mask else (base.sender_name or default_name))
    sender = _sender_marker(base.sender_id, name) if include_sender_name else ""
    line = (f"[{head}]" if head else "") + sender + body
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
        time_str=format_time(getattr(event, "time", None)),
        reply=reply,
        message_id=getattr(event, "message_id", None),
    )


__all__ = ["render_current_turn", "render_history_entry"]
