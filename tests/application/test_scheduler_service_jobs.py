"""SchedulerService 的查询 / 改期 / 立即执行（对齐 AstrBot `cron_manager`）。

背景：此前只有 `register` / `unload` / `count` —— 模块与 WebUI 都不知道
"到底有哪些定时任务在跑"，也无法改期或手动触发一次。这三个方法补齐后，
「定时任务」才能作为一个模块自己管理的东西（`llm_schedule` 迁移前置）。
"""

from __future__ import annotations

import asyncio

import pytest

from app.application.cron_service import SchedulerService


@pytest.fixture
def scheduler():
    return SchedulerService()


async def test_list_jobs_filters_by_prefix(scheduler):
    hits: list[str] = []

    async def handler(tag):
        hits.append(tag)

    await scheduler.register("demo:1:08:00", "08:00", lambda: handler("a"))
    await scheduler.register("other:2:09:00", "09:00", lambda: handler("b"))

    rows = scheduler.list_jobs(prefix="demo:1")
    assert [r["key"] for r in rows] == ["demo:1:08:00"]
    assert rows[0]["time"] == "08:00"

    assert len(scheduler.list_jobs()) == 2
    await scheduler.shutdown()


async def test_update_job_reschedules_and_keeps_handler(scheduler):
    called: list[str] = []

    async def handler():
        called.append("hit")

    await scheduler.register("demo:1:08:00", "08:00", handler)
    assert await scheduler.update_job("demo:1:08:00", "09:30") is True

    rows = scheduler.list_jobs(prefix="demo:1")
    assert rows[0]["time"] == "09:30"

    # handler 仍是原来那个（改期不换实现）
    assert await scheduler.run_job_now("demo:1:08:00") is True
    assert called == ["hit"]
    await scheduler.shutdown()


async def test_update_job_unknown_key_returns_false(scheduler):
    assert await scheduler.update_job("nope", "09:00") is False


async def test_run_job_now_does_not_remove_schedule(scheduler):
    called: list[int] = []

    async def handler():
        called.append(1)

    await scheduler.register("demo:1:08:00", "08:00", handler)
    await scheduler.run_job_now("demo:1:08:00")
    await scheduler.run_job_now("demo:1:08:00")

    assert called == [1, 1]
    assert scheduler.count() == 1, "立即执行不该把任务从表里摘掉"
    await scheduler.shutdown()


async def test_run_job_now_supports_sync_handler(scheduler):
    called: list[int] = []

    def handler():
        called.append(1)

    await scheduler.register("demo:1:08:00", "08:00", handler)
    assert await scheduler.run_job_now("demo:1:08:00") is True
    assert called == [1]
    await scheduler.shutdown()


async def test_run_job_now_swallows_handler_exception(scheduler):
    async def boom():
        raise RuntimeError("炸了")

    await scheduler.register("demo:1:08:00", "08:00", boom)
    assert await scheduler.run_job_now("demo:1:08:00") is False
    assert scheduler.count() == 1, "异常不该把任务摘掉"
    await scheduler.shutdown()


async def test_run_job_now_unknown_key(scheduler):
    assert await scheduler.run_job_now("nope") is False


async def test_unload_module_clears_all_its_jobs(scheduler):
    async def handler():
        return None

    await scheduler.register("demo:1:08:00", "08:00", handler)
    await scheduler.register("demo:1:12:00", "12:00", handler)
    await scheduler.unload_module("demo", 1)
    assert scheduler.list_jobs(prefix="demo:1") == []
    await asyncio.sleep(0)
    await scheduler.shutdown()
