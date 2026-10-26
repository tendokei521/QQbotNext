"""生命周期门控：模块开关必须同时拦住「事件之外」的运行路径。

覆盖（docs/multi-account-design.md §7）：
- 账号上线：禁用模块不调用 on_online、不注册定时任务；
- 账号离线：禁用模块不调用 on_offline；
- 调度器纵深防御：register_module 对禁用模块直接返回 0；
- 动态每日任务：register_daily_schedule 对禁用模块先注销、不注册；
- 装载期：register_data_sources 在线装配时跳过禁用模块；
- 运行期启用：已在线账号补调 on_online。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.plugin.loader as loader
from app.application.cron_service import SchedulerService
from app.core.logger import logger
from app.plugin.api import register_daily_schedule
from app.plugin.registry import ModuleRegistry
from app.webui.api.modules import _sync_module_runtime


class _Authority:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled


class _Scheduler:
    def __init__(self) -> None:
        self.registered: list[str] = []
        self.unloaded: list[tuple[str, str]] = []

    async def register_module(self, module) -> int:
        self.registered.append(module.module_name)
        return 1

    async def unload_module(self, module_name: str, bot_id) -> None:
        self.unloaded.append((module_name, str(bot_id)))


class _Module:
    def __init__(self, name: str, enabled: bool, bot_id: str = "acc1") -> None:
        self.module_name = name
        self.bot_id = bot_id
        self.parent = None
        self.authority = _Authority(enabled)
        self.online_calls = 0
        self.offline_calls = 0
        self.load_calls = 0
        self.unload_calls = 0
        self.ctx = SimpleNamespace(services=SimpleNamespace(runtime=lambda _bid: SimpleNamespace(online=True)))

    async def on_online(self) -> None:
        self.online_calls += 1

    async def on_offline(self) -> None:
        self.offline_calls += 1

    async def on_load(self) -> None:
        self.load_calls += 1

    async def on_unload(self) -> None:
        self.unload_calls += 1


def _registry(tmp_path, scheduler) -> ModuleRegistry:
    registry = ModuleRegistry(
        plugins_dir=tmp_path / "plugins",
        config_service=SimpleNamespace(),
        services=SimpleNamespace(scheduler=scheduler),
    )
    return registry


# ==================== registry.notify_online / offline ====================

async def test_notify_online_skips_disabled_modules(tmp_path):
    scheduler = _Scheduler()
    registry = _registry(tmp_path, scheduler)
    enabled = _Module("enabled_mod", True)
    disabled = _Module("disabled_mod", False)
    registry._modules = {
        enabled.module_name: {"acc1": enabled},
        disabled.module_name: {"acc1": disabled},
    }

    armed = await registry.notify_online("acc1")

    assert armed == 1
    assert enabled.online_calls == 1
    assert disabled.online_calls == 0
    assert scheduler.registered == ["enabled_mod"]


async def test_notify_offline_skips_disabled_modules(tmp_path):
    scheduler = _Scheduler()
    registry = _registry(tmp_path, scheduler)
    enabled = _Module("enabled_mod", True)
    disabled = _Module("disabled_mod", False)
    registry._modules = {
        enabled.module_name: {"acc1": enabled},
        disabled.module_name: {"acc1": disabled},
    }

    stopped = await registry.notify_offline("acc1")

    assert stopped == 1
    assert enabled.offline_calls == 1
    assert disabled.offline_calls == 0
    assert scheduler.unloaded == [("enabled_mod", "acc1")]


# ==================== SchedulerService 纵深防御 ====================

class _ScheduledModule:
    def __init__(self, enabled: bool) -> None:
        self.module_name = "cron_mod"
        self.bot_id = "acc1"
        self.authority = _Authority(enabled)
        self.SCHEDULES = {"00:00": "tick"}

    async def tick(self) -> None:  # pragma: no cover - 只验证注册，不真跑
        pass


async def test_scheduler_does_not_register_disabled_module():
    scheduler = SchedulerService()
    disabled = _ScheduledModule(False)

    assert await scheduler.register_module(disabled) == 0
    assert scheduler.list_jobs("cron_mod:acc1") == []


async def test_scheduler_registers_enabled_module():
    scheduler = SchedulerService()
    enabled = _ScheduledModule(True)
    try:
        assert await scheduler.register_module(enabled) == 1
        assert [job["key"] for job in scheduler.list_jobs("cron_mod:acc1")] == [
            "cron_mod:acc1:00:00"
        ]
    finally:
        await scheduler.unload_module("cron_mod", "acc1")


# ==================== register_daily_schedule 门控 ====================

class _Config:
    def __init__(self, values: dict) -> None:
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


class _OnlineRuntime:
    online = True


class _DailyModule:
    module_name = "daily_mod"
    bot_id = "acc1"

    def __init__(self, enabled: bool, scheduler) -> None:
        self.authority = _Authority(enabled)
        self.config = _Config({"enable": True, "time": "08:00"})
        self.ctx = SimpleNamespace(services=SimpleNamespace(scheduler=scheduler))

    def _runtime(self):
        return _OnlineRuntime()


class _RegisteringScheduler:
    def __init__(self) -> None:
        self.registered: list[tuple[str, str]] = []
        self.unloaded: list[tuple[str, str]] = []

    async def register(self, key: str, time_str: str, handler) -> None:
        self.registered.append((key, time_str))

    async def unload_module(self, module_name: str, bot_id) -> None:
        self.unloaded.append((module_name, str(bot_id)))


async def test_daily_schedule_skips_disabled_module():
    scheduler = _RegisteringScheduler()
    module = _DailyModule(False, scheduler)

    ok = await register_daily_schedule(
        module, key_suffix="daily", enable_key="enable", time_key="time",
        handler_factory=lambda: (lambda: None),
    )

    assert ok is False
    assert scheduler.registered == []
    assert scheduler.unloaded == [("daily_mod", "acc1")]


async def test_daily_schedule_registers_enabled_module():
    scheduler = _RegisteringScheduler()
    module = _DailyModule(True, scheduler)

    ok = await register_daily_schedule(
        module, key_suffix="daily", enable_key="enable", time_key="time",
        handler_factory=lambda: (lambda: None),
    )

    assert ok is True
    assert scheduler.registered == [("daily_mod:acc1:daily", "08:00")]


# ==================== loader.register_data_sources 门控 ====================

class _LoaderModule(_Module):
    SCHEDULES = {"00:00": "tick"}

    def missing_requirements(self):
        return []

    async def tick(self) -> None:  # pragma: no cover
        pass


async def test_online_load_skips_scheduler_for_disabled_module(tmp_path):
    scheduler = _Scheduler()
    services = SimpleNamespace(providers=None, scheduler=scheduler)
    disabled = _LoaderModule("loader_mod", False)

    await loader.register_data_sources(disabled, "loader_mod", services, logger, online=True)

    assert scheduler.registered == []


async def test_online_load_registers_enabled_module(tmp_path):
    scheduler = _Scheduler()
    services = SimpleNamespace(providers=None, scheduler=scheduler)
    enabled = _LoaderModule("loader_mod", True)

    await loader.register_data_sources(enabled, "loader_mod", services, logger, online=True)

    assert scheduler.registered == ["loader_mod"]


# ==================== 运行期启用：在线时补 on_online ====================

@pytest.mark.parametrize("enabled", [True, False])
async def test_runtime_toggle_calls_matching_lifecycle(enabled):
    module = _Module("toggle_mod", enabled)

    await _sync_module_runtime(module, enabled)

    if enabled:
        assert module.load_calls == 1
        assert module.online_calls == 1
    else:
        assert module.offline_calls == 1
        assert module.unload_calls == 1
