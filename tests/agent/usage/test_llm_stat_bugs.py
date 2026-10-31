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
# v3 记忆重写后，蒸馏入口是 MemoryManager.maybe_consolidate（原 _run_distill 已删除）：
# 它只把 list[dict] 的消息写进 episodes，形状错误（dict）也不能把一轮对话打断。


def _memory_manager(tmp_path, monkeypatch):
    from app.plugins.llm_memory.src.manager import MemoryManager

    class _Module:
        bot_id = "1"

        def __init__(self):
            self.config = {
                "memory_enable": True,
                "memory_extract_enable": False,  # 只验证入参形状，不触发后台 LLM
                "memory_private_enable": True,
                "memory_group_enable": True,
                "memory_min_confidence": 0.5,
                "memory_audit_enable": False,
            }
            self.ctx = SimpleNamespace(services=None)
            self._runtime = lambda: None

    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    return MemoryManager(_Module())


async def test_maybe_consolidate_accepts_history_snapshot_shape(tmp_path, monkeypatch):
    """`history_snapshot()` 的形状（list[dict]）必须被正常消费为原始语料。"""
    manager = _memory_manager(tmp_path, monkeypatch)
    messages = [
        {"role": "user", "content": "我喜欢猫", "user_id": "5"},
        {"role": "assistant", "content": "记住了"},
    ]
    try:
        await manager.maybe_consolidate("private_5", False, messages)
        episodes = manager.store.list_episodes("private_5")
    finally:
        manager.stop()
    assert [ep["content"] for ep in episodes] == ["我喜欢猫"]


async def test_maybe_consolidate_with_dict_does_not_crash(tmp_path, monkeypatch):
    """反向验证：误传 dict（旧代码传 `session_mgr.sessions`）不抛异常。"""
    manager = _memory_manager(tmp_path, monkeypatch)
    try:
        await manager.maybe_consolidate(
            "private_5", False, {"private_5": "Session对象"}
        )
        episodes = manager.store.list_episodes("private_5")
    finally:
        manager.stop()
    # 非 dict 的值被忽略，但调用本身必须安全返回
    assert episodes == []
