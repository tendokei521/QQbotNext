"""S2 置信度与权威降级注入测试：评分融合 / min_confidence / 超龄 / 过期 / 重置线 / 试探渲染。"""

import time

from app.llm.config import DEFAULT_LLM_CONFIG
from app.llm.memory.manager import MemoryManager
from app.llm.memory.recall import effective_confidence, rank, render_block, score_row
from app.llm.memory.store import MemoryStore, owner_private


def _store(bot_id="bots2"):
    s = MemoryStore(bot_id)
    return s


def _mkmanager(bot_id="bots2m", data=None):
    class Cfg:
        def __init__(self, d):
            self.data = dict(DEFAULT_LLM_CONFIG)
            self.data.update(d or {})

        def get(self, k, default=None):
            return self.data[k] if k in self.data else default

    class RT:
        def __init__(self):
            self.bot_id = bot_id
            self.config = Cfg(data)

    rt = RT()
    rt.memory = MemoryManager(rt)
    return rt.memory


def test_score_row_confidence_and_evidence():
    now = int(time.time())
    base = {"confidence": 0.5, "confirmed": 0, "evidence_count": 1, "importance": 0.5,
            "updated_at": now, "content": "x", "keywords": ""}
    s0 = score_row({**base}, [], set(), None, now)
    s_confirm = score_row({**base, "confirmed": 1}, [], set(), None, now)
    s_ev = score_row({**base, "evidence_count": 5}, [], set(), None, now)
    assert s_confirm > s0
    assert s_ev > s0


def test_rank_min_confidence():
    st = _store()
    low = st.upsert_fact("我提过喜欢苏打水", owner_private(5), confidence=0.4)
    high = st.upsert_fact("我叫小明", owner_private(5), confidence=0.8)
    _ = low
    # 不设阈值 → 都命中（min=0）
    hits_all = rank(st, owners=[owner_private(5)], query="", min_confidence=0.0)
    assert len(hits_all) >= 2
    # min=0.5 → 低置信被过滤
    hits = rank(st, owners=[owner_private(5)], query="", min_confidence=0.5)
    assert all(h["id"] != low for h in hits)
    assert any(h["id"] == high for h in hits)
    # 被点名（mention）→ 低置信也上浮供核对
    hits_mention = rank(
        st, owners=[owner_private(5)], query="苏打水", min_confidence=0.5,
        mention_owners=[owner_private(5)],
    )
    assert any(h["id"] == low for h in hits_mention)
    st.close()


def test_rank_age_and_expiry():
    st = _store()
    mid_old = st.upsert_fact("我半年前说过X", owner_private(5), confidence=0.8)
    st._execute("UPDATE memories SET updated_at=? WHERE id=?",
                (int(time.time()) - 400 * 86400, mid_old))
    mid_fresh = st.upsert_fact("我最近说过Y", owner_private(5), confidence=0.8)
    # 超龄过滤（默认非确认）
    hits = rank(st, owners=[owner_private(5)], query="", max_age_days=180)
    assert all(h["id"] != mid_old for h in hits)
    assert any(h["id"] == mid_fresh for h in hits)
    # 被确认的超龄仍注入
    st.confirm(owner_private(5), "X")
    hits2 = rank(st, owners=[owner_private(5)], query="", max_age_days=180)
    assert any(h["id"] == mid_old for h in hits2)
    # 过期（expires_at）
    mid_exp = st.upsert_fact("一次性事件", owner_private(5), confidence=0.8,
                             expires_at=int(time.time()) - 10)
    hits3 = rank(st, owners=[owner_private(5)], query="", max_age_days=0)
    assert all(h["id"] != mid_exp for h in hits3)
    st.close()


def test_rank_owner_reset_suspend():
    st = _store()
    auto = st.upsert_fact("自动攒的旧话题", owner_private(5), confidence=0.55, source="extract")
    saved = st.upsert_fact("我叫小明", owner_private(5), confidence=0.8, source="deterministic")
    st._execute("UPDATE memories SET updated_at=? WHERE id IN (?,?)",
                (int(time.time()) - 1000, auto, saved))
    st.set_reset(owner_private(5))  # 会话已重置
    resets = {owner_private(5): st.get_reset(owner_private(5))}
    # suspend：自动型挂起、保存型保留
    hits = rank(st, owners=[owner_private(5)], query="", owner_resets=resets,
                keep_saved_before_reset=True)
    assert all(h["id"] != auto for h in hits)
    assert any(h["id"] == saved for h in hits)
    # keep_saved=False → 全部挂起
    hits_all = rank(st, owners=[owner_private(5)], query="", owner_resets=resets,
                    keep_saved_before_reset=False)
    assert all(h["id"] not in (auto, saved) for h in hits_all)
    st.close()


def test_render_block_hedge_by_confidence():
    now = int(time.time())
    rows = [
        {"content": "高置信我喜欢美式", "confidence": 0.9, "updated_at": now},
        {"content": "中置信我喜欢拿铁", "confidence": 0.6, "updated_at": now},
        {"content": "低置信我喜欢苏打", "confidence": 0.4, "updated_at": now},
    ]
    block = render_block(rows, hedge=True)
    assert "高置信我喜欢美式" in block and "（好像）" not in block.split("高置信")[0]
    # 高置信无前缀；中/低有试探前缀
    assert "\n- 高置信我喜欢美式" in block
    assert "\n- （好像）中置信我喜欢拿铁" in block
    assert "\n- （记不太清）低置信我喜欢苏打" in block
    raw = render_block(rows, hedge=False)
    assert "（好像）" not in raw and "（记不太清）" not in raw


def test_effective_confidence_clamped():
    assert effective_confidence({"confidence": 1.5}) == 1.0
    assert effective_confidence({"confidence": -0.2}) == 0.0
    assert effective_confidence({}) == 0.5


def test_manager_injection_filters_low_confidence(monkeypatch):
    mgr = _mkmanager(bot_id="bots2m1")  # memory_min_confidence=0.5
    mgr.store.upsert_fact("低置信旧事", owner_private(5), confidence=0.3, source="extract")
    block = mgr.recall_block("private_5", user_id="5")
    assert block == ""
    mgr2 = _mkmanager(bot_id="bots2m2", data={"memory_min_confidence": 0.2})
    mgr2.store.upsert_fact("低置信旧事", owner_private(5), confidence=0.3, source="extract")
    assert "低置信旧事" in mgr2.recall_block("private_5", user_id="5")
    # 试探语气出现在注入块
    mgr3 = _mkmanager(bot_id="bots2m3")
    mgr3.store.upsert_fact("我提过喜欢拿铁", owner_private(5), confidence=0.55, source="extract")
    b = mgr3.recall_block("private_5", user_id="5")
    assert "（好像）" in b
