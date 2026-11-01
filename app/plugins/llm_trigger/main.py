"""触发与调度模块：接管「主动消息」（17 键）与「定时任务」（3 键）两组策略与驱动。

## 为什么合成一个模块

两者是同一件事的两面——**谁在什么时候发起一轮发言**：主动按沉默/随机间隔开口，定时按到点
开口。实现同构（都建管理器、都在 `on_online` 武装计时器、`on_offline` 停掉、共用流式发送器），
且定时原本就直接 import 主动的 `entry` / `stream`；合并后跨插件 import 变成包内相对导入。

## 框架侧只保留两件事

1. **通知**：用户/ Bot 说话了 → `llm_hooks` 的 `message_observed` / `bot_sent`；
2. **原料**：`send_to` / `llm_turn` / `spawn` / `sessions`。

## 实现方式

复用框架的 `ProactiveManager` / `TaskScheduler`（计时器与状态机是机制），但**用本模块配置**
驱动：`ConfigView` 把模块配置投影成运行时视图，`build_manager` / `build_scheduler` 拿到本模块实例。
数据位置不变（`data/llm/<bot_id>/proactive_data.json`、`tasks_data.json`）。

定时协议与主动性引导两段系统提示词也归本模块（`src/prompt.py` + `src/wiring.py`）：
模块禁用/卸载时整段消失，不会出现"提示词说有能力、调度器却已不在"的悬空说明。
"""

from __future__ import annotations

from typing import Any

from app.plugin import BaseModule

from .config_schema import SCHEMA
from .service import ScheduleToolSource, build_manager, build_scheduler

#: 主动消息持有的键（原 `llm_proactive` 的 OWNED_KEYS，键名一字未改；合并后仍与
#: `config_partition.MODULE_KEYS["llm_trigger"]` 的前 17 键一致）
PROACTIVE_OWNED_KEYS: tuple[str, ...] = (
    "proactive_friend_enable",
    "proactive_group_enable",
    "stream_proactive_enabled",
    "proactive_friend_sessions",
    "proactive_group_sessions",
    "proactive_min_interval_minutes",
    "proactive_max_interval_minutes",
    "proactive_max_unanswered",
    "proactive_quiet_hours_start",
    "proactive_quiet_hours_end",
    "proactive_group_idle_minutes",
    "proactive_prompt",
    "proactive_prompt_enable",
    "proactive_history_intent_nudge",
    "proactive_env_prompt_enable",
    "proactive_unresolved_prompt_enable",
    "proactive_env_intent_nudge",
)

#: 定时任务持有的键（原 `llm_schedule` 的 OWNED_KEYS，键名一字未改；合并后仍与
#: `config_partition.MODULE_KEYS["llm_trigger"]` 的后 3 键一致）
SCHEDULE_OWNED_KEYS: tuple[str, ...] = (
    "schedule_enable",
    "schedule_prompt",
    "stream_scheduled_enabled",
)

#: 本模块持有的键 = 主动 17 键 + 定时 3 键（键名一字未改）
OWNED_KEYS: tuple[str, ...] = PROACTIVE_OWNED_KEYS + SCHEDULE_OWNED_KEYS


