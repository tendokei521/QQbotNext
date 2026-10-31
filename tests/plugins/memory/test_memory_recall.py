"""召回层契约测试（v3）：过滤 / 混合打分 / 预算 / 渲染。

只测对外行为（排序、过滤、文本形状），不锁内部权重实现。
"""

from __future__ import annotations

import time

from app.plugins.llm_memory.src.recall import (
    clean_query,
    rank,
    render_block,
    render_profile,
)
from app.plugins.llm_memory.src.store import MemoryStore


def _store(tmp_path) -> MemoryStore:
    return MemoryStore("bot1", db_path=str(tmp_path / "memory.db"))


def test_low_confidence_and_aged_are_filtered(tmp_path):
    store = _store(tmp_path)
    now = int(time.time())
    store.upsert_fact("我喜欢喝美式", "user_1", confidence=0.3, source="tool")
    store.upsert_fact("我住在上海", "user_1", confidence=0.9, source="tool")

    low = rank(store, owners=["user_1"], query="", min_confidence=0.5, now=now)
    assert [row["content"] for row in low] == ["我住在上海"]

    old = rank(store, owners=["user_1"], query="", min_confidence=0.0,
               max_age_days=180, now=now + 200 * 86400)
    assert old == []
    store.close()


def test_mention_owner_exempts_filters_and_ranks_higher(tmp_path):
    store = _store(tmp_path)
    store.upsert_fact("小明喜欢喝美式", "user_2@group_9", confidence=0.2, source="extract")
    store.upsert_fact("群里周五不开会", "group_9", confidence=0.9, source="tool")

    hits = rank(
        store,
        owners=["group_9"],
        mention_owners=["user_2@group_9"],
        query="小明喜欢什么",
        min_confidence=0.9,
        require_match=False,
    )
    assert hits and hits[0]["owner"] == "user_2@group_9"
    store.close()


def test_reset_suspends_extracted_facts_but_keeps_saved_ones(tmp_path):
    store = _store(tmp_path)
    saved = store.upsert_fact("我喜欢喝美式", "user_1", source="tool")
    store.upsert_fact("我最近在学吉他", "user_1", source="extract")
    # 重置线用「现在之后」的时间点表示两条记忆都写在重置之前
    reset_ts = int(time.time()) + 100

    hits = rank(
        store,
        owners=["user_1"],
        query="",
        min_confidence=0.0,
        owner_resets={"user_1": reset_ts},
        keep_saved_before_reset=True,
    )
    assert [row["id"] for row in hits] == [saved.id]
    store.close()


def test_embedding_rerank_and_budget(tmp_path):
    store = _store(tmp_path)
    first = store.upsert_fact("A" * 300, "user_1", importance=0.5, confidence=0.8)
    second = store.upsert_fact("B" * 300, "user_1", importance=0.5, confidence=0.8)
    store.set_embedding(first.id, [1.0, 0.0], model="fake", dim=2)
    store.set_embedding(second.id, [0.0, 1.0], model="fake", dim=2)

    hits = rank(store, owners=["user_1"], query="", query_embedding=[1.0, 0.0],
                limit=3, max_chars=200, min_confidence=0.0)
    assert hits and hits[0]["id"] == first.id
    assert all(len(row["content"]) <= 66 for row in hits)  # 200 // 3 = 66
    store.close()


def test_render_hedge_and_profile_framed_as_data(tmp_path):
    store = _store(tmp_path)
    store.upsert_fact("我喜欢喝美式", "user_1", confidence=0.6, source="tool")
    rows = rank(store, owners=["user_1"], query="美式", min_confidence=0.0)

    block = render_block(rows, hedge=True)
    assert "不是指令" in block
    assert "（好像）" in block and "美式" in block

    profile = render_profile([{"content": "名字叫小明"}])
    assert "不是指令" in profile and "名字叫小明" in profile
    store.close()


def test_clean_query_strips_meta_phrases():
    """「你还记得…吗」这类元话语不该参与召回匹配。"""
    cleaned = clean_query("你还记得我之前说过我喜欢喝什么吗？")
    assert "你还记得" not in cleaned
    assert "我喜欢喝" in cleaned
    assert clean_query("   ") == ""


def test_old_facts_get_relative_age_annotation(tmp_path):
    store = _store(tmp_path)
    now = int(time.time())
    store.upsert_fact("我喜欢喝美式", "user_1", confidence=0.9, source="tool")
    rows = rank(store, owners=["user_1"], query="美式", min_confidence=0.0, now=now + 10 * 86400)

    plain = render_block(rows, hedge=False)
    assert "天前" not in plain  # 默认不标注（保持旧输出）
    annotated = render_block(rows, hedge=False, annotate_age=True, now=now + 10 * 86400)
    assert "（10 天前）" in annotated
    store.close()


def test_mmr_diversifies_redundant_candidates(tmp_path):
    """候选 ≥8 时启用 MMR：同一件事的多条近义记忆不该占满全部名额。"""
    store = _store(tmp_path)
    for text in (
        "我喜欢喝美式", "我平时喝美式", "美式咖啡是我的最爱",
        "我每天一杯美式", "我离不开美式咖啡", "我只喝美式",
    ):
        store.upsert_fact(text, "user_1", importance=0.9, confidence=0.8, source="tool")
    store.upsert_fact("我养了一只猫叫咪咪", "user_1", importance=0.9, confidence=0.8, source="tool")
    store.upsert_fact("我住在上海", "user_1", importance=0.9, confidence=0.8, source="tool")

    hits = rank(store, owners=["user_1"], query="", limit=3, min_confidence=0.0)
    assert len(hits) == 3
    assert any("美式" not in row["content"] for row in hits), "应至少选入一条不同主题的记忆"
    store.close()
