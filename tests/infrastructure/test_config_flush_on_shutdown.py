"""关停时最后一次配置落盘不能丢。

背景：同步写接口（`ModuleConfig.set` / `set_module_authority`）把落库排成后台任务
（owner="config"）。`bootstrap.shutdown_services()` 原来直接 `cancel_all_await`：
任务在 0.5s 重试 sleep 中被取消，既不写库、也不报错；`persist_with_retry` 的
`except Exception` 也抓不到 `CancelledError`（BaseException）——最后一次配置修改
静默丢失。

修法：`ConfigService.flush_pending()` 在取消前先等落盘任务写完；
`persist_with_retry` 额外处理 CancelledError（尽力写最后一次再传播取消）。
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.bootstrap import shutdown_services
from app.core.task_manager import TaskManager
from app.infrastructure.config.config_service import ConfigService
from app.infrastructure.config.config_writes import persist_with_retry
from app.infrastructure.persistence.database import Database


class _FakeGateway:
    async def shutdown(self) -> None:
        pass


class _FakeAgentManager:
    async def shutdown(self) -> None:
        pass


class _FakeRecorder:
    async def close(self) -> None:
        pass


def _binding_sql() -> tuple[str, tuple]:
    return (
        "SELECT enabled FROM bindings "
        "WHERE capability_id = ? AND scope_type = ? AND scope_id = ?",
        ("demo", "global", ""),
    )


async def test_flush_pending_writes_scheduled_sync_config(tmp_path):
    db = Database(tmp_path / "flush.db")
    await db.connect()
    config_service = ConfigService(db, tmp_path / "root", task_manager=TaskManager())
    await config_service.init()
    try:
        # 同步写路径：内存立即生效，落库排成后台任务
        config_service.set_binding("demo", "global", enabled=True)

        await config_service.flush_pending(timeout=2.0)

        row = await db.fetchone(*_binding_sql())
        assert row is not None and row["enabled"] == 1, "flush_pending 返回时配置必须已落库"
    finally:
        await db.close()


async def test_shutdown_services_flushes_config_before_db_close(tmp_path):
    """完整关停链路：取消后台任务之前先 flush，关库后重开仍能读到配置。"""
    path = tmp_path / "shutdown.db"
    db = Database(path)
    await db.connect()
    task_manager = TaskManager()
    config_service = ConfigService(db, tmp_path / "root", task_manager=task_manager)
    await config_service.init()

    config_service.set_binding("demo", "global", enabled=True)  # 排队等待落库
    await shutdown_services(
        bot_service=_FakeGateway(),
        agent_manager=_FakeAgentManager(),
        task_manager=task_manager,
        stat_recorder=_FakeRecorder(),
        db=db,
        config_service=config_service,
        cancel_timeout=1.0,
    )

    # 库已关闭 → 重开一份验证写入确实发生（没被 cancel 吞掉）
    reopened = Database(path)
    await reopened.connect()
    try:
        row = await reopened.fetchone(*_binding_sql())
        assert row is not None and row["enabled"] == 1
    finally:
        await reopened.close()


async def test_cancelled_persist_still_writes_one_last_time():
    """任务在重试 sleep 中被取消：仍要尽力写最后一次，再让取消继续传播。"""
    calls: list[int] = []

    async def persist() -> None:
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise RuntimeError("磁盘满了")  # 第一次失败 → 进入 0.5s 重试等待

    task = asyncio.create_task(
        persist_with_retry(persist, (), logging.getLogger("test.config.persist"))
    )
    for _ in range(20):
        await asyncio.sleep(0)
        if calls:
            break
    assert calls == [1]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls == [1, 2], "取消时也必须尝试落盘最后一次，不能静默丢配置"
