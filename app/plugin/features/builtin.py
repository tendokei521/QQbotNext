"""框架内置能力的**唯一登记表**（v3：只剩高级接口）。

只登记"更换机制 / 接管行为"的 4 个能力。业务能力（记忆、知识库、主动消息、
定时任务、OneBot 工具、MCP、元信息渲染、指代消解）由各模块用基础接口实现，
**不在这里登记** —— 框架不该知道它们存在。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.plugin.features.controllers import (
    AgentFeatureController,
    AssemblerFeatureController,
    OutputFeatureController,
    ProviderInvokerFeatureController,
    RuntimeSlotFeatureController,
)

#: 控制器工厂：(agent_manager) -> FeatureController
ControllerFactory = Callable[[Any], Any]

#: 内置能力清单（顺序即注册顺序）
BUILTIN_CONTROLLERS: tuple[ControllerFactory, ...] = (
    AgentFeatureController,
    AssemblerFeatureController,
    ProviderInvokerFeatureController,
    OutputFeatureController,
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
