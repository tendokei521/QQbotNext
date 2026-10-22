"""S5-S7 测试：查询（桶/补零/top-N/summary/筛选）、facets、purge API。"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.usage import query as stat_query
from app.agent.usage.recorder import StatRecorder, set_recorder
from app.infrastructure.persistence.database import Database
from app.webui.api import stats as stats_router


def _day(offset: int = 0) -> int:
    """今天（本地）往前 offset 天的 0 点（UTC 秒，按本地时区换算）。"""
    tm = time.localtime(time.time() - offset * 86400)
    return int(time.mktime((tm.tm_year, tm.tm_mon, tm.tm_mday, 0, 0, 0, 0, 0, -1)))


@pytest.fixture
async def env(tmp_path):
    db = Database(tmp_path / "q.db")
    await db.connect()
    rec = StatRecorder(db, flush_max=1000, flush_interval=0)
    set_recorder(rec)

    # 造数据：3 天 × 2 模型
    rows = [
        (2, "deepseek-chat", 100, 50, 30, 70, "chat", "99", "group_1"),
        (2, "gpt-4o", 20, 10, 0, 20, "llm_proactive", "99", "group_1"),
        (1, "deepseek-chat", 200, 80, 120, 80, "chat", "99", "group_2"),
        (0, "deepseek-chat", 300, 120, 200, 100, "llm_schedule", "99", "group_2"),
    ]
    for offset, model, inp, out, hit, miss, source, bot, session in rows:
        rec.record_llm(
            usage={
                "prompt_tokens": inp,
                "completion_tokens": out,
                "prompt_cache_hit_tokens": hit,
                "prompt_cache_miss_tokens": miss,
            },
            bot_id=bot,
            source=source,
            api="deepseek" if "deepseek" in model else "openai",
            model=model,
            session_id=session,
            ts=_day(offset) + 43200,
        )
    # 入站消息：一条 llm 响应、一条 ignored
    rec.record_msg_in(outcome="llm", bot_id="99", session_id="group_1", ts=_day(0) + 43200)
    rec.record_msg_in(outcome="ignored", bot_id="99", session_id="group_1", ts=_day(0) + 43300)
    rec.record_msg_out(segments=2, nbytes=40, bot_id="99", session_id="group_1", ts=_day(0) + 43400)
    await rec.flush()
    yield rec, db
    await rec.close()
    set_recorder(None)
    await db.close()


# ==================== 查询 ====================


async def test_query_skips_empty_buckets_by_default(env):
    """用量表形态（日期做列）：默认必须省略全零时段，否则表格被 0 撑满。"""
    _rec, db = env
    data = await stat_query.query(
        db, kind="llm", metric="tokens", granularity="day", breakdown="model",
        from_ts=_day(3), to_ts=_day(0) + 86400,
    )
    buckets = [b["bucket"] for b in data["buckets"]]
    assert len(buckets) == 3, f"只有 3 天有数据，不该出现空桶：{buckets}"
    assert data["meta"]["hidden_buckets"] == 1
    assert all(b["total"] > 0 for b in data["buckets"])


async def test_query_can_keep_full_axis_for_trends(env):
    """画连续趋势时传 skip_empty=False，保留完整时间轴（空桶补 0）。"""
    _rec, db = env
    data = await stat_query.query(
        db, kind="llm", metric="tokens", granularity="day", breakdown="model",
        from_ts=_day(3), to_ts=_day(0) + 86400, skip_empty=False,
    )
    assert len(data["buckets"]) == 4
    assert data["meta"]["hidden_buckets"] == 0
    assert any(b["total"] == 0 for b in data["buckets"])


async def test_query_summary_is_independent_of_top_n(env):
    _rec, db = env
    data = await stat_query.query(
        db, kind="llm", metric="tokens", granularity="day", breakdown="model",
        from_ts=_day(3), to_ts=_day(0) + 86400,
    )
    bucket_sum = sum(b["total"] for b in data["buckets"])
    assert data["summary"]["tokens"] == bucket_sum
    # 输入输出也应一致
    assert data["summary"]["input"] == 620  # 100+20+200+300
    assert data["summary"]["output"] == 260  # 50+10+80+120


async def test_query_breaks_down_by_source(env):
    _rec, db = env
    data = await stat_query.query(
        db, kind="llm", metric="requests", granularity="month", breakdown="source",
        from_ts=_day(3), to_ts=_day(0) + 86400,
    )
    series = set()
    for b in data["buckets"]:
        series |= set(b["series"])
    assert {"chat", "llm_proactive", "llm_schedule"} <= series


async def test_query_filters_by_model(env):
    _rec, db = env
    data = await stat_query.query(
        db, kind="llm", metric="tokens", granularity="day", breakdown="model",
        from_ts=_day(3), to_ts=_day(0) + 86400, filters={"model": "gpt-4o"},
    )
    assert data["summary"]["tokens"] == 30
    assert data["summary"]["requests"] == 1


async def test_query_filters_by_source_and_bot(env):
    _rec, db = env
    data = await stat_query.query(
        db, kind="llm", metric="tokens", granularity="day", breakdown="none",
        from_ts=_day(3), to_ts=_day(0) + 86400,
        filters={"source": "llm_schedule", "bot_id": "99"},
    )
    assert data["summary"]["tokens"] == 420


async def test_query_cache_rate_and_unknown_cache(env):
    _rec, db = env
    data = await stat_query.query(
        db, kind="llm", metric="tokens", granularity="day", breakdown="none",
        from_ts=_day(3), to_ts=_day(0) + 86400,
    )
    s = data["summary"]
    assert s["cache_hit"] == 350
    assert s["cache_miss"] == 270
    assert s["cache_rate"] == pytest.approx(350 / 620, rel=1e-3)
    assert s["unknown_cache"] == 0


async def test_query_message_kinds_and_outcomes(env):
    _rec, db = env
    data = await stat_query.query(
        db, kind="msg_in", metric="messages", granularity="day", breakdown="outcome",
        from_ts=_day(1), to_ts=_day(0) + 86400,
    )
    s = data["summary"]
    assert s["messages"] == 2
    assert s["triggered"] == 1
    assert s["by_llm"] == 1
    assert s["by_command"] == 0

    out = await stat_query.query(
        db, kind="msg_out", metric="messages", granularity="day", breakdown="none",
        from_ts=_day(1), to_ts=_day(0) + 86400,
    )
    assert out["summary"]["messages"] == 1


async def test_query_unknown_metric_falls_back(env):
    _rec, db = env
    data = await stat_query.query(
        db, kind="llm", metric="不存在的度量", granularity="天", breakdown="xx",
        from_ts=_day(1), to_ts=_day(0) + 86400,
    )
    assert data["meta"]["metric"] == "tokens"
    assert data["meta"]["granularity"] == "day"
    assert data["meta"]["breakdown"] == "model"


async def test_top_n_truncates_series(env):
    rec, db = env
    for i in range(10):
        rec.record_llm(
            usage={"prompt_tokens": 1, "completion_tokens": 1},
            bot_id="99", source="chat", api="x", model=f"m{i}", ts=_day(0) + 5000 + i,
        )
    await rec.flush()
    data = await stat_query.query(
        db, kind="llm", metric="tokens", granularity="day", breakdown="model",
        from_ts=_day(1), to_ts=_day(0) + 86400,
    )
    assert data["meta"]["truncated"] > 0
    assert "other" in data["meta"]["series_order"]
    assert len(data["meta"]["series_order"]) == stat_query.TOP_SERIES + 1


# ==================== facets ====================


async def test_facets_only_list_observed_values(env):
    _rec, db = env
    data = await stat_query.facets(db, kind="llm", from_ts=_day(3), to_ts=_day(0) + 86400)
    models = {item["value"] for item in data["model"]}
    assert models == {"deepseek-chat", "gpt-4o"}
    sources = {item["value"] for item in data["source"]}
    assert sources == {"chat", "llm_proactive", "llm_schedule"}
    # 空值不该出现在下拉里
    assert all(item["value"] for item in data["source"])


async def test_facets_are_not_restricted_by_their_own_filter(env):
    """选 model=gpt-4o 时，model 下拉仍应列出所有模型（否则切不回来）。"""
    _rec, db = env
    data = await stat_query.facets(
        db, kind="llm", from_ts=_day(3), to_ts=_day(0) + 86400, filters={"model": "gpt-4o"}
    )
    assert {i["value"] for i in data["model"]} == {"deepseek-chat", "gpt-4o"}


# ==================== HTTP API ====================


@pytest.fixture
def client(env):
    rec, _db = env
    app = FastAPI()
    app.include_router(stats_router.router, prefix="/api")
    app.state.container = SimpleNamespace(get=lambda cls: rec)
    return TestClient(app)


def test_api_query_endpoint(client):
    resp = client.get("/api/stats/query", params={"kind": "llm", "granularity": "day"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["summary"]["tokens"] == 880
    assert body["meta"]["unit"] == "tokens"


def test_api_query_rejects_bad_range(client):
    resp = client.get("/api/stats/query", params={"from_ts": 200, "to_ts": 100})
    assert resp.status_code == 400


def test_api_facets_endpoint(client):
    resp = client.get("/api/stats/facets", params={"kind": "llm"})
    assert resp.status_code == 200
    assert {i["value"] for i in resp.json()["facets"]["api"]} == {"deepseek", "openai"}


def test_api_purge_requires_scope(client):
    resp = client.post("/api/stats/purge")
    assert resp.status_code == 400, "无参数时必须拒绝（防误清空）"


def test_api_purge_by_bot(client):
    resp = client.post("/api/stats/purge", params={"bot_id": "99"})
    assert resp.status_code == 200
    assert resp.json()["removed"] == 7


def test_api_without_recorder_returns_503():
    app = FastAPI()
    app.include_router(stats_router.router, prefix="/api")
    app.state.container = SimpleNamespace(get=lambda cls: None)
    set_recorder(None)
    with TestClient(app) as c:
        assert c.get("/api/stats/query").status_code == 503
