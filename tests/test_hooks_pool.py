"""装饰器钩子与 LLM 请求池的测试。"""

import asyncio

from app.llm.context import LlmContext, LlmJob
from app.llm.pool import LlmPool
from app.infrastructure.onebot.client import BotConnection
from app.modules.base import BaseModule
from app.modules.hooks import SendHookRegistry, llm_hook, module_hook, send_hook


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


def test_collect_hooks():
    module_hooks, llm_hooks = _DummyModule.collect_hooks()

    assert {h["event_type"] for h in module_hooks} == {"message_group", "message_private"}
    assert {h["order"] for h in module_hooks} == {5, 6}
    assert llm_hooks[0]["stage"] == "pre_request"
    assert llm_hooks[0]["order"] == 1


def test_collect_send_hooks():
    send_hooks = _DummyModule.collect_send_hooks()

    assert send_hooks == [{"method": "after_send", "message_type": "group", "order": 3}]


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
