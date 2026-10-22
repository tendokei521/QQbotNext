"""框架内置能力的**唯一登记表**。

历史问题：8 个控制器原本在 `bootstrap.py` 里逐个手写 `feature_registry.register(...)`，
于是"新增一个能力"要改三处（控制器类、bootstrap、测试里的清单），三处必然漂移。

现在只在这张表里加一行，`bootstrap` 与测试都从它取值。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.plugin.features.controllers import (
    AgentFeatureController,
    AssemblerFeatureController,
    KnowledgeFeatureController,
    MemoryFeatureController,
    OneBotToolsFeatureController,
    ProactiveFeatureController,
    ProviderInvokerFeatureController,
    RuntimeSlotFeatureController,
    ScheduleFeatureController,
)

#: 控制器工厂：(agent_manager) -> FeatureController
ControllerFactory = Callable[[Any], Any]

#: 内置能力清单：顺序即注册顺序（同名会抛错，所以保持唯一）
BUILTIN_CONTROLLERS: tuple[ControllerFactory, ...] = (
    ProactiveFeatureController,
    ScheduleFeatureController,
    MemoryFeatureController,
    KnowledgeFeatureController,
    OneBotToolsFeatureController,
    AgentFeatureController,
    AssemblerFeatureController,
    ProviderInvokerFeatureController,
)


def register_builtin_controllers(registry: Any, agent_manager: Any) -> list[str]:
    """把内置能力全部登记进 ``registry``，返回已登记的 ``feature_id`` 列表。"""
    registered: list[str] = []
    for factory in BUILTIN_CONTROLLERS:
        controller = factory(agent_manager)
        registry.register(controller)
        registered.append(controller.feature_id)
    return registered


__all__ = [
    "BUILTIN_CONTROLLERS",
    "ControllerFactory",
    "RuntimeSlotFeatureController",
    "register_builtin_controllers",
]
