"""会话事件日志（P4 第 1 步）：追加语义 + 派生投影。

这一步只**加表加类**，不改任何写入口——所以它必须自己证明三件事：

1. seq 按会话单调、只增不改；
2. `derive_messages()` 只投影 surface-eligible（工具/轮次事件留在日志里，模型看不到）；
3. 流式逐句更新（`assistant/delta`）与句柄回填（`assistant/message_id`）都能在
   投影里正确折叠——这两条是后续把 `upsert_streaming_assistant` /
   `mark_last_assistant_message_id` 改成事件语义的硬约束。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.session.history import SQLiteHistoryStore
from app.agent.session.session_log import (
    EVENT_ASSISTANT,
    EVENT_ASSISTANT_DELTA,
    EVENT_ASSISTANT_MESSAGE_ID,
    EVENT_TOOL_CALL,
    EVENT_TURN_END,
    EVENT_USER,
    LOG_ONLY,
    SURFACE_ELIGIBLE,
    SessionLog,
    surface_eligible,
)


@pytest.fixture()
def store(tmp_path) -> SQLiteHistoryStore:
    return SQLiteHistoryStore("1", db_path=str(Path(tmp_path) / "history.db"))


def _entry(role: str, content: str, **extra) -> dict:
    return {"role": role, "time": 1788342260, "content": content, **extra}


def test_append_assigns_monotonic_seq_per_task(store):
    log = SessionLog(store, "task_a")

    first = log.append(EVENT_USER, entry=_entry("user", "你好"))
    second = log.append(EVENT_ASSISTANT, entry=_entry("assistant", "在"))

    assert (first["seq"], second["seq"]) == (1, 2)
    assert [e["seq"] for e in log.events()] == [1, 2]
    assert [e["seq"] for e in log.events(since=1)] == [2]

    other = SessionLog(store, "task_b")
    assert other.append(EVENT_USER, entry=_entry("user", "另一个会话"))["seq"] == 1


def test_events_survive_reopen(tmp_path):
    db = str(Path(tmp_path) / "history.db")
    first = SessionLog(SQLiteHistoryStore("1", db_path=db), "task_a")
    first.append(EVENT_USER, entry=_entry("user", "持久化"))

    reopened = SessionLog(SQLiteHistoryStore("1", db_path=db), "task_a")

    assert [e["payload"]["entry"]["content"] for e in reopened.events()] == ["持久化"]


def test_derive_messages_projects_only_surface_types(store):
    log = SessionLog(store, "task_a")
    log.append(EVENT_USER, entry=_entry("user", "你好"))
    log.append(EVENT_TOOL_CALL, name="probe", args={})
    log.append(EVENT_ASSISTANT, entry=_entry("assistant", "在"))
    log.append(EVENT_TURN_END, reason="completed")

    derived = log.derive_messages()

    assert [m["content"] for m in derived] == ["你好", "在"]
    assert "tool/call" in LOG_ONLY and "turn/end" in LOG_ONLY
    assert surface_eligible("user/message") and not surface_eligible("tool/call")


def test_delta_folds_into_the_same_entry(store):
    """流式边发边更新：同一 entry_id 的多次 delta 只投影成一条。"""
    log = SessionLog(store, "task_a")
    log.append(EVENT_USER, entry=_entry("user", "讲个故事"))
    log.append(EVENT_ASSISTANT_DELTA, entry_id="e1", entry=_entry("assistant", "从前"))
    log.append(EVENT_ASSISTANT_DELTA, entry_id="e1", entry=_entry("assistant", "从前有座山"))

    derived = log.derive_messages()

    assert len(derived) == 2
    assert derived[1]["content"] == "从前有座山"


def test_message_id_is_attached_without_touching_the_body(store):
    log = SessionLog(store, "task_a")
    log.append(EVENT_ASSISTANT, entry=_entry("assistant", "在"))
    log.append(EVENT_ASSISTANT_MESSAGE_ID, message_id="mid-1")

    derived = log.derive_messages()

    assert derived[0]["content"] == "在"
    assert derived[0]["message_id"] == "mid-1"


def test_surface_eligible_matches_the_documented_set():
    assert SURFACE_ELIGIBLE == {"user/message", "assistant/message"}
