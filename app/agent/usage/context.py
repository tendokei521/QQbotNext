"""用量归属上下文：`source`（哪个模块花的）靠 contextvar 传递。

provider 层拿不到 `session_id` / 模块名，但记账必须知道"这一笔是谁的"。
于是在**调用方**（`run_turn` / `BaseModule.llm_chat` / `llm_stream`）设置上下文，
provider 记账时读取。拿不到就记 `unknown` —— 这本身是有用信号：
它说明这次调用绕过了总线（历史上有 6 处模块直连就是这种情况）。
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

#: 未知归属（没设上下文就记账）
UNKNOWN_SOURCE = "unknown"


@dataclass(frozen=True)
class UsageCtx:
    """一次 LLM 调用的归属信息。"""

    bot_id: str = ""
    source: str = UNKNOWN_SOURCE
    session_id: str = ""
    session_label: str = ""
    group_id: str = ""
    user_id: str = ""


#: 当前上下文的 ContextVar（async 安全：每个任务独立）
_CURRENT: contextvars.ContextVar[UsageCtx | None] = contextvars.ContextVar(
    "qqbot_usage_ctx", default=None
)


def current_usage_ctx() -> UsageCtx:
    """读当前上下文；没设过就返回 `unknown` 归属。"""
    return _CURRENT.get() or UsageCtx()


@contextmanager
def usage_scope(
    *,
    bot_id: Any = "",
    source: str = UNKNOWN_SOURCE,
    session_id: Any = "",
    session_label: str = "",
    group_id: Any = "",
    user_id: Any = "",
) -> Iterator[UsageCtx]:
    """在 with 块内把本次调用归属于给定模块/会话。"""
    ctx = UsageCtx(
        bot_id=str(bot_id or ""),
        source=str(source or UNKNOWN_SOURCE),
        session_id=str(session_id or ""),
        session_label=str(session_label or ""),
        group_id=str(group_id or ""),
        user_id=str(user_id or ""),
    )
    token = _CURRENT.set(ctx)
    try:
        yield ctx
    finally:
        _CURRENT.reset(token)


__all__ = ["UNKNOWN_SOURCE", "UsageCtx", "current_usage_ctx", "usage_scope"]
