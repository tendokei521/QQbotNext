"""两个 bug 的回归测试。

1. **记忆蒸馏参数**：必须传消息列表（`list[dict]`），不能传会话表（`dict`）。
   历史 bug：`session_mgr.sessions` → 遍历到字符串键 → `'str' object has no attribute 'get'`。

2. **流式记账**：`chat_stream` 必须被计量代理包住。
   历史 bug：只包了 `chat` / `stream`，而 `iter_stream_with_fallback` 调的是 `chat_stream`
   → 开着 stream_output 时 100% 对话不记账。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.usage.context import usage_scope
from app.agent.usage.metered import MeteredProvider
from app.agent.usage.recorder import StatRecorder, set_recorder
from app.infrastructure.persistence.database import Database


@pytest.fixture
async def env(tmp_path):
    db = Database(tmp_path / "bugs.db")
    await db.connect()
    rec = StatRecorder(db, flush_max=1000, flush_interval=0)
    set_recorder(rec)
    yield rec, db
    await rec.close()
    set_recorder(None)
    await db.close()


# ==================== Bug 2：流式必须记账 ====================


class _StreamEvent:
    def __init__(self, type_: str = "text", text: str = "", usage: dict | None = None):
        self.type = type_
        self.text = text
        self.usage = usage


class _StreamProvider:
    """模拟 `BaseProvider`：有 `chat` 与 `chat_stream`（真实调用点用的是后者）。"""

    name = "fake"
    config = {"provider": "openai", "api_base": "https://api.deepseek.com", "model": "m"}

    async def chat(self, messages, **kwargs):
        return SimpleNamespace(usage={"prompt_tokens": 10, "completion_tokens": 2}, ok=True)

    async def chat_stream(self, messages, **kwargs):
        yield _StreamEvent("text", "你")
        yield _StreamEvent("text", "好")
        # 上游通常在最后一帧带 usage
        yield _StreamEvent("done", usage={"prompt_tokens": 37, "completion_tokens": 16,
                                          "prompt_cache_hit_tokens": 20,
                                          "prompt_cache_miss_tokens": 17})

    async def stream(self, messages, **kwargs):
        async for ev in self.chat_stream(messages, **kwargs):
            yield ev


async def test_chat_stream_is_metered(env):
    """`chat_stream`（真实流式入口）必须记一条 llm 行。"""
    rec, db = env
    wrapped = MeteredProvider(_StreamProvider())
    with usage_scope(bot_id="42", source="chat", session_id="private_9"):
        events = [ev async for ev in wrapped.chat_stream([{"role": "user", "content": "hi"}], model="m")]
    assert len(events) == 3
    await rec.flush()

    rows = await db.fetchall("SELECT * FROM stat_event WHERE kind='llm'")
    assert len(rows) == 1, "流式调用必须记账（此前 chat_stream 被透传，0 行）"
    row = rows[0]
    assert row["input_tokens"] == 37
    assert row["output_tokens"] == 16
    assert row["cache_hit"] == 20
    assert row["source"] == "chat"
    assert row["api"] == "deepseek", "api 应显示真实服务商，不是适配器名 openai"


async def test_stream_alias_is_metered_too(env):
    rec, db = env
    wrapped = MeteredProvider(_StreamProvider())
    with usage_scope(bot_id="42", source="llm_proactive"):
        _ = [ev async for ev in wrapped.stream([], model="m")]
    await rec.flush()
    rows = await db.fetchall("SELECT source FROM stat_event WHERE kind='llm'")
    assert [r["source"] for r in rows] == ["llm_proactive"]


async def test_stream_without_usage_marks_estimated(env):
    """上游不给 usage（老代理）→ 仍记一行，但标 estimated。"""

    class _Bare(_StreamProvider):
        async def chat_stream(self, messages, **kwargs):
            yield _StreamEvent("text", "hi")

    rec, db = env
    wrapped = MeteredProvider(_Bare())
    with usage_scope(bot_id="1", source="chat"):
        _ = [ev async for ev in wrapped.chat_stream([], model="m")]
    await rec.flush()
    rows = await db.fetchall("SELECT estimated, tokens_total FROM stat_event WHERE kind='llm'")
    assert len(rows) == 1
    assert rows[0]["estimated"] == 1


# ==================== Bug 1：蒸馏参数类型 ====================


async def test_run_distill_accepts_history_snapshot_shape(tmp_path, monkeypatch):
    """`_run_distill` 收到 `history_snapshot()` 的形状（list[dict]）必须正常工作。

    历史 bug：调用方传的是 `session_mgr.sessions`（dict），遍历到字符串键 →
    `'str' object has no attribute 'get'`。
    """
    from app.plugins.llm_memory.manager import MemoryManager

    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    runtime = SimpleNamespace(bot_id="1", config={}, bot=None, session_mgr=None)
    mgr = MemoryManager(runtime)

    saved: list = []

    async def _fake_extract(_runtime, uid, texts, is_group=False):
        return [{"content": f"{uid} 喜欢猫", "importance": 0.9, "confidence": 0.8}]

    monkeypatch.setattr(mgr, "save_fact", lambda *a, **k: saved.append(a) or "m1")
    monkeypatch.setattr(mgr, "own_owner", lambda sid, uid: f"private_{uid}")

    import app.plugins.llm_memory.extract as extract_mod

    monkeypatch.setattr(extract_mod, "extract_facts_for_user_async", _fake_extract)

    messages = [
        {"role": "user", "content": "我喜欢猫", "user_id": "5"},
        {"role": "assistant", "content": "记住了"},
    ]
    await mgr._run_distill("private_5", False, messages, "chat")
    assert saved, "正常入参应能蒸馏出记忆"
    mgr.stop()


async def test_run_distill_with_dict_logs_warning_instead_of_crashing(tmp_path, monkeypatch, caplog):
    """反向验证：传 dict（旧写法）会被内部捕获并记 warning（就是用户看到的那条日志）。"""
    from app.plugins.llm_memory.manager import MemoryManager

    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    runtime = SimpleNamespace(bot_id="1", config={}, bot=None, session_mgr=None)
    mgr = MemoryManager(runtime)
    try:
        await mgr._run_distill("private_5", False, {"private_5": "Session对象"}, "chat")
    finally:
        mgr.stop()
    # 不抛异常（内部吞掉），但要留下 warning 痕迹
    assert any("蒸馏失败" in r.message for r in caplog.records), "应记录蒸馏失败日志"
