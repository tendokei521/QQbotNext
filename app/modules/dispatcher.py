"""事件分发器：把领域事件路由到业务模块。

实现从「硬编码过滤循环」重构为「入站节点链」：
- 由 NodeRegistry 提供节点（内置 Router/Permission/Invoke，见 app/modules/nodes.py）；
- 框架/模块可插入新节点（限流/敏感词…）、替换内置节点（自定义权限）；
- node_registry 未传时自动装配内置三节点（保持独立可用）。
"""

from __future__ import annotations

import time
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
        event_completed_hooks: Any = None,
        log=None,
    ) -> None:
        self.registry = registry
        self.config_service = config_service
        self.gateway = gateway
        self.event_completed_hooks = event_completed_hooks
        self.log = log or logger

        if node_registry is None:
            node_registry = NodeRegistry(log=self.log)
            node_registry.register(ModuleRouterNode(registry, self.log))
            node_registry.register(ModulePermissionNode(config_service, gateway, self.log))
            node_registry.register(ModuleInvokeNode(self.log))
        self.node_registry = node_registry

    async def dispatch(self, event: BaseEvent) -> None:
        ctx = MessageContext(event=event, bot=getattr(event, "bot", None), state={})
        # 挂载节点链上下文，供模块 event.stop() 短路整条链路
        event._ctx = ctx
        started = time.monotonic()
        gated = event.event_type in ("message_group", "message_private")
        if gated:
            # 群聊"完全没反应"的排查锚点：确认事件确实进了节点链（否则问题在更上游）
            self.log.add_info(f"#{getattr(event, 'bot_index', '?')}").debug(
                f"[Dispatch] {event.event_type} 进入节点链"
                f"（account={getattr(event, 'account_id', None)} self_id={event.self_id} "
                f"bot_id={event.bot_id} 群={getattr(getattr(event, 'group', None), 'group_id', None)}）"
            )
        await NodeRunner(self.node_registry.inbound_nodes()).run(ctx)
        if gated:
            self.log.add_info(f"#{getattr(event, 'bot_index', '?')}").debug(
                f"[Dispatch] {event.event_type} 节点链结束"
                f"（llm_stop={getattr(event, '_llm_stop', False)} "
                f"stopped={getattr(event, '_stopped', False)} "
                f"已提交LLM={getattr(event, '_llm_job', None) is not None}）"
            )
        if self.event_completed_hooks is not None:
            try:
                await self.event_completed_hooks.run(
                    event,
                    state=ctx.state,
                    duration_ms=(time.monotonic() - started) * 1000,
                )
            except Exception as e:
                self.log.exception(f"[Dispatch] 事件完成钩子执行异常: {e}")
