"""定时任务模块：接管「定时任务」这组策略与驱动（3 键）。

## 它负责什么

`schedule_enable` / `schedule_prompt` / `stream_scheduled_enabled` —— 原来在
agent 配置页，现在归本模块。到点做什么由模块自己的 `SCHEDULES` 或
`ctx.services.scheduler` 决定（框架只提供定时服务本身）。

## 实现方式

与主动消息同构：复用框架的 `TaskScheduler`（计时/持久化是机制），
但用本模块配置驱动它（`RuntimeFacade` 把 `config` 换成模块视图）。
"""

from __future__ import annotations

from typing import Any

from app.plugin import BaseModule

from .config_schema import SCHEMA
from .service import build_scheduler

#: 本模块持有的键（与 `config_partition.MODULE_KEYS["llm_schedule"]` 一致）
OWNED_KEYS: tuple[str, ...] = (
    "schedule_enable",
    "schedule_prompt",
    "stream_scheduled_enabled",
)


class Module(BaseModule):
    name = "定时任务"
    sign = "LlmSchedule"
    description = "到点自动发言（每天 / 每周 / 每月）"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    order = 90
    default_config = {
        "schedule_enable": True,
        "schedule_prompt": "",
        "stream_scheduled_enabled": False,
    }
    config_schema = SCHEMA

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._scheduler: Any = None

    async def on_load(self) -> None:
        """装配（账号一创建就会发生，此时还没连上 QQ）：只构建调度器，**不武装计时器**。"""
        runtime = self._runtime()
        if runtime is None:
            return
        from app.agent.delivery.output import ConfigView

        runtime.schedule_options = ConfigView(
            getattr(runtime, "config", None), self.config, OWNED_KEYS
        )
        self._scheduler = build_scheduler(self)
        from app.agent.loop.providers import register_tool_source

        from .service import ScheduleToolSource

        register_tool_source(runtime, ScheduleToolSource(self))
        runtime.scheduler = self._scheduler

    async def on_online(self) -> None:
        """账号连上：武装定时器。离线期间计时器一律不启动（`TaskScheduler.start`）。"""
        if self._scheduler is not None:
            self._scheduler.start()

    async def on_offline(self) -> None:
        """账号断开：停掉定时器——否则会对着死连接继续发消息。"""
        if self._scheduler is not None:
            self._scheduler.stop()

    async def on_unload(self) -> None:
        runtime = self._runtime()
        if self._scheduler is not None:
            try:
                self._scheduler.stop()
            except Exception as e:  # noqa: BLE001
                from app.core.logger import logger

                logger.debug(f"[Schedule] 停止调度器失败（已忽略）: {e}")
            self._scheduler = None
        if runtime is None:
            return
        runtime.scheduler = None
        runtime.schedule_options = None

    def _runtime(self) -> Any:
        services = getattr(getattr(self, "ctx", None), "services", None)
        getter = getattr(services, "runtime", None)
        return getter(self.bot_id) if callable(getter) else None

    @property
    def scheduler(self) -> Any:
        return self._scheduler

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("schedule_enable", True))


def owned_keys() -> tuple[str, ...]:
    """本模块持有的配置键（供迁移脚本与契约测试核对）。"""
    return OWNED_KEYS
