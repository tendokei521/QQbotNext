"""装饰器钩子与 LLM 请求池的测试。"""

import asyncio

from app.infrastructure.onebot.client import BotConnection
from app.llm.context import LlmContext, LlmJob
from app.llm.hooks import ToolCallContext, ToolCallHookRegistry
from app.llm.pool import LlmPool
from app.modules.base import BaseModule
from app.modules.hooks import (
    ApiHookRegistry,
    BeforeSendHookRegistry,
    EventCompletedHookRegistry,
    LifecycleHookRegistry,
    SendHookRegistry,
    api_hook,
    before_send_hook,
    bot_lifecycle_hook,
    event_completed_hook,
    llm_hook,
    module_hook,
    send_hook,
    tool_call_hook,
)


class _DummyModule(BaseModule):
    @module_hook("message_group", order=5)
    @module_hook("message_private", order=6)
    async def on_msg(self, event):
        pass

    @llm_hook("pre_request", event_type="*", order=1)
    async def pre_request(self, ctx):
        pass

    @send_hook("group", order=3)
    async def after_send(self, ctx):
        pass

    @before_send_hook("group", order=4)
    async def before_send(self, ctx):
        pass

    @api_hook("send_*", order=5)
    async def after_api(self, ctx):
        pass

    @bot_lifecycle_hook("login", order=6)
    async def on_login(self, ctx):
        pass

    @event_completed_hook(order=7)
    async def after_event(self, ctx):
        pass

    @tool_call_hook("group", order=8)
    async def after_tool_call(self, ctx):
        pass


def test_collect_hooks():
    module_hooks, llm_hooks = _DummyModule.collect_hooks()

    assert {h["event_type"] for h in module_hooks} == {"message_group", "message_private"}
    assert {h["order"] for h in module_hooks} == {5, 6}
    assert llm_hooks[0]["stage"] == "pre_request"
    assert llm_hooks[0]["order"] == 1


def test_collect_plugin_hooks():
    assert _DummyModule.collect_send_hooks() == [{"method": "after_send", "message_type": "group", "order": 3}]
    assert _DummyModule.collect_before_send_hooks() == [{"method": "before_send", "message_type": "group", "order": 4}]
    assert _DummyModule.collect_api_hooks() == [{"method": "after_api", "action": "send_*", "order": 5}]
    assert _DummyModule.collect_lifecycle_hooks() == [{"method": "on_login", "state": "login", "order": 6}]
    assert _DummyModule.collect_event_completed_hooks() == [{"method": "after_event", "order": 7}]
    assert _DummyModule.collect_tool_call_hooks() == [{"method": "after_tool_call", "event_type": "group", "order": 8}]


class _FakeSendBot:
    bot_id = 123
    index = 0


async def test_send_hook_registry_run_with_message_id():
    got = []

    async def handler(ctx):
        got.append(ctx)

    reg = SendHookRegistry(log=None)
    reg.register(bot_id=123, module=object(), handler=handler, message_type="*", order=1)

    await reg.run(
        _FakeSendBot(),
        "send_group_msg",
        {"group_id": 1, "message": "hi"},
        {"status": "ok", "data": {"message_id": 42}},
    )

    assert len(got) == 1
    assert got[0].message_id == 42
    assert got[0].message_type == "group"
    assert got[0].group_id == 1


async def test_before_send_hook_registry_rewrite_and_block():
    seen = []

    async def rewrite(ctx):
        seen.append(ctx.params.get("message"))
        ctx.params["message"] = "[改写]" + ctx.params.get("message", "")

    async def blocker(ctx):
        ctx.skip = True

    reg = BeforeSendHookRegistry(log=None)
    reg.register(bot_id=123, module=object(), handler=rewrite, order=1)

    params = {"group_id": 1, "message": "hi"}
    assert await reg.run(_FakeSendBot(), "send_group_msg", params) is True
    assert params["message"] == "[改写]hi"

    reg.register(bot_id=123, module=object(), handler=blocker, order=2)
    assert await reg.run(_FakeSendBot(), "send_group_msg", params) is False


async def test_api_hook_registry_matches_wildcard():
    got = []

    async def handler(ctx):
        got.append(ctx)

    reg = ApiHookRegistry(log=None)
    reg.register(bot_id=123, module=object(), handler=handler, action="send_*")
    await reg.run(_FakeSendBot(), "send_group_msg", {"group_id": 1}, {"status": "ok", "data": {"message_id": 9}})

    assert len(got) == 1
    assert got[0].success is True
    assert got[0].message_id == 9


