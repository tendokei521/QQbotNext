"""关停顺序：后台任务 finally 里的 DB 写入必须早于 `db.close()`。

历史问题（`app/bootstrap.py` 的退出路径）：

    task_manager.cancel_all()      # 只 cancel，不 await
    await stat_recorder.close()
    await db.close()               # 任务 finally 可能还没跑完 → 写入抛
                                   # "Cannot operate on a closed database"

修法：抽出 `shutdown_services()` 固定顺序
网关 → Agent/模块运行时 → 任务（await 取消，超时 5s）→ 统计 → DB。
"""

from __future__ import annotations

import asyncio
import time

from app.bootstrap import shutdown_services
from app.core.task_manager import TaskManager


class _FakeDB:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.closed = False

    async def execute(self, sql: str) -> None:
        assert not self.closed, "DB 已关闭后仍在写入：任务 finally 没被等到"
        self.events.append("db:write")

    async def close(self) -> None:
        self.events.append("db:close")
        self.closed = True


class _FakeGateway:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def shutdown(self) -> None:
        self.events.append("gateway:shutdown")


class _FakeAgentManager:
    """AgentManager.shutdown 已改 async：它会 await 全部 runtime（含 LLM 回合任务）。"""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def shutdown(self) -> None:
        self.events.append("agent:shutdown")


class _FakeRecorder:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def close(self) -> None:
        self.events.append("stats:close")


async def test_task_finally_db_write_finishes_before_database_close():
    events: list[str] = []
    db = _FakeDB(events)
    task_manager = TaskManager()

    async def worker() -> None:
        try:
            await asyncio.sleep(30)
        finally:
            # 真实场景：统计/会话的最后一笔写入就发生在这里
            await db.execute("INSERT INTO stat_event ...")
            events.append("task:finally")

    task_manager.create_task(worker(), name="stats-flush", owner="framework")
    await asyncio.sleep(0)  # 让任务真正进入 sleep

    await shutdown_services(
        bot_service=_FakeGateway(events),
        agent_manager=_FakeAgentManager(events),
        task_manager=task_manager,
        stat_recorder=_FakeRecorder(events),
        db=db,
        cancel_timeout=2.0,
    )

    assert "task:finally" in events
    assert events.index("db:write") < events.index("db:close"), "DB 写必须早于关库"
    assert events.index("gateway:shutdown") < events.index("agent:shutdown") < events.index(
        "task:finally"
    ) < events.index("stats:close") < events.index("db:close")
    assert task_manager.count() == 0


async def test_cancel_all_await_times_out_instead_of_hanging_shutdown():
    """任务无视取消时，关停最多等 cancel_timeout，之后照常关库（不能永久卡住）。"""
    events: list[str] = []
    db = _FakeDB(events)
    task_manager = TaskManager()
    started = asyncio.Event()

    async def stubborn() -> None:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            await asyncio.sleep(0.3)  # 故意拖过超时时间

    task_manager.create_task(stubborn(), name="stubborn", owner="framework")
    await started.wait()

    begin = time.monotonic()
    await shutdown_services(
        bot_service=_FakeGateway(events),
        agent_manager=_FakeAgentManager(events),
        task_manager=task_manager,
        stat_recorder=_FakeRecorder(events),
        db=db,
        cancel_timeout=0.05,
    )
    elapsed = time.monotonic() - begin

    assert elapsed < 1.0, f"关停被无视取消的任务卡住了 {elapsed:.2f}s"
    assert db.closed is True
    assert "stats:close" in events and "db:close" in events

    await asyncio.sleep(0.4)  # 让 stubborn 自己退出，避免留下 pending task
