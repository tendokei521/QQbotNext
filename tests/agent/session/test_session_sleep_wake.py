"""会话超时后的唤醒契约：复用内存快照，不再「创建会话 → 重读归档」。

历史问题：``get_session`` 在会话超时后直接丢对象，下一条消息走
``get_or_create_session`` 的 create + restore 分支，于是每个间隔超过
``session_timeout`` 的会话都打一遍「创建会话 / 归档恢复」，且多读一次磁盘。
"""

from __future__ import annotations

import time

from app.agent.session.manager import SessionManager

BOT_ID = "session-sleep-wake-test"


def _fresh_manager() -> SessionManager:
    manager = SessionManager(BOT_ID)
    with manager.lock:
        manager.sessions.clear()
        manager._sleeping.clear()
    return manager


def test_expired_session_reuses_memory_snapshot_on_next_message():
    manager = _fresh_manager()
    try:
        session = manager.create_session("group_G", "group", timeout=5)
        session.add_participant("u1")
        session.config.last_time = int(time.time()) - 60  # 已超时

        revived = manager.get_session("group_G")

        assert revived is session, "超时后应复用内存快照，而不是新建会话"
        assert manager.sessions["group_G"] is session
        assert "group_G" not in manager._sleeping
        assert revived.participants == set(), "唤醒等同重开会话：参与者清空"
        assert revived.is_alive()
    finally:
        SessionManager.evict(BOT_ID)


def test_cleanup_expired_keeps_snapshot_and_destroy_clears_it():
    manager = _fresh_manager()
    try:
        session = manager.create_session("group_G", "group", timeout=5)
        session.config.last_time = int(time.time()) - 60
        manager._cleanup_expired()

        assert "group_G" not in manager.sessions
        assert manager._sleeping.get("group_G") is session

        manager.destroy_session("group_G")
        assert "group_G" not in manager._sleeping, "销毁会话后不能被快照复活"
    finally:
        SessionManager.evict(BOT_ID)
