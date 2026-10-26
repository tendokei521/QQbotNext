"""历史窗口（Q2）：决定"这一次请求内联多少条历史"。

为什么单独成模块：这段逻辑有三个调用方关心（`prompt_prep`、测试、将来的主动消息），
而它又很容易写错（少截一条 / 提示语带条数导致每轮改写 system 前缀）。抽成纯函数后：

- `apply_history_mode()`：只做"取哪一段"，不读 IO、不碰 session；
- 提示语常量由 `assembly.prompt.HISTORY_OMITTED_HINT` 提供，**不含数字**。

`history_mode=full` 时原样返回（与改造前逐字一致），所以这是可选能力而不是行为替换。
"""

from __future__ import annotations

from typing import Any

DEFAULT_RECENT = 12
MIN_RECENT = 2


def history_mode_of(config: Any) -> str:
    """`recent` / `full`；读不出来按 `recent`（默认值在 config 层已声明）。"""
    getter = getattr(config, "get", None)
    if not callable(getter):
        return "recent"
    try:
        mode = str(getter("history_mode", "recent") or "recent").strip().lower()
    except Exception:  # noqa: BLE001 —— 配置异常按默认
        return "recent"
    return mode if mode in ("recent", "full") else "recent"


def recent_limit_of(config: Any) -> int:
    """内联条数上限（至少 2 条：否则历史里连一轮问答都放不下）。"""
    getter = getattr(config, "get", None)
    if not callable(getter):
        return DEFAULT_RECENT
    try:
        raw = getter("history_recent_messages", DEFAULT_RECENT)
        value = DEFAULT_RECENT if raw is None else int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_RECENT
    return max(MIN_RECENT, value)


def apply_history_mode(history: list[dict], config: Any) -> tuple[list[dict], bool]:
    """按配置截取历史，返回 ``(要内联的历史, 是否发生了截断)``。

    只截尾部：更早的内容仍留在会话存储里，由 `search_history` 工具按需取回。
    """
    items = list(history or [])
    if history_mode_of(config) != "recent":
        return items, False
    limit = recent_limit_of(config)
    if len(items) <= limit:
        return items, False
    return items[-limit:], True
