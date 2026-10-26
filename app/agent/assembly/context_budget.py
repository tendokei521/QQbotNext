"""上下文预算：这一轮离"窗口爆掉"还有多远（Q1）。

## 为什么按 token 压力而不是按条数

按条数（`len(history) > history_rounds`）有两个明显的失效模式：

- 一条工具结果就两万字符，50 条可以撑爆窗口；
- 短消息群聊时 50 条才几 k token，却已经开始摘要、白丢细节。

所以触发条件改成"**估算输入 / 模型窗口**"的比例。估算沿用
`assembly/token_budget.estimate_tokens`（字符启发式，只用于防爆、不用于计费），
比例只决定**什么时候压**，压多少由 `keep_ratio` 决定。

窗口来源：`context_window`（agent 配置，0 = 关闭压力触发、回退按条数）。
per-provider 覆盖可以从链上取，但首版只读 agent 配置——一个数字，语义清楚。
"""

from __future__ import annotations

from typing import Any


def estimate_messages_tokens(messages: list[dict]) -> int:
    """估算一份 messages 的输入 token（复用 token_budget 的口径）。"""
    try:
        from app.agent.assembly.token_budget import estimate_tokens

        return int(estimate_tokens(messages or []))
    except Exception:  # noqa: BLE001 —— 估算失败按 0（不触发压力压缩）
        return 0


def context_window_of(config: Any) -> int:
    """模型窗口（token）；读不到或 <=0 表示"不启用压力触发"。"""
    getter = getattr(config, "get", None)
    if not callable(getter):
        return 0
    try:
        return max(0, int(getter("context_window", 0) or 0))
    except (TypeError, ValueError):
        return 0


def usage_ratio(messages: list[dict], window: int) -> float:
    """估算输入占窗口的比例（窗口未知返回 0）。"""
    if not window or window <= 0:
        return 0.0
    return estimate_messages_tokens(messages) / float(window)


def should_compress_by_pressure(
    messages: list[dict],
    config: Any,
    *,
    window: int | None = None,
) -> bool:
    """输入压力是否已越过 `context_compress_trigger_ratio`。

    比例 <= 0 表示"不启用压力触发"（回退到按条数，由调用方判断）。
    """
    getter = getattr(config, "get", None)
    if not callable(getter):
        return False
    try:
        trigger = float(getter("context_compress_trigger_ratio", 0.0) or 0.0)
    except (TypeError, ValueError):
        trigger = 0.0
    if trigger <= 0:
        return False
    win = context_window_of(config) if window is None else int(window or 0)
    if win <= 0:
        return False
    return usage_ratio(messages, win) >= trigger


def keep_count_for(messages: list[dict], config: Any, fallback: int) -> int:
    """要保留多少条原文：`keep_ratio × 总数`，不低于 2 条，不高于 fallback。

    ``history_rounds`` 仍然是**上限**（用户显式配的条数），比例只在上限内收紧。
    """
    total = len(messages or [])
    if total <= 0:
        return 0
    getter = getattr(config, "get", None)
    ratio = 0.0
    if callable(getter):
        try:
            ratio = float(getter("context_compress_keep_ratio", 0.0) or 0.0)
        except (TypeError, ValueError):
            ratio = 0.0
    upper = max(0, int(fallback or 0))
    if ratio <= 0 or upper <= 0:
        return upper
    keep = max(2, int(total * min(ratio, 0.9)))
    return min(keep, upper)
