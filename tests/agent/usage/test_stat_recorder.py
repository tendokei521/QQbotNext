"""S1/S2 测试：`stat_event` 表、`StatRecorder` 写入/批量/清理、`MeteredProvider` 计量与归属。

原则：全部走真实 SQLite 临时库，不 mock 存储层。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.agent.usage.context import UNKNOWN_SOURCE, current_usage_ctx, usage_scope
from app.agent.usage.labels import label_from_session_id, session_label, short_name
from app.agent.usage.metered import MeteredProvider, wrap_metered
from app.agent.usage.recorder import COLUMNS, StatRecorder, day_key, set_recorder
from app.infrastructure.persistence.database import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "stats.db")
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
async def recorder(db):
    rec = StatRecorder(db, flush_max=1000, flush_interval=0)  # 手动 flush，避免后台任务
    set_recorder(rec)
    yield rec
    await rec.close()
    set_recorder(None)


# ==================== 表结构 ====================


async def test_table_has_all_design_columns(db):
    cols = await db.table_columns("stat_event")
    assert set(COLUMNS) <= cols
    # 关键：缓存命中可空（NULL = 上游未报 ≠ 0）
    rows = await db.fetchall("PRAGMA table_info(stat_event)")
    nullable = {r["name"]: (r["notnull"] == 0) for r in rows}
    assert nullable["cache_hit"] is True
    assert nullable["cache_miss"] is True


# ==================== 记录器基础 ====================


async def test_record_and_flush_writes_row(db, recorder):
    recorder.record(kind="llm", bot_id="1", source="chat", tokens_total=100, requests=1)
    assert recorder.written == 0, "写入前不该落库"
    written = await recorder.flush()
    assert written == 1
    rows = await db.fetchall("SELECT * FROM stat_event")
    assert len(rows) == 1
    assert rows[0]["tokens_total"] == 100
    assert rows[0]["day_local"] == day_key(time.time())


async def test_record_llm_extracts_tokens_and_cache(db, recorder):
    recorder.record_llm(
        usage={
            "prompt_tokens": 653,
            "completion_tokens": 8,
            "total_tokens": 661,
            "prompt_cache_hit_tokens": 512,
            "prompt_cache_miss_tokens": 141,
            "completion_tokens_details": {"reasoning_tokens": 8},
        },
        bot_id="1",
        source="llm_proactive",
        api="deepseek",
        model="deepseek-v4-flash",
    )
    await recorder.flush()
    row = (await db.fetchall("SELECT * FROM stat_event"))[0]
    assert row["kind"] == "llm"
    assert row["input_tokens"] == 653
    assert row["output_tokens"] == 8
    assert row["cache_hit"] == 512
    assert row["cache_miss"] == 141
    assert row["tokens_total"] == 661
    assert row["reasoning_tokens"] == 8
    assert row["source"] == "llm_proactive"
    assert row["estimated"] == 0


async def test_record_llm_without_cache_fields_stores_null(db, recorder):
    """上游没报缓存 → NULL（不是 0），否则"未知"会被误读成"缓存没生效"。"""
    recorder.record_llm(usage={"prompt_tokens": 100, "completion_tokens": 20}, bot_id="1")
    await recorder.flush()
    row = (await db.fetchall("SELECT * FROM stat_event"))[0]
    assert row["cache_hit"] is None
    assert row["cache_miss"] is None
    assert row["tokens_total"] == 120


async def test_record_llm_without_usage_marks_estimated(db, recorder):
    recorder.record_llm(usage=None, bot_id="1")
    await recorder.flush()
    row = (await db.fetchall("SELECT * FROM stat_event"))[0]
    assert row["estimated"] == 1
    assert row["tokens_total"] == 0


async def test_record_msg_in_out(db, recorder):
    recorder.record_msg_in(outcome="llm", bot_id="1", session_id="group_9", event_type="message_group")
    recorder.record_msg_in(outcome="ignored", bot_id="1", session_id="group_9")
    recorder.record_msg_out(segments=3, nbytes=42, bot_id="1", session_id="group_9")
    await recorder.flush()
    rows = await db.fetchall("SELECT * FROM stat_event ORDER BY id")
    assert [r["kind"] for r in rows] == ["msg_in", "msg_in", "msg_out"]
    assert [r["outcome"] for r in rows[:2]] == ["llm", "ignored"]
    assert rows[2]["segments"] == 3
    assert rows[2]["bytes"] == 42
    assert rows[2]["direction"] == "out"


async def test_batched_flush_on_threshold(db):
    """攒够 flush_max 条自动落库（有事件循环时）。"""
    rec = StatRecorder(db, flush_max=3, flush_interval=0)
    set_recorder(rec)
    try:
        rec.record(kind="llm", bot_id="1")
        rec.record(kind="llm", bot_id="1")
        assert rec.written == 0
        rec.record(kind="llm", bot_id="1")  # 第 3 条触发
        await asyncio.sleep(0.05)
        assert rec.written == 3
    finally:
        await rec.close()
        set_recorder(None)


async def test_close_forces_flush(db):
    """退出必须强制落库（历史教训：fire-and-forget 会丢数据）。"""
    rec = StatRecorder(db, flush_max=1000, flush_interval=100)
    set_recorder(rec)
    rec.record(kind="llm", bot_id="9")
    await rec.close()
    set_recorder(None)
    rows = await db.fetchall("SELECT COUNT(*) AS c FROM stat_event")
    assert rows[0]["c"] == 1


async def test_write_after_close_is_ignored(db):
    rec = StatRecorder(db, flush_max=1000, flush_interval=0)
    set_recorder(rec)
    await rec.close()
    rec.record(kind="llm", bot_id="1")
    assert rec.written == 0
    set_recorder(None)


async def test_purge_in_batches(db, recorder):
    for i in range(5):
        recorder.record(kind="llm", bot_id="1", ts=1000 + i)
    recorder.record(kind="llm", bot_id="1", ts=99999)
    await recorder.flush()

    removed = await recorder.purge(before_ts=5000, batch=2)
    assert removed == 5
    left = await db.fetchall("SELECT COUNT(*) AS c FROM stat_event")
    assert left[0]["c"] == 1


async def test_purge_by_bot_only(db, recorder):
    recorder.record(kind="llm", bot_id="a")
    recorder.record(kind="llm", bot_id="b")
    await recorder.flush()
    assert await recorder.purge(bot_id="a") == 1
    rows = await db.fetchall("SELECT bot_id FROM stat_event")
    assert [r["bot_id"] for r in rows] == ["b"]


# ==================== 归属上下文 ====================


def test_usage_scope_sets_and_restores():
    assert current_usage_ctx().source == UNKNOWN_SOURCE
    with usage_scope(bot_id="7", source="llm_memory", session_id="private_1"):
        ctx = current_usage_ctx()
        assert ctx.bot_id == "7"
        assert ctx.source == "llm_memory"
        assert ctx.session_id == "private_1"
    assert current_usage_ctx().source == UNKNOWN_SOURCE, "退出后必须还原"


def test_usage_scope_is_async_safe():
    async def _worker(name: str) -> str:
        with usage_scope(source=name):
            await asyncio.sleep(0.01)
            return current_usage_ctx().source

    async def _main() -> list[str]:
        return await asyncio.gather(_worker("a"), _worker("b"))

    assert asyncio.get_event_loop_policy() is not None
    assert asyncio.run(_main()) == ["a", "b"]


# ==================== 展示名 ====================


def test_short_name_truncates_to_five_chars():
    assert short_name("小明") == "小明"
    assert short_name("一二三四五") == "一二三四五"
    assert short_name("一二三四五六") == "一二三四五…"
    assert short_name("") == ""
    assert short_name(None) == ""


def test_session_label_format():
    assert session_label("才羽 ミドリ", "group", "466052056") == "才羽 ミド…group466052056"
    assert session_label("小明", "private", "1901691195") == "小明private1901691195"


def test_label_from_session_id():
    assert label_from_session_id("group_77") == "group77"
    assert label_from_session_id("private_88", "小明") == "小明private88"
    assert label_from_session_id("weird") == "weird"


# ==================== 计量代理 ====================


class _Resp:
    def __init__(self, usage=None, ok=True):
        self.usage = usage
        self.ok = ok
        self.text = "hi"
        self.model = "m"


class _InnerProvider:
    name = "fake"

    def __init__(self, config=None, *, fail=False):
        self.config = config or {"provider": "deepseek", "model": "deepseek-chat"}
        self.calls = 0
        self.fail = fail

    def supports(self, cap):
        return cap in ("chat", "stream")

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return _Resp(
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "prompt_cache_hit_tokens": 4,
                "prompt_cache_miss_tokens": 6,
            }
        )

    async def stream(self, messages, **kwargs):
        class _Ev:
            def __init__(self, usage=None):
                self.usage = usage
                self.delta = "x"

        yield _Ev()
        yield _Ev({"prompt_tokens": 12, "completion_tokens": 3})


async def test_metered_provider_records_chat(db, recorder):
    inner = _InnerProvider()
    wrapped = MeteredProvider(inner)

    with usage_scope(bot_id="42", source="llm_schedule", session_id="group_5"):
        resp = await wrapped.chat([{"role": "user", "content": "hi"}], model="deepseek-chat")

    assert resp is not None and inner.calls == 1
    await recorder.flush()
    row = (await db.fetchall("SELECT * FROM stat_event"))[0]
    assert row["bot_id"] == "42"
    assert row["source"] == "llm_schedule"
    assert row["session_id"] == "group_5"
    assert row["session_label"] == "group5"
    assert row["api"] == "deepseek"
    assert row["model"] == "deepseek-chat"
    assert row["cache_hit"] == 4
    assert row["tokens_total"] == 15


async def test_metered_provider_records_failure_then_raises(db, recorder):
    wrapped = MeteredProvider(_InnerProvider(fail=True))
    with pytest.raises(RuntimeError):
        with usage_scope(bot_id="1", source="llm_proactive"):
            await wrapped.chat([], model="m")
    await recorder.flush()
    row = (await db.fetchall("SELECT * FROM stat_event"))[0]
    assert row["ok"] == 0
    assert row["err_kind"] == "RuntimeError"
    assert row["source"] == "llm_proactive"


async def test_metered_provider_stream_collects_usage(db, recorder):
    wrapped = MeteredProvider(_InnerProvider())
    with usage_scope(bot_id="1", source="chat"):
        events = [ev async for ev in wrapped.stream([], model="m")]
    assert len(events) == 2
    await recorder.flush()
    row = (await db.fetchall("SELECT * FROM stat_event"))[0]
    assert row["input_tokens"] == 12
    assert row["output_tokens"] == 3
    assert row["estimated"] == 0


async def test_metered_provider_without_context_records_unknown(db, recorder):
    """模块绕过总线（没设 context）→ source=unknown，但总量照样记。"""
    wrapped = MeteredProvider(_InnerProvider())
    await wrapped.chat([], model="m")
    await recorder.flush()
    row = (await db.fetchall("SELECT * FROM stat_event"))[0]
    assert row["source"] == UNKNOWN_SOURCE
    assert row["tokens_total"] == 15


async def test_wrap_metered_is_idempotent_and_noop_without_recorder(db):
    inner = _InnerProvider()
    set_recorder(None)
    assert wrap_metered(inner) is inner, "没装记录器时不包装"
    set_recorder(StatRecorder(db, flush_max=10, flush_interval=0))
    wrapped = wrap_metered(inner)
    assert isinstance(wrapped, MeteredProvider)
    assert wrap_metered(wrapped) is wrapped, "已包过不再包"
    set_recorder(None)


async def test_metered_provider_passes_through_attributes(db, recorder):
    wrapped = MeteredProvider(_InnerProvider())
    assert wrapped.name == "fake"
    assert wrapped.supports("chat") is True
    assert wrapped.config["provider"] == "deepseek"
