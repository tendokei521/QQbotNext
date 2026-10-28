"""定时任务下次执行时间（next_run）与墙钟跳变回归测试。

真实缺陷：``list_jobs`` 读 ``task.next_run``，但 ``ScheduledTask`` 从未设置它 →
WebUI 永远看不到「下次几点跑」。另：cron 目标时刻缓存没有墙钟漂移检测，
NTP 校时 / 休眠唤醒后仍按旧墙钟复用目标，导致下一次触发时间算错。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from app.application.cron_service import CRON_CACHE_MAX_DRIFT, ScheduledTask, SchedulerService


class _NullLog:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, msg: str, *_a, **_k) -> None:
        self.errors.append(str(msg))

    def warning(self, msg: str, *_a, **_k) -> None:
        self.errors.append(str(msg))

    debug = warning
    info = warning


async def _noop() -> None:
    pass


async def test_list_jobs_exposes_next_run_epoch():
    """注册后 list_jobs 必须带 next_run（epoch 秒），且等于期望的目标时刻。"""
    expected = (datetime.now() + timedelta(hours=1)).replace(minute=1, second=2, microsecond=0)
    time_str = expected.strftime("%H:%M:%S")

    service = SchedulerService()
    await service.register("mod:main:daily", time_str, _noop)
    try:
        jobs = service.list_jobs()
        assert len(jobs) == 1
        row = jobs[0]
        assert row["key"] == "mod:main:daily"
        assert row["time"] == time_str
        next_run = row["next_run"]
        assert isinstance(next_run, int), f"next_run 应为 epoch 秒，实际 {next_run!r}"
        assert datetime.fromtimestamp(next_run) == expected, "next_run 应等于下次目标时刻"
        assert next_run > int(time.time()), "next_run 必须在未来"
    finally:
        await service.shutdown()


async def test_cron_task_next_run_lands_on_cron_slot():
    task = ScheduledTask("cron", "*/5 * * * *", _noop, log=_NullLog())
    await task.start()
    try:
        assert task.next_run is not None
        target = datetime.fromtimestamp(task.next_run)
        assert target.minute % 5 == 0, f"next_run 应落在 5 分钟整点，实际 {target}"
        assert target.second == 0
        assert (target - datetime.now()).total_seconds() > 0
    finally:
        await task.stop()


async def test_next_run_refreshes_on_each_next():
    """每次 _next() 都要刷新 next_run（触发后必须前进，不能停在旧值）。"""
    task = ScheduledTask("cron", "*/5 * * * *", _noop, log=_NullLog())
    task._next()
    first = task.next_run
    assert first is not None
    task._cron_target = None  # 模拟已触发：强制下一次重新计算
    task._next()
    assert task.next_run is not None and task.next_run >= first


def test_cron_cache_reused_without_wall_clock_drift():
    task = ScheduledTask("cron", "*/5 * * * *", _noop, log=_NullLog())
    first = task._next_cron_target()
    assert task._cron_target == first and task._cron_cached_at is not None
    assert task._next_cron_target() == first, "未发生漂移时应复用缓存目标"


def test_cron_cache_discarded_on_wall_clock_drift():
    """墙钟跳变（NTP/休眠）超过阈值 → 丢弃缓存目标并按当前墙钟重算。"""
    task = ScheduledTask("cron", "*/5 * * * *", _noop, log=_NullLog())
    task._next_cron_target()
    cached_at = task._cron_cached_at
    assert cached_at is not None

    # 模拟「上一次计算发生在 2 分钟前」→ 与当前墙钟的差超过允许漂移
    stale_cached_at = cached_at - timedelta(seconds=CRON_CACHE_MAX_DRIFT + 60)
    task._cron_cached_at = stale_cached_at

    target = task._next_cron_target()

    assert target > datetime.now() - timedelta(seconds=1), "重算后目标必须在未来"
    assert task._cron_cached_at is not None
    assert task._cron_cached_at > stale_cached_at + timedelta(seconds=CRON_CACHE_MAX_DRIFT), \
        "检测到墙钟跳变后必须用当前墙钟刷新缓存计算时刻"
    assert task._cron_target == target


def test_cron_recomputes_when_cached_target_already_passed():
    task = ScheduledTask("cron", "*/5 * * * *", _noop, log=_NullLog())
    task._cron_target = datetime.now() - timedelta(seconds=5)
    task._cron_cached_at = datetime.now()

    target = task._next_cron_target()

    assert target > datetime.now(), "过期目标必须重新计算"


async def test_invalid_time_does_not_leave_running_task():
    """非法时间：start() 记错误并保持 stopped（不得留下永不触发的空转任务）。"""
    log = _NullLog()
    task = ScheduledTask("bad", "99:99", _noop, log=log)
    await task.start()
    assert task.running is False
    assert task._task is None
    assert task.next_run is None
    assert log.errors and "时间解析错误" in log.errors[0]
