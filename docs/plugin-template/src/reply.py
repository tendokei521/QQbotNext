"""消息判断与回复文案（纯函数）：可以脱离框架单测。"""

from __future__ import annotations

from typing import Any, Sequence


def hit_keyword(message: Any, keywords: Sequence[str]) -> bool:
    """消息文本里是否出现任一关键词（忽略大小写）。"""
    text = str(message or "").lower()
    return any(str(word).strip().lower() in text for word in keywords if str(word).strip())


def build_reply(event: Any) -> str:
    """拼出要发送的文本；复杂插件的消息构建也放在这一层。"""
    user_id = getattr(event, "user_id", "") or ""
    return f"收到（来自 {user_id}）"
