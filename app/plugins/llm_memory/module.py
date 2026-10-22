"""长期记忆模块：接管记忆策略配置（17 键）。

## 它负责什么

`memory_enable` / 召回条数与预算 / 确定性兜底 / 自动蒸馏 / 可信度与年龄门槛 /
审计 / 重置策略 —— 原来全在 agent 配置页的「长期记忆」组里，现在归本模块。

## 机制在哪

记忆的存储、检索、蒸馏 worker 仍是框架的 `app/agent/memory/*`（那是机制，
做过大量线上打磨）。本模块只提供**策略配置**：`on_load` 挂
`runtime.memory_options`，框架侧 `MemoryManager._get()` 全部走
`policy_config(runtime, "memory")`。

于是：
- 装模块 → 记忆功能可用，参数由模块配置决定；
- 卸模块 → `memory_options` 清空，框架回退 agent 配置（迁移期）/ 默认（删键后）。

## 数据位置不变

记忆数据仍写 `data/llm/<bot_id>/`，模块化不改数据布局，用户升级零迁移。
"""

from __future__ import annotations

from typing import Any

from app.plugin import BaseModule

from .schemas import MEMORY_SCHEMA

#: 本模块持有的键（与 `config_partition.MODULE_KEYS["llm_memory"]` 一致）
OWNED_KEYS: tuple[str, ...] = (
    "memory_enable",
    "memory_private_enable",
    "memory_group_enable",
    "memory_recall_max",
    "memory_recall_max_chars",
    "memory_save_deterministic",
    "memory_extract_enable",
    "memory_extract_interval_min",
    "memory_max_per_owner",
    "memory_user_cross_group",
    "memory_audit_enable",
    "memory_audit_inject",
    "memory_min_confidence",
    "memory_inject_hedge",
    "memory_max_age_days",
    "memory_on_reset",
    "memory_upgrade_saved_only",
)


class Module(BaseModule):
    name = "长期记忆"
    sign = "LlmMemory"
    description = "跨会话记住用户偏好与事实，并在后续对话里按可信度召回"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    order = 50
    provides = ("memory",)
    supersedes = ("memory",)
    default_config = {
        "memory_enable": False,
        "memory_private_enable": True,
        "memory_group_enable": True,
        "memory_recall_max": 8,
        "memory_recall_max_chars": 1200,
        "memory_save_deterministic": True,
        "memory_extract_enable": False,
        "memory_extract_interval_min": 30,
        "memory_max_per_owner": 200,
        "memory_user_cross_group": False,
        "memory_audit_enable": False,
        "memory_audit_inject": False,
        "memory_min_confidence": 0.5,
        "memory_inject_hedge": True,
        "memory_max_age_days": 0,
        "memory_on_reset": "keep",
        "memory_upgrade_saved_only": True,
    }
    config_schema = MEMORY_SCHEMA

    async def on_load(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        from app.agent.delivery.output import ConfigView

        runtime.memory_options = ConfigView(
            getattr(runtime, "config", None), self.config, OWNED_KEYS
        )

    async def on_unload(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        runtime.memory_options = None

    def _runtime(self) -> Any:
        services = getattr(getattr(self, "ctx", None), "services", None)
        getter = getattr(services, "runtime", None)
        return getter(self.bot_id) if callable(getter) else None

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("memory_enable", False))


def owned_keys() -> tuple[str, ...]:
    """本模块持有的配置键（供迁移脚本与契约测试核对）。"""
    return OWNED_KEYS
