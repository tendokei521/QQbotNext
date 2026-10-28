"""关停链路的最后一段：LLM 回合任务必须被 **await** 结束，之后才允许关库。

背景：`LlmPipeline` 的回合任务由裸 `asyncio.create_task` 创建（不进 TaskManager），
`shutdown()` 原来只 cancel 不 await；`AgentRuntime.stop()` / `AgentManager.shutdown()`
也是同步调用。于是 `bootstrap.shutdown_services()` 的 `cancel_all_await` 等不到它们，
回合任务 finally 里的会话/统计写入会落在 `db.close()` 之后
（"Cannot operate on a closed database"）。

修法：pipeline.shutdown 变 async 并 `asyncio.wait(..., timeout=5s)`；
runtime.stop / manager.shutdown（含 bind_account / drop_runtime / sync_accounts）变 async。
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from app.agent.loop.pipeline import LlmPipeline
from app.agent.runtime import AgentManager
from app.bootstrap import shutdown_services
from app.core.task_manager import TaskManager


class _FakeDB:
    """记录写入与关闭顺序；关库后再写会直接失败。"""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.closed = False

    async def execute(self, _sql: str) -> None:
        assert not self.closed, "DB 已关闭后仍在写入：回合任务没被 await"
        self.events.append("db:write")

    async def close(self) -> None:
        self.events.append("db:close")
        self.closed = True


class _TaskMgr:
    def create_task(self, coro, **kw):
        return asyncio.get_running_loop().create_task(coro)


class _FakeGateway:
    def __init__(self, db: _FakeDB) -> None:
        self.db = db

    async def shutdown(self) -> None:
        self.db.events.append("gateway")


class _FakeRecorder:
    def __init__(self, db: _FakeDB) -> None:
        self.db = db

    async def close(self) -> None:
        self.db.events.append("stats")


class _PipelineRuntime:
    """只实现 AgentManager 关停所需接口的运行时替身。"""

    def __init__(self, pipeline: LlmPipeline) -> None:
        self.bot_id = "1"
        self.pipeline = pipeline

    async def stop(self, close_session: bool = True) -> None:
        await self.pipeline.shutdown()


def _pipeline() -> LlmPipeline:
    return LlmPipeline(SimpleNamespace(bot_id="1", config_service=None))


def _add_turn(pipeline: LlmPipeline, db: _FakeDB) -> asyncio.Task:
    """往 pipeline 里塞一个「正在跑、取消后 finally 写库」的回合任务。"""

    async def turn() -> None:
        try:
            await asyncio.sleep(30)
        finally:
            await db.execute("INSERT INTO stat_event ...")

    task = asyncio.create_task(turn())
    pipeline._tasks.add(task)
    task.add_done_callback(pipeline._tasks.discard)  # 对齐真实 _on_task_done
    return task


async def test_pipeline_shutdown_waits_for_turn_finally():
    db = _FakeDB()
    pipeline = _pipeline()
    task = _add_turn(pipeline, db)
    await asyncio.sleep(0)  # 让回合任务真正进 sleep

    await pipeline.shutdown()

    assert task.done(), "shutdown 返回时回合任务必须已经结束"
    assert db.events == ["db:write"], "回合任务 finally 的 DB 写必须在 shutdown 返回前完成"


async def test_pipeline_shutdown_times_out_instead_of_hanging():
    """无视取消的任务：最多等 shutdown_timeout，之后照常返回（只告警）。"""
    pipeline = _pipeline()
    pipeline.shutdown_timeout = 0.05
    started = asyncio.Event()

    async def stubborn() -> None:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            await asyncio.sleep(0.3)  # 故意拖过超时

    task = asyncio.create_task(stubborn())
    pipeline._tasks.add(task)
    await started.wait()

    begin = time.monotonic()
    await pipeline.shutdown()
    elapsed = time.monotonic() - begin

    assert elapsed < 1.0, f"shutdown 被无视取消的任务卡住 {elapsed:.2f}s"
    await asyncio.sleep(0.4)  # 让 stubborn 自己退出，避免 pending task


async def test_agent_manager_shutdown_awaits_every_runtime():
    db = _FakeDB()
    pipeline = _pipeline()
    task = _add_turn(pipeline, db)
    await asyncio.sleep(0)

    manager = AgentManager(config_service=None, task_manager=_TaskMgr())
    manager._runtimes = {"1": _PipelineRuntime(pipeline)}

    await manager.shutdown()

    assert task.done()
    assert db.events == ["db:write"]
    assert manager.get_runtime("1") is None


async def test_drop_runtime_awaits_soft_stop():
    """删号/换号回收也走 async stop（软停止：close_session=False）。"""
    class _Runtime:
        bot_id = "acc1"

        def __init__(self) -> None:
            self.stopped = False
            self.close_session: bool | None = None

        def set_online(self, _value: bool) -> None:
            pass

        async def stop(self, close_session: bool = True) -> None:
            await asyncio.sleep(0)
            self.stopped = True
            self.close_session = close_session

    manager = AgentManager(config_service=None, task_manager=_TaskMgr())
    runtime = _Runtime()
    manager._runtimes = {"acc1": runtime}

    await manager.drop_runtime("acc1", "测试删号")

    assert runtime.stopped is True
    assert runtime.close_session is False
    assert manager.get_runtime("acc1") is None


async def test_shutdown_services_closes_db_after_agent_turns_finish():
    """全链路顺序：网关 → Agent（等回合任务 finally）→ 统计 → DB。"""
    db = _FakeDB()
    pipeline = _pipeline()
    task = _add_turn(pipeline, db)
    await asyncio.sleep(0)

    manager = AgentManager(config_service=None, task_manager=_TaskMgr())
    manager._runtimes = {"1": _PipelineRuntime(pipeline)}

    await shutdown_services(
        bot_service=_FakeGateway(db),
        agent_manager=manager,
        task_manager=TaskManager(),
        stat_recorder=_FakeRecorder(db),
        db=db,
        cancel_timeout=1.0,
    )

    assert task.done()
    assert db.events == ["gateway", "db:write", "stats", "db:close"]
