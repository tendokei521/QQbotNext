"""定时任务测试（SchedulerService 精确到点触发）。"""

import asyncio
from datetime import datetime, timedelta

from app.services.scheduler import SchedulerService


class _FakeModule:
    """模拟带 SCHEDULES 的模块实例。SCHEDULES 可在类或实例上声明。"""

    def __init__(self, module_name, bot_id, schedules, calls=None):
        self.module_name = module_name
        self.bot_id = bot_id
        self.SCHEDULES = schedules
        self._calls = calls

    async def handler(self):
        if self._calls is not None:
            self._calls.append(1)


async def test_schedule_fires_at_time():
    """调度到 ~2s 后触发一次；注销后不再重复。"""
    calls = []
    s = SchedulerService(log=None)
    future = datetime.now() + timedelta(seconds=2)
    await s.register_module(_FakeModule("test", 1, {future.strftime("%H:%M:%S"): "handler"}, calls))

    await asyncio.sleep(3)
    assert len(calls) == 1, f"应触发一次，实际 {len(calls)}"

    await s.unload_module("test", 1)
    assert s.count() == 0


async def test_register_only_for_real_bot():
    """bot_id 为 None（全局实例）时不注册定时任务。"""
    s = SchedulerService(log=None)
    n = await s.register_module(_FakeModule("test", None, {"05:00:00": "handler"}, []))
    assert n == 0
    assert s.count() == 0


async def test_unload_stops_firing():
    """注销后不再触发。"""
    calls = []
    s = SchedulerService(log=None)
    future = datetime.now() + timedelta(seconds=2)
    await s.register_module(_FakeModule("test", 1, {future.strftime("%H:%M:%S"): "handler"}, calls))
    await s.unload_module("test", 1)
    await asyncio.sleep(1.5)
    assert calls == []
    assert s.count() == 0


async def test_multiple_bots_same_module_independent():
    """同一模块的两个 bot 各注册各的任务，互不覆盖。"""
    s = SchedulerService(log=None)
    await s.register_module(_FakeModule("m", 123, {"05:00:00": "handler"}, []))
    await s.register_module(_FakeModule("m", 456, {"05:00:00": "handler"}, []))
    assert s.count() == 2


def test_cron_field_matching():
    from app.services.scheduler import ScheduledTask

    assert ScheduledTask._match_cron_field("*", 30) is True
    assert ScheduledTask._match_cron_field("*/10", 30) is True
    assert ScheduledTask._match_cron_field("*/10", 35) is False
    assert ScheduledTask._match_cron_field("5-10", 7) is True
    assert ScheduledTask._match_cron_field("5-10", 12) is False
    assert ScheduledTask._match_cron_field("0,15,30", 30) is True
    assert ScheduledTask._match_cron_field("0,15,30", 20) is False
    assert ScheduledTask._match_cron_field("5", 5) is True


def test_cron_next_time():
    from datetime import datetime, timedelta

    from app.services.scheduler import ScheduledTask

    now = datetime.now()
    # 下一分钟触发
    future_minute = (now.minute + 1) % 60
    cron = f"{future_minute} * * * *"
    task = ScheduledTask("t", cron, lambda: None)
    delta = task._next_cron()
    assert 0 < delta < 120, f"应在一两分钟内触发，实际 {delta:.0f}s"

    # 每天固定时间（05:00）→ 距明天 05:00 或今天 05:00 的差值
    task2 = ScheduledTask("t2", "0 5 * * *", lambda: None)
    d2 = task2._next_cron()
    assert 0 < d2 < 86400, f"每日 5 点应在 24h 内触发，实际 {d2:.0f}s"

    # 非法字段不抛异常（兜底返回大值）
    task3 = ScheduledTask("t3", "99 25 * * *", lambda: None)
    assert task3._next_cron() >= 0
