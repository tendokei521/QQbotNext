"""P0/P1 优化项回归测试：遥测、会话锁、Provider 注册、工具权限、知识库向量后端。"""

import asyncio

from app.llm.locks import SessionLockManager
from app.llm.providers import AnthropicProvider, GeminiProvider, get_provider_class, provider_supports
from app.llm.providers.base import BaseProvider
from app.llm.telemetry import LLMCallRecord, TelemetryRecorder, ToolCallRecord
from app.llm.tool import ToolContext, ToolSpec


# ---------- LLM 遥测 ----------

def test_telemetry_recorder_stats_and_reset():
    t = TelemetryRecorder(max_records=10)
    t.record_call(LLMCallRecord(provider="openai", model="gpt", success=True, latency_ms=100.0,
                                input_tokens=10, output_tokens=20, tool_calls=1))
    t.record_call(LLMCallRecord(provider="openai", model="gpt", success=False, latency_ms=200.0,
                                error="boom"))
    t.record_tool(ToolCallRecord(name="weather", success=True, duration_ms=5.0))
    t.record_hook("pre_request", 1.5)

    stats = t.stats()
    assert stats["total_calls"] == 2
    assert stats["success_calls"] == 1
    assert stats["error_calls"] == 1
    assert stats["total_tool_calls"] == 1
    assert stats["tool_total"] == 1
    assert len(t.recent(10)) == 2

    t.reset()
    assert t.stats()["total_calls"] == 0
    assert t.recent_tools() == []


def test_telemetry_record_call_simple():
    t = TelemetryRecorder()
    t.record_call_simple(provider="gemini", model="gemini-2.0-flash", success=True, latency_ms=10)
    assert t.recent(1)[0]["model"] == "gemini-2.0-flash"


# ---------- 会话锁 ----------

async def test_session_lock_serializes():
    lm = SessionLockManager()
    lock = lm.lock("group_1")
    assert lock.locked() is False
    await lm.acquire("group_1")
    assert lm.active_count() == 1
    assert lock.locked() is True
    lm.release("group_1")
    assert lock.locked() is False
    lm.clear("group_1")
    assert "group_1" not in lm._locks


# ---------- Provider 注册 / 能力 ----------

def test_provider_registry_has_native_adapters():
    assert issubclass(get_provider_class("anthropic"), BaseProvider)
    assert issubclass(get_provider_class("gemini"), BaseProvider)
    assert AnthropicProvider({}).supports("chat")
    assert GeminiProvider({}).supports("stream")
    assert provider_supports({"provider": "openai", "api_key": "k"}, "chat") is True


# ---------- 工具级权限/作用域 ----------

async def test_tool_permission_denies_non_admin_in_group():
    handler_calls = []

    async def handler(ctx, args):
        handler_calls.append(args)
        return "ok"

    spec = ToolSpec(
        name="admin_tool", description="", parameters={}, handler=handler,
        permission="group_admin", scopes=["group"],
    )

    class _Event:
        event_type = "message_group"
        permission_role = "member"
        is_bot_owner = False
        group = object()
        user_id = 1

    ctx = ToolContext(event=_Event())
    assert spec.allows(ctx) is False

    class _AdminEvent(_Event):
        permission_role = "group_admin"

    ctx2 = ToolContext(event=_AdminEvent())
    assert spec.allows(ctx2) is True


def test_tool_scope_private_only():
    async def handler(ctx, args):
        return "ok"

    spec = ToolSpec(
        name="private_tool", description="", parameters={}, handler=handler,
        permission="everyone", scopes=["private"],
    )

    class _PrivateEvent:
        event_type = "message_private"
        permission_role = "member"
        is_bot_owner = False
        user_id = 1

    class _GroupEvent:
        event_type = "message_group"
        permission_role = "member"
        is_bot_owner = False
        group = object()
        user_id = 1

    assert spec.allows(ToolContext(event=_PrivateEvent())) is True
    assert spec.allows(ToolContext(event=_GroupEvent())) is False


# ---------- 知识库向量后端回退 ----------

def test_knowledge_store_vector_backend_fallback(tmp_path):
    from app.llm.knowledge.store import KnowledgeStore

    store = KnowledgeStore("kb_test", db_path=str(tmp_path / "kb.db"))
    try:
        cid = store.add("苹果是一种水果", title="水果", embedding=[1.0, 0.0, 0.0])
        assert cid
        rows = store.search([1.0, 0.0, 0.0], limit=5)
        assert rows and rows[0]["title"] == "水果"
        assert rows[0]["_score"] >= 0.9
    finally:
        store.close()
