"""结构化消息的「解析约定」段（L1，order 60）。

格式自解释（`[time=..][sender:..]正文[at:..][reply_to:..]`），这里只给模型最必要的
几条读法，避免回到"解释旧格式"的大段文案。
"""

from __future__ import annotations

from typing import Any

from .system import CONVENTION


def _cfg(req: Any, key: str, default: Any):
    getter = getattr(getattr(req, "config", None), "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except Exception:  # noqa: BLE001
            return default
    return default


def message_meta_section(req: Any) -> list[dict]:
    if not bool(_cfg(req, "structured_assembly", True)):
        return []
    return [{"role": "system", "content": CONVENTION}]


# 兼容：框架内置段统一用 convention_section 这个名字
convention_section = message_meta_section