async def test_lifecycle_hook_registry():
    got = []

    async def handler(ctx):
        got.append((ctx.state, ctx.bot_id))

    reg = LifecycleHookRegistry(log=None)
    reg.register(bot_id=123, module=object(), handler=handler, state="login")
    await reg.run(_FakeSendBot(), "login")

    assert got == [("login", 123)]


async def test_event_completed_hook_registry():
    got = []

    async def handler(ctx):
        got.append(ctx.duration_ms)

    class _Event:
        bot = _FakeSendBot()
        bot_id = 123

    reg = EventCompletedHookRegistry(log=None)
    reg.register(bot_id=123, module=object(), handler=handler)
    await reg.run(_Event(), state={"x": 1}, duration_ms=3.5)

    assert got == [3.5]


async def test_tool_call_hook_registry():
    got = []

    async def handler(ctx):
        got.append(ctx.name)

    reg = ToolCallHookRegistry(log=None)
    reg.register(event_type="group", handler=handler)
    await reg.run(ToolCallContext(name="weather", args={}, result="sunny", success=True, duration_ms=1.0,
                                  event_type="group", bot_id=123))

    assert got == ["weather"]


async def test_bot_connection_send_triggers_registry():
    got = []

    async def handler(ctx):
        got.append(ctx.message_id)

    reg = SendHookRegistry(log=None)
    reg.register(bot_id=123, module=object(), handler=handler)

    conn = BotConnection()
    conn.bot_id = 123
    conn.index = 0
    conn.send_hook_registry = reg

    async def fake_outbound(action, params):
        return {"status": "ok", "data": {"message_id": 7}}

    conn.outbound_hook = fake_outbound

    resp = await conn._send("send_group_msg", {"group_id": 1, "message": "hi"})
    assert resp["data"]["message_id"] == 7
    assert got == [7]


async def test_bot_connection_before_send_and_api_hooks():
    seen = []

    before_reg = BeforeSendHookRegistry(log=None)

    async def before_handler(ctx):
        ctx.params["message"] = "[改写]" + ctx.params.get("message", "")

    before_reg.register(bot_id=123, module=object(), handler=before_handler)

    api_reg = ApiHookRegistry(log=None)

    async def api_handler(ctx):
        seen.append((ctx.action, ctx.success))

    api_reg.register(bot_id=123, module=object(), handler=api_handler)

    conn = BotConnection()
    conn.bot_id = 123
    conn.index = 0
    conn.before_send_hook_registry = before_reg
    conn.api_hook_registry = api_reg

    sent = []

    async def fake_outbound(action, params):
        sent.append((action, params))
        return {"status": "ok", "data": {}}

    conn.outbound_hook = fake_outbound

    resp = await conn._send("send_group_msg", {"group_id": 1, "message": "hi"})
    assert resp["status"] == "ok"
    assert sent[0][1]["message"] == "[改写]hi"
    assert seen == [("send_group_msg", True)]


async def test_send_hook_registry_skips_failed_or_missing_id():
    got = []

    async def handler(ctx):
        got.append(ctx)

    reg = SendHookRegistry(log=None)
    reg.register(bot_id=123, module=object(), handler=handler)
    bot = _FakeSendBot()

    await reg.run(bot, "send_group_msg", {"group_id": 1}, {"status": "failed", "data": {}})
    await reg.run(bot, "send_group_msg", {"group_id": 1}, {"status": "ok", "data": {}})
    assert got == []


class _Event:
    pass


def _make_job(pool: LlmPool, name: str) -> LlmJob:
    ctx = LlmContext(
        event=_Event(),
        runtime=None,
        bot=None,
        session_id="group_1",
        user_text=name,
    )
    job = LlmJob(id=name, group_key="group_1", ctx=ctx)
    ctx.job = job
    return job


async def test_pool_supersede_old_job():
    pool = LlmPool()

    async def run(name: str):
        job = _make_job(pool, name)
        return await pool.wait_for_continue(job, debounce=0.1)

    first = asyncio.create_task(run("a"))
    await asyncio.sleep(0.02)
    second = asyncio.create_task(run("b"))

    results = await asyncio.gather(first, second)
    assert results == [False, True]