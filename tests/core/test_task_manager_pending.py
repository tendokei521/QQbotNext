"""TaskManager 超时取消后任务不得脱管。

历史问题：`cancel_owner_await` 超时只 warning 并 `pop` 掉 owner 登记；随后
`bootstrap.shutdown_services()` 关库，而超时任务还在跑（可能写已关闭的库），
且 `TaskManager` 里再也查不到"谁没停"。

修法：超时任务保留登记、日志打印任务名；新增 `pending_names()` 供关停告警。
"""

from __future__ import annotations

import asyncio

from app.core.task_manager import TaskManager


async def _stubborn() -> None:
    try:
        await asyncio.sleep(30)
    except asyncio.CancelledError:
        await asyncio.sleep(0.3)  # 故意拖过超时


async def test_timed_out_task_stays_registered():
    manager = TaskManager()
    manager.create_task(_stubborn(), name="stubborn", owner="framework")
    await asyncio.sleep(0)

    await manager.cancel_owner_await("framework", timeout=0.02)

    names = manager.pending_names()
    assert any("stubborn" in name for name in names), f"超时任务必须还能被查到: {names}"
    assert manager.count("framework") == 1

    await asyncio.sleep(0.4)  # 让它自己退出
    assert manager.pending_names() == []
    assert manager.count("framework") == 0


async def test_cancel_owner_await_clears_registration_when_all_finished():
    manager = TaskManager()

    async def quick() -> None:
        await asyncio.sleep(30)

    manager.create_task(quick(), name="quick", owner="module:a")
    await asyncio.sleep(0)

    assert await manager.cancel_owner_await("module:a", timeout=1.0) == 1
    assert manager.count("module:a") == 0
    assert manager.pending_names() == []


async def test_cancel_all_await_reports_leftover_tasks():
    manager = TaskManager()
    manager.create_task(_stubborn(), name="stuck-1", owner="module:a")
    manager.create_task(_stubborn(), name="stuck-2", owner="module:b")
    await asyncio.sleep(0)

    await manager.cancel_all_await(timeout=0.02)

    names = manager.pending_names()
    assert len(names) == 2, f"两个无视取消的任务都应保留登记: {names}"
    assert all("stuck-" in name for name in names)

    await asyncio.sleep(0.4)
    assert manager.pending_names() == []
