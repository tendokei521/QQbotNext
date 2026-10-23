"""回归测试：`triggered`（响应条数）必须只统计非 ignored 的消息。

历史 bug：前端把 `metric=triggered` 映射成 `messages` 字段，
于是"响应条数"图画的是收到总数；后端也没在序列里返回 triggered。
"""

from __future__ import annotations

import pytest

from app.agent.usage import query as stat_query
from app.agent.usage.recorder import StatRecorder, set_recorder
from app.infrastructure.persistence.database import Database


@pytest.fixture
async def env(tmp_path):
    db = Database(tmp_path / "trig.db")
    await db.connect()
    rec = StatRecorder(db, flush_max=1000, flush_interval=0)
    set_recorder(rec)
    # 6 围观 + 2 LLM + 1 指令 + 1 模块
    for outcome in ["ignored"] * 6 + ["llm"] * 2 + ["command"] + ["module"]:
        rec.record_msg_in(outcome=outcome, bot_id="1", source="inbound", session_id="group_1")
    await rec.flush()
    yield rec, db
    await rec.close()
    set_recorder(None)
    await db.close()


async def test_summary_separates_received_and_responded(env):
    _rec, db = env
    data = await stat_query.query(
        db, kind="msg_in", metric="messages", granularity="day", breakdown="outcome",
        from_ts=0, to_ts=9_999_999_999,
    )
    s = data["summary"]
    assert s["messages"] == 10, "收到的 10 条"
    assert s["triggered"] == 4, "响应的只有 4 条（ignored 不算）"
    assert s["by_llm"] == 2
    assert s["by_command"] == 1
    assert s["by_module"] == 1


async def test_series_exposes_triggered_field(env):
    """序列里必须带 triggered（表格"响应条数"行要用）。"""
    _rec, db = env
    data = await stat_query.query(
        db, kind="msg_in", metric="messages", granularity="day", breakdown="outcome",
        from_ts=0, to_ts=9_999_999_999,
    )
    ignored = data["buckets"][0]["series"]["ignored"]
    assert ignored["messages"] == 6
    assert ignored["triggered"] == 0, "围观的响应数必须是 0"


async def test_triggered_metric_value_excludes_ignored(env):
    """metric=triggered 时，每个序列的 `value` 必须等于 triggered（前端图表只看 value）。"""
    _rec, db = env
    data = await stat_query.query(
        db, kind="msg_in", metric="triggered", granularity="day", breakdown="outcome",
        from_ts=0, to_ts=9_999_999_999,
    )
    series = data["buckets"][0]["series"]
    assert series["ignored"]["value"] == 0
    assert series["llm"]["value"] == 2
    assert series["command"]["value"] == 1
    assert series["module"]["value"] == 1
    assert sum(item["value"] for item in series.values()) == 4
