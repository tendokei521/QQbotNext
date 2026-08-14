"""事件分发器：把领域事件路由到业务模块。

实现从「硬编码过滤循环」重构为「入站节点链」：
- 由 NodeRegistry 提供节点（内置 Router/Permission/Invoke，见 app/modules/nodes.py）；
- 框架/模块可插入新节点（限流/敏感词…）、替换内置节点（自定义权限）；
- node_registry 未传时自动装配内置三节点（保持独立可用）。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger
from app.domain.events import BaseEvent
from app.modules.nodes import ModuleInvokeNode, ModulePermissionNode, ModuleRouterNode
from app.nodes.base import MessageContext, NodeRunner
from app.nodes.registry import NodeRegistry


class ModuleDispatcher:
    def __init__(
        self,
        *,
        registry,
        config_service: Any,
        gateway: Any,
        node_registry: Any = None,
        log=None,
    ) -> None:
        self.registry = registry
        self.config_service = config_service
        self.gateway = gateway
        self.log = log or logger

        if node_registry is None:
            node_registry = NodeRegistry(log=self.log)
            node_registry.register(ModuleRouterNode(registry, self.log))
            node_registry.register(ModulePermissionNode(config_service, gateway, self.log))
            node_registry.register(ModuleInvokeNode(self.log))
        self.node_registry = node_registry

    async def dispatch(self, event: BaseEvent) -> None:
        ctx = MessageContext(event=event, bot=getattr(event, "bot", None), state={})
        await NodeRunner(self.node_registry.inbound_nodes()).run(ctx)
