"""迁移脚本（P4 第 4 步）：旧会话历史 → 事件日志。

三件必须证明的事：

1. **等价**：迁移后 `derive_messages()` 与原 `messages` 逐条相等（这是"单一真相"的前提）；
2. **幂等**：重复跑不会产生重复事件，也不会覆盖已迁移/已新增的会话；
3. **安全**：`--dry-run` 不写库；真写前有备份。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import migrate_session_events as mig  # noqa: E402

from app.agent.session.session_log import SessionLog  # noqa: E402


def _make_db(path: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            bot_id TEXT NOT NULL, task_id TEXT NOT NULL, conv_id TEXT NOT NULL,
            session_id TEXT NOT NULL, type TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '', saved_at INTEGER NOT NULL,
            messages TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (bot_id, task_id)
        );
        """
    )
    for row in rows:
        conn.execute(
            "INSERT INTO conversations (bot_id, task_id, conv_id, session_id, type, title, saved_at, messages)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                row.get("bot_id", "1"), row["task_id"], row["task_id"], "group_1", "group",
                "", 1788342260, json.dumps(row["messages"], ensure_ascii=False),
            ),
        )
    conn.commit()
    conn.close()


def _store(db: Path):
    from app.agent.session.history import SQLiteHistoryStore

    return SQLiteHistoryStore("1", db_path=str(db))


@pytest.fixture()
def db(tmp_path) -> Path:
    path = tmp_path / "history.db"
    _make_db(path, [
        {"task_id": "t1", "messages": [
            {"role": "user", "time": 1, "content": "你好"},
            {"role": "assistant", "time": 2, "content": "在的"},
        ]},
        {"task_id": "t2", "messages": []},
    ])
    return path


def test_dry_run_writes_nothing(db):
    stats = mig.migrate_db(db, dry_run=True)

    assert stats["migrated"] == 1 and stats["skipped"] == 1
    assert stats["messages"] == 2
    assert stats["backup"] == ""
    assert _store(db).load_events("t1") == []


def test_migration_is_equivalent_to_the_old_messages(db):
    mig.migrate_db(db)

    derived = SessionLog(_store(db), "t1").derive_messages()

    assert [m["content"] for m in derived] == ["你好", "在的"]
    assert [m["role"] for m in derived] == ["user", "assistant"]


def test_migration_is_idempotent(db):
    first = mig.migrate_db(db)
    second = mig.migrate_db(db)

    assert first["migrated"] == 1
    assert second["migrated"] == 0 and second["skipped"] == 2
    assert len(_store(db).load_events("t1")) == 2, "重复跑不许产生重复事件"


def test_migration_keeps_events_appended_afterwards(db):
    """迁移后再有新消息：seq 从迁移后的最大值继续，不撞主键。"""
    mig.migrate_db(db)
    store = _store(db)
    log = SessionLog(store, "t1")

    appended = log.append("user/message", entry={"role": "user", "content": "后来的"})

    assert appended["seq"] == 3
    assert [m["content"] for m in log.derive_messages()] == ["你好", "在的", "后来的"]


def test_migration_backs_up_before_writing(db):
    stats = mig.migrate_db(db)

    assert stats["backup"] and Path(stats["backup"]).is_file()