class Module(BaseModule):
    name = "触发与调度"
    sign = "LlmTrigger"
    description = "谁在什么时候发起发言：主动消息（私聊随机 / 群聊沉默后开口，支持免打扰）+ 定时任务（每天 / 每周 / 每月）"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    order = 80
    default_config = {
        "proactive_friend_enable": False,
        "proactive_group_enable": False,
        "stream_proactive_enabled": False,
        "proactive_friend_sessions": [],
        "proactive_group_sessions": [],
        "proactive_min_interval_minutes": 30,
        "proactive_max_interval_minutes": 900,
        "proactive_max_unanswered": 3,
        "proactive_quiet_hours_start": 1,
        "proactive_quiet_hours_end": 7,
        "proactive_group_idle_minutes": 10,
        "proactive_prompt": "",
        "proactive_prompt_enable": True,
        "proactive_history_intent_nudge": True,
        "proactive_env_prompt_enable": True,
        "proactive_unresolved_prompt_enable": True,
        "proactive_env_intent_nudge": True,
        "schedule_enable": True,
        "schedule_prompt": "",
        "stream_scheduled_enabled": False,
    }
    config_schema = SCHEMA

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._manager: Any = None
        self._scheduler: Any = None

    async def on_load(self) -> None:
        """装配（账号一创建就会发生，此时还没连上 QQ）：只建管理器与调度器，**不武装计时器**。"""
        runtime = self._runtime()
        if runtime is None:
            return
        from app.agent.delivery.output import ConfigView

        # 两个视图各自保留原 owned 子集（主动 17 键 / 定时 3 键）：多给的键会改变框架侧
        # "读不到就回落"的语义。文档化句柄供 WebUI 按鸭子类型读。
        runtime.proactive_options = ConfigView(
            getattr(runtime, "config", None), self.config, PROACTIVE_OWNED_KEYS
        )
        self._manager = build_manager(self)
        runtime.proactive_service = self._manager
        # **订阅**框架广播的两个事实（框架不认识"主动消息"）：
        runtime.llm_hooks.register(
            stage="message_observed", event_type="*", order=80,
            handler=self._on_message_observed, module=self,
        )
        runtime.llm_hooks.register(
            stage="bot_sent", event_type="*", order=80,
            handler=self._on_bot_sent, module=self,
        )

        runtime.schedule_options = ConfigView(
            getattr(runtime, "config", None), self.config, SCHEDULE_OWNED_KEYS
        )
        self._scheduler = build_scheduler(self)
        from app.agent.loop.providers import register_tool_source

        register_tool_source(runtime, ScheduleToolSource(self))
        runtime.scheduler = self._scheduler

        # 定时协议 / 主动性引导两段系统提示词归本模块（细节见 src/wiring.py）
        from .src.wiring import register_prompt_sections

        register_prompt_sections(runtime)

    async def on_online(self) -> None:
        """账号连上：武装主动消息计时器与定时任务计时器（离线期间一条都不会发）。"""
        if self._manager is not None:
            self._manager.start()
        if self._scheduler is not None:
            self._scheduler.start()

    async def on_offline(self) -> None:
        """账号断开：停掉计时器，避免断线后继续"主动"发言 / 对着死连接发消息。"""
        if self._manager is not None:
            self._manager.stop()
        if self._scheduler is not None:
            self._scheduler.stop()

    async def _on_message_observed(self, ctx, msg=None) -> None:
        """有人说话了：重置沉默计时 / 安排下次主动发言（实现见 `src/proactive/hooks.py`）。"""
        from .src.proactive.hooks import on_message_observed

        await on_message_observed(self._manager, ctx, self.bot_id)

    async def _on_bot_sent(self, ctx, msg=None) -> None:
        """Bot 发过话了：重置计时（实现见 `src/proactive/hooks.py`）。"""
        from .src.proactive.hooks import on_bot_sent

        await on_bot_sent(self._manager, ctx, self.bot_id)

    async def on_unload(self) -> None:
        from .src.lifecycle import stop_quietly

        stop_quietly(self._manager, "Proactive")
        stop_quietly(self._scheduler, "Schedule")
        self._manager = None
        self._scheduler = None
        runtime = self._runtime()
        if runtime is None:
            return
        from .src.wiring import unregister_prompt_sections

        unregister_prompt_sections(runtime)
        runtime.proactive_service = None
        runtime.proactive_options = None
        runtime.scheduler = None
        runtime.schedule_options = None

    def _runtime(self) -> Any:
        services = getattr(getattr(self, "ctx", None), "services", None)
        getter = getattr(services, "runtime", None)
        return getter(self.bot_id) if callable(getter) else None

    @property
    def manager(self) -> Any:
        """当前主动消息计时器实例（诊断/测试用）。"""
        return self._manager

    @property
    def scheduler(self) -> Any:
        """当前定时任务调度器实例（诊断/测试用）。"""
        return self._scheduler

    @property
    def enabled(self) -> bool:
        """定时任务总闸（`schedule_enable`）。"""
        return bool(self.config.get("schedule_enable", True))

    @property
    def facade(self) -> Any:
        """运行时门面（诊断用）。"""
        from app.agent.policy import RuntimeFacade

        return RuntimeFacade


def owned_keys() -> tuple[str, ...]:
    """本模块持有的配置键（供迁移脚本与契约测试核对）。"""
    return OWNED_KEYS
