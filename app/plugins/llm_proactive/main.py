"""主动消息模块：接管「主动消息」这组策略与驱动（17 键）。

## 为什么它是"纯业务模块"

"什么时候开口、隔多久、免打扰时段、最多追问几次"——全是业务判断，框架不该知道。
框架只保留两件事：

1. **通知**：用户说话了 / Bot 说话了 → `runtime.proactive.on_message()` /
   `on_bot_sent()`（pipeline 里已有，且有 `getattr(..., None)` 保护）；
2. **原料**：`send_to` / `llm_turn` / `spawn` / `sessions`（W1 已就绪）。

## 实现方式

复用框架里打磨过的 `ProactiveManager`（计时器和状态机是机制），但**用本模块的
配置**驱动它：给它一个 facade，`config` 指向本模块配置，其余属性透传到运行时。
于是 17 个 `proactive_*` 键彻底离开 agent 配置页。

## 数据位置不变

状态仍写 `data/llm/<bot_id>/proactive_data.json`，升级零迁移。
"""

from __future__ import annotations

from typing import Any

from app.plugin import BaseModule

from .config_schema import SCHEMA
from .service import build_manager

#: 本模块持有的键（与 `config_partition.MODULE_KEYS["llm_proactive"]` 一致）
OWNED_KEYS: tuple[str, ...] = (
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


class Module(BaseModule):
    name = "主动消息"
    sign = "LlmProactive"
    description = "私聊按随机间隔、群聊按沉默时长主动开口（含免打扰与追问上限）"
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
    }
    config_schema = SCHEMA

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._manager: Any = None

    async def on_load(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        from app.agent.delivery.output import ConfigView

        runtime.proactive_options = ConfigView(
            getattr(runtime, "config", None), self.config, OWNED_KEYS
        )
        self._manager = build_manager(self)
        # 文档化句柄：WebUI「主动消息」状态页按鸭子类型读它（不再塞 runtime.proactive）
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

    async def _on_message_observed(self, ctx, msg=None) -> None:
        """有人说话了：重置沉默计时 / 安排下次主动发言。"""
        if self._manager is None:
            return
        event = getattr(ctx, "event", None)
        if event is None:
            return
        try:
            await self._manager.on_message(
                ctx.session_id,
                getattr(event, "event_type", "") == "message_group",
                is_self=(getattr(event, "user_id", None) == getattr(event, "self_id", None)),
            )
        except Exception as e:  # noqa: BLE001 —— 业务异常不该毁掉整轮
            from app.core.logger import logger

            logger.add_info(f"#{self.bot_id}").debug(f"[主动消息] 观察消息失败（已忽略）: {e}")

    async def _on_bot_sent(self, ctx, msg=None) -> None:
        """Bot 发过话了：重置计时。"""
        if self._manager is None:
            return
        event = getattr(ctx, "event", None)
        if event is None:
            return
        try:
            await self._manager.on_bot_sent(
                ctx.session_id, getattr(event, "message_type", "") != "private"
            )
        except Exception as e:  # noqa: BLE001
            from app.core.logger import logger

            logger.add_info(f"#{self.bot_id}").debug(f"[主动消息] 更新发送状态失败（已忽略）: {e}")

    async def on_unload(self) -> None:
        runtime = self._runtime()
        if self._manager is not None:
            try:
                self._manager.stop()
            except Exception as e:  # noqa: BLE001 —— 停止失败不该阻断卸载
                from app.core.logger import logger

                logger.debug(f"[Proactive] 停止计时器失败（已忽略）: {e}")
            self._manager = None
        if runtime is None:
            return
        runtime.proactive_service = None
        runtime.proactive_options = None

    def _runtime(self) -> Any:
        services = getattr(getattr(self, "ctx", None), "services", None)
        getter = getattr(services, "runtime", None)
        return getter(self.bot_id) if callable(getter) else None

    @property
    def manager(self) -> Any:
        """当前计时器实例（诊断/测试用）。"""
        return self._manager

    @property
    def facade(self) -> Any:
        """运行时门面（诊断用）。"""
        from app.agent.policy import RuntimeFacade

        return RuntimeFacade


def owned_keys() -> tuple[str, ...]:
    """本模块持有的配置键（供迁移脚本与契约测试核对）。"""
    return OWNED_KEYS
