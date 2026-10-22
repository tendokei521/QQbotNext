"""S9 测试：模块直连 provider 时也带上正确归属（不再是 unknown）。"""

from __future__ import annotations

import pytest

from app.agent.usage import context as usage_context
from app.agent.usage.recorder import StatRecorder, set_recorder
from app.agent.usage.metered import MeteredProvider
from app.infrastructure.persistence.database import Database


@pytest.fixture
async def env(tmp_path):
    db = Database(tmp_path / "s9.db")
    await db.connect()
    rec = StatRecorder(db, flush_max=1000, flush_interval=0)
    set_recorder(rec)
    yield rec, db
    await rec.close()
    set_recorder(None)
    await db.close()


class _Resp:
    usage = {"prompt_tokens": 7, "completion_tokens": 3}
    ok = True


class _Inner:
    name = "fake"
    config = {"provider": "deepseek", "model": "m"}

    async def chat(self, messages, **kwargs):
        return _Resp()

    async def stream(self, messages, **kwargs):
        class _Ev:
            usage = {"prompt_tokens": 7, "completion_tokens": 3}

        yield _Ev()


async def test_proactive_wrapper_sets_source(env, monkeypatch):
    """`stream_send_initiative` 内部调用 provider 时应带 `llm_proactive` 归属。"""
    rec, db = env
    from app.plugins.llm_proactive import stream as initiative_stream

    captured: dict = {}

    async def _fake_inner(*args, **kwargs):
        captured["source"] = usage_context.current_usage_ctx().source
        captured["session_id"] = usage_context.current_usage_ctx().session_id
        return "ok"

    monkeypatch.setattr(initiative_stream, "_stream_send_initiative_inner", _fake_inner)
    from types import SimpleNamespace

    runtime = SimpleNamespace(bot_id="42")
    out = await initiative_stream.stream_send_initiative(runtime, None, "group_9", True, 9, [])
    assert out == "ok"
    assert captured["source"] == "llm_proactive"
    assert captured["session_id"] == "group_9"


async def test_metered_provider_records_module_source_via_scope(env):
    """scope 生效时，provider 层记录的是模块名而不是 unknown。"""
    rec, db = env
    wrapped = MeteredProvider(_Inner())
    with usage_context.usage_scope(bot_id="42", source="llm_schedule", session_id="group_7"):
        await wrapped.chat([], model="m")
    await rec.flush()
    row = (await db.fetchall("SELECT * FROM stat_event"))[0]
    assert row["source"] == "llm_schedule"
    assert row["session_id"] == "group_7"
    assert row["session_label"] == "group7"
    assert row["tokens_total"] == 10
