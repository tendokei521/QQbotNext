"""P4 第 2 步：每个历史写入口都走事件日志，且派生结果与内存历史一致。

三个硬约束（计划点名）：

1. `add_message` 之后：事件日志里能读出同样的事实，`derive_messages()` 与
   `session.data.history` 逐条相等（双写期间两者不许漂移）；
2. 流式 upsert 仍是"一条发言"，`len(get_history)` 与改造前一致（不因逐句 delta 变多）；
3. 句柄回填只补 `message_id`，不改正文；存储层没有 `append_event`（旧替身）时不炸。
"""

from __future__ import annotations

from app.agent.session.manager import SessionManager


def _mgr(tmp_path, bot_id=778) -> SessionManager:
    mgr = SessionManager(str(bot_id))
    mgr.history.history_dir = str(tmp_path / "history")
    return mgr


def _events(mgr: SessionManager, session_id: str) -> list[dict]:
    session = mgr.get_session(session_id)
    return mgr.history.load_events(session.task_id)


def _derived(mgr: SessionManager, session_id: str) -> list[dict]:
    from app.agent.session.session_log import SessionLog

    session = mgr.get_session(session_id)
    return SessionLog(mgr.history, session.task_id).derive_messages()


def test_add_message_writes_events_and_stays_in_sync(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.create_session("group_1", "group", 60)

    mgr.add_message("group_1", "user", "你好", "1001", nickname="小明")
    mgr.add_message("group_1", "assistant", "在的")

    events = _events(mgr, "group_1")
    assert [e["type"] for e in events] == ["user/message", "assistant/message"]
    assert [e["seq"] for e in events] == [1, 2]

    derived = _derived(mgr, "group_1")
    session = mgr.get_session("group_1")
    assert [m["content"] for m in derived] == ["你好", "在的"]
    assert derived == session.data.history, "双写期间派生视图必须与内存历史逐条相等"


def test_streaming_upsert_is_still_one_message(tmp_path):
    """计划里的硬约束：流式逐句更新后 `len(get_history)` 仍等于改造前的值。"""
    mgr = _mgr(tmp_path)
    mgr.create_session("group_1", "group", 60)
    mgr.add_message("group_1", "user", "讲个故事")

    assert mgr.upsert_streaming_assistant("group_1", "从前")
    assert mgr.upsert_streaming_assistant("group_1", "从前有座山")

    assert len(mgr.get_history("group_1")) == 2, "user + 一条 assistant，不是三条"
    deltas = [e for e in _events(mgr, "group_1") if e["type"] == "assistant/delta"]
    assert len(deltas) == 2, "两次更新各有一条 delta 事件（日志不丢事实）"
    assert deltas[0]["payload"]["entry_id"] == deltas[1]["payload"]["entry_id"]
    assert _derived(mgr, "group_1")[-1]["content"] == "从前有座山"

    mgr.end_streaming_assistant("group_1")
    assert mgr.upsert_streaming_assistant("group_1", "下一轮")
    assert len(mgr.get_history("group_1")) == 3, "结束一轮后新发言是新条目"


def test_mark_message_id_does_not_touch_the_body(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.create_session("group_1", "group", 60)
    mgr.add_message("group_1", "assistant", "在的")

    assert mgr.mark_last_assistant_message_id("group_1", "mid-1") is True

    history = mgr.get_history("group_1")
    assert history[-1]["content"] == "在的"
    assert history[-1]["message_id"] == "mid-1"
    assert _derived(mgr, "group_1")[-1]["message_id"] == "mid-1"
    # 重复回填同一个 id 不再写
    assert mgr.mark_last_assistant_message_id("group_1", "mid-1") is False


def test_survives_a_history_store_without_event_support(tmp_path):
    """旧替身（JSON HistoryManager / 测试 stub）没有 append_event：行为和以前一样。"""
    mgr = _mgr(tmp_path)
    mgr.create_session("group_1", "group", 60)

    class _Legacy:
        def save_session(self, session):
            return None

        def save_conversation(self, session, conv, saved_at=None):
            return None

    mgr.history = _Legacy()

    mgr.add_message("group_1", "user", "还能写")
    assert [m["content"] for m in mgr.get_history("group_1")] == ["还能写"]
