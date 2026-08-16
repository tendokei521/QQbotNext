"""装饰器钩子与 LLM 请求池的测试。"""

import asyncio

from app.llm.context import LlmContext, LlmJob
from app.llm.pool import LlmPool
from app.modules.base import BaseModule
from app.modules.hooks import llm_hook, module_hook


class _DummyModule(BaseModule):
    @module_hook("message_group", order=5)
    @module_hook("message_private", order=6)
    async def on_msg(self, event):
        pass

    @llm_hook("pre_request", event_type="*", order=1)
    async def pre_request(self, ctx):
        pass


def test_collect_hooks():
    module_hooks, llm_hooks = _DummyModule.collect_hooks()

    assert {h["event_type"] for h in module_hooks} == {"message_group", "message_private"}
    assert {h["order"] for h in module_hooks} == {5, 6}
    assert llm_hooks[0]["stage"] == "pre_request"
    assert llm_hooks[0]["order"] == 1


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
