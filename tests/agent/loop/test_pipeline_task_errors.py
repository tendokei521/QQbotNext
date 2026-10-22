"""步骤 3 的回归测试：任务异常必须被取走并落日志，不能静默消失。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.agent.loop.context import LlmContext, LlmJob
from app.agent.loop.pipeline import LlmPipeline


class _Runtime:
    def __init__(self):
        self.bot_id = "1"
        self.config = {"interrupt_enable": False, "private_enable": True}
        self.proactive = None
        self.output = None
        self.llm_hooks = None
        self.telemetry = None

    def provider_config(self):
        return {"api_key": "k"}


def _pipeline() -> LlmPipeline:
    return LlmPipeline(_Runtime())


def _job() -> LlmJob:
    runtime = _Runtime()
    ctx = LlmContext(
        event=SimpleNamespace(
            event_type="message_private",
            message_type="private",
            user_id=1,
            self_id=2,
            message=[],
        ),
        runtime=runtime,
        bot=None,
        session_id="private_1",
        job=None,
        user_text="hi",
    )
    job = LlmJob(id="abcdef123456", group_key="private_1", ctx=ctx)
    ctx.job = job
    return job


async def test_task_exception_is_retrieved_and_logged(monkeypatch):
    """`_run` 抛异常时：异常被取走（无 'never retrieved'）且落 error 日志。"""
    pipeline = _pipeline()
    job = _job()
    logged: list[str] = []

    import logging

    holder = logging.getLogger("test.pipeline.task")
    holder.handlers.clear()
    holder.setLevel(logging.DEBUG)

    class _H(logging.Handler):
        def emit(self, record):
            logged.append(record.getMessage())

    holder.addHandler(_H())

    # PrefixLogger 是 LoggerAdapter，用一个包了 holder 的真 adapter
    from app.core.logger import PrefixLogger

    log = PrefixLogger(holder, "Main")

    import app.core.logger as logger_mod

    monkeypatch.setattr(logger_mod, "logger", log, raising=False)

    async def _boom(_job):
        raise AttributeError("'AgentConfig' object has no attribute 'set_session'")

    monkeypatch.setattr(pipeline, "_run", _boom)

    task = pipeline.submit  # 确认入口存在
    assert callable(task)

    # 直接走 create_task 路径（submit 需要 bot/event 装配，这里只验证回调）
    created = asyncio.create_task(_boom(job))
    pipeline._tasks.add(created)
    created.add_done_callback(lambda t: pipeline._on_task_done(t, job))

    await asyncio.sleep(0.05)

    assert any("任务异常终止" in line for line in logged), f"必须落日志，实际: {logged}"
    assert any("set_session" in line for line in logged), "日志要带上原始异常信息"
    assert not pipeline._tasks, "任务应从集合里移除"


async def test_cancelled_task_is_not_logged_as_error(monkeypatch):
    pipeline = _pipeline()
    job = _job()
    logged: list[str] = []

    import logging

    holder = logging.getLogger("test.pipeline.simple")
    holder.handlers.clear()
    holder.setLevel(logging.DEBUG)

    class _H(logging.Handler):
        def emit(self, record):
            logged.append(record.getMessage())

    holder.addHandler(_H())
    from app.core.logger import PrefixLogger

    log = PrefixLogger(holder, "Main")

    import app.core.logger as logger_mod

    monkeypatch.setattr(logger_mod, "logger", log, raising=False)

    async def _forever():
        await asyncio.sleep(10)

    task = asyncio.create_task(_forever())
    pipeline._tasks.add(task)
    task.add_done_callback(lambda t: pipeline._on_task_done(t, job))
    task.cancel()
    await asyncio.sleep(0.05)

    assert not any("任务异常终止" in line for line in logged), "取消不是异常"
    assert not pipeline._tasks


async def test_successful_task_logs_nothing(monkeypatch):
    pipeline = _pipeline()
    job = _job()
    logged: list[str] = []

    import logging

    holder = logging.getLogger("test.pipeline.simple")
    holder.handlers.clear()
    holder.setLevel(logging.DEBUG)

    class _H(logging.Handler):
        def emit(self, record):
            logged.append(record.getMessage())

    holder.addHandler(_H())
    from app.core.logger import PrefixLogger

    log = PrefixLogger(holder, "Main")

    import app.core.logger as logger_mod

    monkeypatch.setattr(logger_mod, "logger", log, raising=False)

    async def _ok():
        return None

    task = asyncio.create_task(_ok())
    pipeline._tasks.add(task)
    task.add_done_callback(lambda t: pipeline._on_task_done(t, job))
    await asyncio.sleep(0.05)

    assert not logged, f"成功不该记 error: {logged}"


def test_run_prologue_is_inside_try():
    """静态守卫：`_run` 里 prologue 不得再出现"try 之前的取属性"（防回归）。"""
    from pathlib import Path

    src = Path("app/agent/loop/pipeline.py").read_text(encoding="utf-8")
    start = src.index("async def _run(self, job: LlmJob)")
    body = src[start : start + 900]
    try_at = body.index("try:")
    prologue = body[:try_at]
    assert "job.ctx" in prologue  # 只允许这一句（finally 需要）
    assert prologue.count("=") <= 3, f"prologue 太多逻辑，应移进 try: {prologue!r}"
