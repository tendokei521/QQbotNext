"""S1 存储层 v2 测试：状态/置信度/矛盾检测/近义合并/correct/deny/confirm/reset。"""

import pytest

from app.llm.memory.store import (
    MemoryStore,
    has_negation_conflict,
    owner_private,
    text_similarity,
)


def _store(bot_id="bots1"):
    return MemoryStore(bot_id)


def test_new_columns_and_defaults():
    s = _store()
    mid = s.upsert_fact("我喜欢喝美式", owner_private(5))
    row = s.get(mid)
    assert row["status"] == "active"
    assert row["confidence"] == 0.5
    assert row["confirmed"] == 0
    assert row["evidence_count"] == 1
    assert row["expires_at"] is None
    s.close()


def test_legacy_migration_columns_exist():
    """表结构含 v2 列（老库 ALT ER 迁移路径：直接验列存在）。"""
    s = _store()
    cols = {r["name"] for r in s._fetch("PRAGMA table_info(memories)")}
    assert {"status", "confidence", "confirmed", "expires_at", "evidence_count"} <= cols
    owners = {r["name"] for r in s._fetch("PRAGMA table_info(memory_owners)")}
    assert {"bot_id", "owner", "last_reset_at"} <= owners
    s.close()


def test_evidence_accumulates_on_resay():
    s = _store()
    a = s.upsert_fact("我住上海", owner_private(5), confidence=0.5)
    b = s.upsert_fact("我住上海", owner_private(5), confidence=0.8)
    assert a == b
    row = s.get(a)
    assert row["evidence_count"] == 2
    assert row["confidence"] == 0.8  # 取 max
    s.close()


def test_negation_conflict_autosupersede():
    s = _store()
    old = s.upsert_fact("我喜欢喝美式", owner_private(5), supersede_conflicts=True)
    new = s.upsert_fact("我不喜欢喝美式", owner_private(5), supersede_conflicts=True)
    assert old != new
    assert s.get(old)["status"] == "superseded"
    assert s.get(new)["status"] == "active"
    assert s.count_active_by_owner(owner_private(5)) == 1
    assert s.find_conflicts(owner_private(5), "我不喜欢喝美式") == []
    s.close()


def test_near_dupe_merge_keeps_id():
    s = _store()
    a = s.upsert_fact("我喜欢喝美式", owner_private(5))
    b = s.upsert_fact("我喜欢喝美式咖啡", owner_private(5))
    assert a == b
    row = s.get(a)
    assert row["content"] == "我喜欢喝美式咖啡"  # 换成最新措辞
    assert s.count_by_owner(owner_private(5)) == 1
    s.close()


def test_deny_confirm_supersede_status_machine():
    s = _store()
    mid = s.upsert_fact("我喜欢喝美式", owner_private(5))
    # deny → negative，默认查询不返回
    assert s.deny(owner_private(5), "美式") == 1
    assert s.get(mid)["status"] == "negative"
    assert s.list_by_owner(owner_private(5)) == []
    assert s.list_by_owner(owner_private(5), include_all=True), "include_all 可见"
    # confirm（对仍 active 的一条）
    mid2 = s.upsert_fact("我喜欢拿铁", owner_private(5))
    assert s.confirm(owner_private(5), "拿铁") == 1
    row = s.get(mid2)
    assert row["confirmed"] == 1
    assert row["confidence"] > 0.5
    # supersede
    assert s.supersede(owner_private(5), "拿铁") == 1
    assert s.get(mid2)["status"] == "superseded"
    s.close()


def test_correct_replaces_old():
    s = _store()
    s.upsert_fact("我喜欢喝美式", owner_private(5))
    mid_new = s.correct(owner_private(5), "美式", "我喝不惯美式", source_user="5")
    assert mid_new
    active = s.list_by_owner(owner_private(5))
    assert len(active) == 1
    assert active[0]["content"] == "我喝不惯美式"
    assert active[0]["source"] == "correct"
    assert s.count_active_by_owner(owner_private(5)) == 1
    s.close()


def test_reset_line_per_owner():
    s = _store()
    assert s.get_reset(owner_private(5)) == 0
    s.set_reset(owner_private(5))
    r5 = s.get_reset(owner_private(5))
    assert r5 > 0
    # 其它 owner 不受影响
    assert s.get_reset(owner_private(6)) == 0
    # 再次 set 单调递增
    import time
    time.sleep(0.02)
    s.set_reset(owner_private(5))
    assert s.get_reset(owner_private(5)) >= r5
    s.close()


def test_heuristics():
    assert text_similarity("我喜欢喝美式", "我喜欢喝美式咖啡") >= 0.85
    assert has_negation_conflict("我喜欢喝美式", "我喝不惯美式") is True
    assert has_negation_conflict("我喜欢喝美式", "我也喜欢喝美式") is False
    assert has_negation_conflict("我住在上海", "我搬去北京了") is True
