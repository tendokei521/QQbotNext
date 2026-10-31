"""记忆存储层契约测试（v3）：旧库迁移 / 去重与失效 / 淘汰 / 语料缓冲。

只测对外行为，不锁内部实现细节（见 docs/TEST_POLICY.md）。
"""

from __future__ import annotations

import sqlite3

from app.plugins.llm_memory.src.store import MemoryStore


def _store(tmp_path, bot_id: str = "bot1") -> MemoryStore:
    return MemoryStore(bot_id, db_path=str(tmp_path / "memory.db"))


def _old_v2_database(path) -> None:
    """造一个 v2 结构的旧库（缺少 v3 新增列），用来验证增量迁移不丢数据。"""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, owner TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'fact', content TEXT NOT NULL,
            keywords TEXT NOT NULL DEFAULT '', importance REAL NOT NULL DEFAULT 0.5,
            visibility TEXT NOT NULL DEFAULT 'group', source TEXT NOT NULL DEFAULT 'tool',
            source_user TEXT NOT NULL DEFAULT '', source_task TEXT NOT NULL DEFAULT '',
            embedding BLOB, status TEXT NOT NULL DEFAULT 'active',
            confidence REAL NOT NULL DEFAULT 0.5, confirmed INTEGER NOT NULL DEFAULT 0,
            expires_at INTEGER, evidence_count INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            hit_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "INSERT INTO memories (id, bot_id, owner, content, created_at, updated_at)"
        " VALUES ('old1', 'bot1', 'user_1', '旧记忆：小明喜欢美式', 100, 100)"
    )
    conn.commit()
    conn.close()


def test_v2_database_migrates_without_data_loss(tmp_path):
    path = tmp_path / "old.db"
    _old_v2_database(path)

    store = MemoryStore("bot1", db_path=str(path))
    row = store.get("old1")
    assert row is not None and row["content"] == "旧记忆：小明喜欢美式"
    # v3 新列补齐，且 valid_from 回填为创建时间（时间有效性语义）
    assert int(row["valid_from"]) == 100
    assert store.list_episodes("private_1") == []
    store.close()

    # 再次打开幂等（不重复迁移、数据仍在）
    reopened = MemoryStore("bot1", db_path=str(path))
    assert reopened.get("old1") is not None
    reopened.close()


def test_exact_duplicate_updates_evidence(tmp_path):
    store = _store(tmp_path)
    first = store.upsert_fact("我养了一只叫咪咪的猫", "user_1", importance=0.5)
    second = store.upsert_fact("我养了一只叫咪咪的猫", "user_1", importance=0.9)

    assert first.id == second.id
    assert second.action == "update"
    row = store.get(first.id)
    assert row["importance"] == 0.9
    assert int(row["evidence_count"]) == 2
    assert store.count_by_owner("user_1") == 1
    store.close()


def test_negated_fact_supersedes_instead_of_merging(tmp_path):
    """曾坏过：裸子串 + 0.85 相似合并会把「不喜欢」合并进「喜欢」并静默翻转。"""
    store = _store(tmp_path)
    old = store.upsert_fact("我喜欢喝美式", "user_1", source="tool", supersede_conflicts=True)
    new = store.upsert_fact(
        "我不喜欢喝美式，喝不惯", "user_1", source="tool", supersede_conflicts=True
    )

    assert old.id != new.id
    assert store.get(old.id)["status"] == "superseded"
    assert store.get(old.id)["invalid_at"] is not None
    assert store.get(new.id)["status"] == "active"
    assert store.count_by_owner("user_1") == 2
    store.close()


def test_negative_is_not_resurrected(tmp_path):
    store = _store(tmp_path)
    fact = store.upsert_fact("我喜欢喝美式", "user_1", source="tool")
    assert store.deny("user_1", "美式") == 1
    assert store.get(fact.id)["status"] == "negative"

    # 同一措辞再次出现：只累计证据，不能把用户否认过的记忆复活
    store.upsert_fact("我喜欢喝美式", "user_1", source="tool")
    assert store.get(fact.id)["status"] == "negative"
    store.close()


def test_enforce_limit_keeps_important_rows(tmp_path):
    store = _store(tmp_path)
    for index, importance in enumerate((0.1, 0.2, 0.5, 0.8, 0.9)):
        store.upsert_fact(f"事实 {index}", "user_1", importance=importance)

    removed = store.enforce_limit("user_1", 2)
    assert removed == 3
    rows = store.list_by_owner("user_1")
    assert {row["content"] for row in rows} == {"事实 3", "事实 4"}
    store.close()


def test_episode_buffer_dedupes_and_marks(tmp_path):
    store = _store(tmp_path)
    first = store.add_episode("private_5", "user_5", "我喜欢美式", user_id="5", message_id="m1")
    duplicate = store.add_episode("private_5", "user_5", "我喜欢美式", user_id="5", message_id="m1")
    assert first and duplicate in (first, "")
    assert len(store.list_episodes("private_5")) == 1

    rows = store.list_episodes("private_5")
    assert rows[0]["owner"] == "user_5" and rows[0]["user_id"] == "5"
    assert store.mark_episodes_consolidated([rows[0]["id"]]) == 1
    assert store.list_episodes("private_5") == []
    store.close()
