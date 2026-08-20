"""P0 存储层测试：建表 / CRUD / owner 隔离 / 去重 / 淘汰 / 审计 / bot 隔离。"""

import os

import pytest

from app.llm import llm_data_dir
from app.llm.memory.store import (
    MemoryStore,
    owner_group,
    owner_group_member,
    owner_private,
    session_owner,
)


def _store(bot_id="bot1", **kw) -> MemoryStore:
    return MemoryStore(bot_id, **kw)


def test_init_creates_db_and_tables():
    s = _store()
    assert os.path.exists(s.db_path)
    tables = {r["name"] for r in s._fetch("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"memories", "memory_events"} <= tables
    s.close()


def test_db_path_is_per_bot_isolated():
    a = _store("botA")
    b = _store("botB")
    assert a.bot_id == "botA"
    assert b.bot_id == "botB"
    assert a.db_path != b.db_path
    assert "botA" in a.db_path and "botB" in b.db_path
    # 存在同 owner 记忆互不可见（bot 级隔离）
    a.upsert_fact("小明喜欢美式", "user_1", source="tool")
    assert b.count_by_owner("user_1") == 0
    assert a.count_by_owner("user_1") == 1
    a.close()
    b.close()


def test_owner_helpers():
    assert owner_private(10001) == "user_10001"
    assert owner_group(555) == "group_555"
    assert owner_group_member(555, 10001) == "user_10001@group_555"
    assert session_owner("private_10001") == "user_10001"
    assert session_owner("group_555") == "group_555"
    assert session_owner("group_555", user_id=10001) == "user_10001@group_555"
    # 非法字符 sanitize
    assert owner_private("100 01") == "user_100_01"


def test_add_get_and_owner_isolation():
    s = _store()
    mid = s.upsert_fact("我住上海", owner="user_1", source_user="1", keywords="上海 住")
    assert mid
    got = s.get(mid)
    assert got["content"] == "我住上海"
    assert got["owner"] == "user_1"
    # 其它 owner 不可见（isolation）
    assert s.get_owned(mid, "group_999") is None
    assert s.get_owned(mid, "user_1") is not None
    # 空内容拒绝
    with pytest.raises(ValueError):
        s.upsert_fact("  ", "user_1")
    s.close()


def test_upsert_dedupe():
    s = _store()
    a = s.upsert_fact("我养了一只叫咪咪的猫", "user_1")
    b = s.upsert_fact("我养了一只叫咪咪的猫", "user_1", importance=0.9)
    assert a == b  # 同 owner 同内容 → 同一 id
    assert s.count_by_owner("user_1") == 1
    assert s.get(a)["importance"] == pytest.approx(0.9)
    # 不同内容 → 不同条
    c = s.upsert_fact("我喜欢喝美式", "user_1")
    assert c != a
    assert s.count_by_owner("user_1") == 2
    s.close()


def test_list_by_owner_and_search():
    s = _store()
    s.upsert_fact("小明喜欢喝美式咖啡", "user_2@group_9", keywords="小明 咖啡 美式")
    s.upsert_fact("小红讨厌香菜", "user_3@group_9", keywords="小红 香菜")
    s.upsert_fact("群约定：周五不开会", "group_9")
    rows = s.list_by_owner("user_2@group_9")
    assert len(rows) == 1 and rows[0]["content"] == "小明喜欢喝美式咖啡"
    # 跨 owner 搜索
    hits = s.search_in_owners(["user_2@group_9", "user_3@group_9", "group_9"], "小明")
    assert len(hits) == 1 and "美式" in hits[0]["content"]
    hits2 = s.search_in_owners(["user_2@group_9", "group_9"], "开会")
    assert len(hits2) == 1 and hits2[0]["owner"] == "group_9"
    s.close()


def test_delete_clear_count():
    s = _store()
    a = s.upsert_fact("A", "user_1")
    s.upsert_fact("B带词", "user_1")
    s.upsert_fact("C", "group_2")
    assert s.delete_fact(a, owner="user_1") is True
    assert s.delete_fact(a, owner="group_2") is False  # owner 不匹配
    assert s.count_by_owner("user_1") == 1
    # delete_by_query
    removed = s.delete_by_query("user_1", "带词")
    assert removed == 1
    assert s.count_by_owner("user_1") == 0
    assert s.clear("group_2") == 1
    s.close()


def test_enforce_limit_evicts_low_priority():
    s = _store()
    for i in range(10):
        s.upsert_fact(f"事实{i}", "user_1", importance=0.1 if i % 2 else 0.9)
    removed = s.enforce_limit("user_1", max_per_owner=3)
    assert removed == 7
    assert s.count_by_owner("user_1") == 3
    remains = s.list_by_owner("user_1")
    assert {r["importance"] for r in remains} == {0.9}  # 高重要度保留
    s.close()


def test_audit_and_recent():
    s = _store()
    s.audit("write", owner="user_1", user_id="1", summary="我住上海", source="tool")
    s.audit("delete", owner="user_1", user_id="1", summary="忘了那条", source="chat")
    rows = s.recent_audit(owner="user_1")
    assert len(rows) == 2
    assert rows[1]["action"] == "write"  # 时间倒序
    assert rows[0]["action"] == "delete"
    assert "忘了" in rows[0]["summary"]
    all_rows = s.recent_audit()
    assert len(all_rows) == 2
    s.close()


def test_stats():
    s = _store()
    assert s.stats() == {"memories": 0, "owners": 0, "events": 0}
    s.upsert_fact("x", "user_1")
    s.upsert_fact("y", "user_2")
    s.audit("write", owner="user_1")
    st = s.stats()
    assert st["memories"] == 2 and st["owners"] == 2 and st["events"] == 1
    s.close()
