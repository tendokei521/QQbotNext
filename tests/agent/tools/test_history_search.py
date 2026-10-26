"""Q2：历史按需化（`history_mode=recent` + `search_history`）。

两件事：

1. **窗口**：`recent` 只内联最近 N 条，`full` 原样返回（默认值改变必须有测试盯着）；
2. **取回**：被截掉的更早内容能通过 `search_history` 检索到，且**只查本会话**。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.assembly.history_window import (
    apply_history_mode,
    history_mode_of,
    recent_limit_of,
)
from app.agent.tools.history_search import search_history

# ── 窗口 ──────────────────────────────────────────────


def _history(n: int) -> list[dict]:
    return [{"role": "user", "content": f"第 {i} 条"} for i in range(n)]


def test_full_mode_keeps_everything():
    history = _history(30)

    kept, omitted = apply_history_mode(history, {"history_mode": "full"})

    assert kept == history and omitted is False


def test_recent_mode_keeps_only_the_tail_and_reports_truncation():
    history = _history(30)

    kept, omitted = apply_history_mode(
        history, {"history_mode": "recent", "history_recent_messages": 5}
    )

    assert omitted is True
    assert kept == history[-5:]
    assert kept[-1]["content"] == "第 29 条", "保留的是最近的一段"


def test_short_history_is_untouched():
    history = _history(3)

    kept, omitted = apply_history_mode(
        history, {"history_mode": "recent", "history_recent_messages": 12}
    )

    assert kept == history and omitted is False


def test_recent_limit_has_a_floor_of_two():
    assert recent_limit_of({"history_recent_messages": 0}) == 2
    assert recent_limit_of({"history_recent_messages": -5}) == 2
    assert recent_limit_of({}) == 12


def test_unknown_mode_falls_back_to_recent():
    assert history_mode_of({"history_mode": "whatever"}) == "recent"
    assert history_mode_of({}) == "recent"
    assert history_mode_of({"history_mode": "FULL"}) == "full"


# ── 检索工具 ──────────────────────────────────────────


class _FakeManager:
    def __init__(self, history: list[dict]) -> None:
        self._history = history
        self.calls: list[tuple[str, int]] = []

    def get_history(self, session_id: str, limit: int = 50):
        self.calls.append((session_id, limit))
        return list(self._history)


def _runtime(manager) -> SimpleNamespace:
    return SimpleNamespace(bot_id="10001", session_mgr=manager)


def test_search_finds_older_turn_and_scopes_to_the_session():
    history = [
        {"role": "user", "content": "上周说过要去爬山", "time": 1700000000, "message_id": "m1"},
        {"role": "assistant", "content": "记得带水", "time": 1700000010},
        {"role": "user", "content": "今天吃什么", "time": 1700000020},
    ]
    manager = _FakeManager(history)

    text = search_history(_runtime(manager), "group_1", "爬山")

    assert "上周说过要去爬山" in text
    assert "今天吃什么" not in text
    assert "message_id=m1" in text
    assert manager.calls and manager.calls[0][0] == "group_1", "只查调用者自己的会话"


def test_search_reports_no_hits_clearly():
    text = search_history(_runtime(_FakeManager([])), "group_1", "不存在")

    assert "没有找到" in text


def test_search_requires_a_query():
    text = search_history(_runtime(_FakeManager([])), "group_1", "  ")

    assert text.startswith("error:")


def test_search_without_session_is_refused():
    text = search_history(_runtime(_FakeManager([])), "", "爬山")

    assert text.startswith("error:")


def test_search_limits_results_and_truncates_long_bodies():
    history = [
        {"role": "user", "content": "关键词 " + "x" * 500} for _ in range(10)
    ]

    text = search_history(_runtime(_FakeManager(history)), "group_1", "关键词", limit=3)

    assert text.count("- [") == 3
    assert "…" in text, "长正文要截断，别把整段塞回模型"
