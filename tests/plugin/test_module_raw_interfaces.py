"""模块侧原料接口测试：`send_to` / `spawn` / `llm_turn` / `framework_tools`。

这些是 v3「模块自己写业务」的全部依赖面。没它们，模块写主动回复只能抄框架
（历史上抄了 3 份、1100+ 行）。本文件锁住"只用公开接口就能干完"。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.plugin.base import BaseModule, ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess


class _Bot:
    def __init__(self):
        self.sent: list[tuple] = []

    async def send_group_msg(self, group_id, message, auto_escape=False):
        self.sent.append(("group", group_id, getattr(message, "text", message)))
        return {"status": "ok", "data": {"message_id": 1}}

    async def send_private_msg(self, user_id, message, auto_escape=False):
        self.sent.append(("private", user_id, getattr(message, "text", message)))
        return {"status": "ok", "data": {"message_id": 2}}


class _TaskManager:
    def __init__(self):
        self.created: list[tuple[str, object]] = []

    def create_task(self, coro, name=None, owner="system"):
        self.created.append((owner, name))
        task = asyncio.ensure_future(coro)
        return task


class _ConfigService:
    """最小配置中心：内存字典。"""

    def __init__(self, values=None):
        self._values = values or {}

    def get_module_config(self, name, bot_id):
        return dict(self._values.get((name, bot_id), {}))

    def set_module_config(self, name, bot_id, data, persist=True):
        self._values[(name, bot_id)] = dict(data)

    def get_module_authority(self, name, bot_id):
        return {"enabled": True}

    def set_module_authority(self, name, bot_id, data):
        return True


class _Runtime:
    def __init__(self, bot=None):
        self.bot_id = 12345
        self.bot = bot
        self.turn_calls: list[object] = []

    async def fake_turn(self, request, on_sentence=None):
        self.turn_calls.append(request)
        if on_sentence is not None:
            await on_sentence("流式第一句")
        return SimpleNamespace(text="ok", error="", blocked=False, session_id=request.session_id)


def _make_module(services: ServiceAccess, bot_id=12345):
    cls = type("Demo", (BaseModule,), {"name": "演示", "sign": "Demo"})
    ctx = ModuleContext(
        module_name="demo",
        bot_id=bot_id,
        config=ModuleConfig("demo", bot_id, {"enable": True}, _ConfigService()),
        authority=ModuleAuthority("demo", bot_id, _ConfigService()),
        services=services,
        bot=services.extra.get("bot"),
    )
    return cls(ctx)


@pytest.fixture
def bot():
    return _Bot()


@pytest.fixture
def module(bot):
    runtime = _Runtime(bot=bot)
    services = ServiceAccess(
        task_manager=_TaskManager(),
        runtime=lambda bot_id: runtime,
        sessions=lambda bot_id: SimpleNamespace(bot_id=str(bot_id)),
    )
    services.extra["bot"] = bot
    return _make_module(services)


# ---------- send_to ----------


async def test_send_to_routes_by_session_prefix(module, bot):
    await module.send_to("group_999", "晚上好", hooks=False)
    await module.send_to("private_888", "在吗", hooks=False)

    assert bot.sent[0] == ("group", 999, "晚上好")
    assert bot.sent[1] == ("private", 888, "在吗")


async def test_send_to_uses_runtime_live_bot(module):
    """运行时重绑后 `send_to` 用新连接（换号场景不发给旧 socket）。"""
    new_bot = _Bot()
    module._runtime().bot = new_bot
    await module.send_to("group_1", "hi", hooks=False)
    assert new_bot.sent and new_bot.sent[0][0] == "group"


# ---------- spawn ----------


async def test_spawn_uses_module_scoped_owner(module):
    async def work():
        await asyncio.sleep(0)

    task = module.spawn(work(), name="demo-work")
    assert module.task_owner == "module:demo:12345"
    assert module.ctx.services.task_manager.created == [("module:demo:12345", "demo-work")]
    await task


async def test_spawn_without_task_manager_raises():
    ctx = ModuleContext(
        module_name="demo",
        bot_id=1,
        config=ModuleConfig("demo", 1, {}, _ConfigService()),
        authority=ModuleAuthority("demo", 1, _ConfigService()),
        services=ServiceAccess(),
    )
    module = type("Demo", (BaseModule,), {})(ctx)

    async def work():
        return None

    coro = work()
    try:
        with pytest.raises(RuntimeError):
            module.spawn(coro)
    finally:
        coro.close()  # 未托管的协程要显式关闭，避免 "never awaited" 告警


# ---------- llm_turn ----------


async def test_llm_turn_delegates_to_run_turn(module, monkeypatch):
    import app.agent.loop.turn as turn_module

    runtime = module._runtime()
    captured: dict = {}

    async def fake_run_turn(rt, request, *, ctx=None, on_sentence=None):
        captured["runtime"] = rt
        captured["request"] = request
        if on_sentence is not None:
            await on_sentence("一句话")
        return turn_module.TurnResult(text="一句话", session_id=request.session_id)

    monkeypatch.setattr(turn_module, "run_turn", fake_run_turn)
    result = await module.llm_turn("总结", session_id="group_1", record_history=False)

    assert result.text == "一句话"
    assert captured["runtime"] is runtime
    request = captured["request"]
    assert request.session_id == "group_1"
    assert request.is_private is False  # 由 group_ 前缀推断
    assert request.record_history is False  # 模块默认不污染会话
    assert request.bot is runtime.bot


async def test_llm_turn_send_true_sends_streamed_sentences(module, monkeypatch, bot):
    import app.agent.loop.turn as turn_module

    async def fake_run_turn(rt, request, *, ctx=None, on_sentence=None):
        assert on_sentence is not None, "send=True 必须提供流式回调"
        await on_sentence("流式第一句")
        return turn_module.TurnResult(text="流式第一句", session_id=request.session_id)

    monkeypatch.setattr(turn_module, "run_turn", fake_run_turn)
    await module.llm_turn("hi", session_id="group_7", send=True)

    assert bot.sent and bot.sent[0] == ("group", 7, "流式第一句")


async def test_llm_turn_without_runtime_degrades(monkeypatch):
    services = ServiceAccess(runtime=lambda bot_id: None)
    module = _make_module(services)
    result = await module.llm_turn("hi", session_id="group_1")
    assert result.error == "no_runtime"


# ---------- framework_tools / sessions ----------


async def test_framework_tools_degrades_without_runtime():
    services = ServiceAccess(runtime=lambda bot_id: None)
    module = _make_module(services)
    specs, ctx = await module.framework_tools("group_1")
    assert specs == [] and ctx is None


async def test_sessions_facade_returns_manager(module):
    assert module.sessions.bot_id == "12345"


async def test_framework_tools_passes_session_target(monkeypatch, module):
    import app.agent.tools.catalog as catalog

    seen: dict = {}

    async def fake_framework_tools(runtime, *, session_id, is_private, **kwargs):
        seen.update(
            {"session_id": session_id, "is_private": is_private, "kwargs": kwargs}
        )
        return ["spec"], "ctx"

    monkeypatch.setattr(catalog, "framework_tools", fake_framework_tools)
    specs, ctx = await module.framework_tools("group_42")

    assert specs == ["spec"] and ctx == "ctx"
    assert seen["session_id"] == "group_42"
    assert seen["is_private"] is False
    assert seen["kwargs"]["group_id"] == "42"
