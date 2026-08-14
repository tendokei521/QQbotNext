"""模块分发的内置节点（入站链）。

从原 dispatcher 的硬编码过滤抽取，行为保持一致：
- ModuleRouterNode     订阅匹配 + bot 归属 → ctx.state.candidates
- ModulePermissionNode 启停 + 单一服务 + 权限等级 → ctx.state.allowed
- ModuleInvokeNode     逐个调用业务模块 handle（1 级叶子）
- AgentNode            模块链之后的 LLM 兜底（模块可 event.llm.stop() 跳过）

这些节点依赖模块框架（app.modules），故放在此而非 app/nodes。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger
from app.domain.events import BaseEvent
from app.modules.authority import (
    check_event_permission,
    check_module_enabled,
    is_single_service_skipped,
)
from app.modules.base import BaseModule
from app.modules.registry import ModuleRegistry
from app.nodes.base import MessageContext, MessageNode, Next


class _AgentGate:
    """Agent 权限门控对象（对齐模块 authority 接口，供 check_* 复用）。

    数据来自框架级 AgentRuntime 配置（enabled / permission / authority_type），
    Agent 开关不依赖 llm_chat_v2 模块。
    """

    def __init__(self, runtime) -> None:
        self.authority_type = runtime.config.get("authority_type", "strict")
        self.sign = "LLM Agent"
        self.module_name = "llm_chat_v2"
        self.authority = type("_A", (), {
            "enabled": runtime.config.enabled,
            "permission": runtime.config.permission,
        })()


class AgentNode(MessageNode):
    """框架级 LLM Agent 兜底响应：模块链之后执行。

    顺序：模块先处理（Router → Permission → Invoke），LLM 最后兜底。
    模块可在 handle 中调用 event.llm.stop() 声明「我已处理，跳过 LLM 回复」；
    未声明时 LLM 按触发规则（私聊全触发 / 群聊 @或关键词）决定是否回复。
    主动消息观察独立于回复，即使模块 stop 了 LLM 也照常维护。
    """

    name = "agent"
    order = 130  # 模块链（Router 100 / Permission 110 / Invoke 120）之后

    def __init__(self, agent_manager: Any, config_service: Any, gateway: Any, log=None) -> None:
        self.agent_manager = agent_manager
        self.config_service = config_service
        self.gateway = gateway
        self.log = log or logger

    async def process(self, ctx: MessageContext, next_: Next) -> None:
        event = ctx.event
        if event.event_type not in ("message_group", "message_private"):
            await next_()
            return
        runtime = self.agent_manager.get_runtime(event.bot_id) if event.bot_id else None
        if runtime is None:
            await next_()
            return
        # 门控：Agent 启停 / 单一服务 / 黑白名单（框架级配置）
        gate = _AgentGate(runtime)
        if not check_module_enabled(gate):
            await next_()
            return
        if is_single_service_skipped(gate, event, self.config_service, self.gateway):
            await next_()
            return
        event.authority_level = None
        if not check_event_permission(event, gate):
            await next_()
            return
        # 模块已声明跳过 LLM 回复（event.llm.stop()）→ 仅维护主动消息观察
        if getattr(event, "_llm_stop", False):
            await self._observe_proactive(runtime, event)
            await next_()
            return
        from app.llm import handle as agent_handle

        await agent_handle(runtime, event)
        await next_()

    @staticmethod
    async def _observe_proactive(runtime, event) -> None:
        """主动消息观察：模块跳过 LLM 回复时也维护 Agent 的活跃状态。"""
        pm = getattr(runtime, "proactive", None)
        if pm is None:
            return
        is_group = event.event_type == "message_group"
        sid = f"group_{event.group.group_id}" if is_group else f"private_{event.user_id}"
        await pm.on_message(sid, is_group, is_self=(event.user_id == event.self_id))


class ModuleRouterNode(MessageNode):
    """选择订阅了本事件类型且属于该 bot 的候选模块。"""

    name = "router"
    order = 100

    def __init__(self, registry: ModuleRegistry, log=None) -> None:
        self.registry = registry
        self.log = log or logger

    async def process(self, ctx: MessageContext, next_: Next) -> None:
        event = ctx.event
        candidates: list[BaseModule] = []
        for module in self.registry.loaded():
            if event.event_type not in module.subscribe:
                continue
            # bot 归属：有明确 bot 只派发给该 bot 的实例；全局(None)实例不处理事件
            if event.bot_id:
                if not module.bot_id or module.bot_id != event.bot_id:
                    continue
            else:
                if module.bot_id:
                    continue
            candidates.append(module)
        ctx.state["candidates"] = candidates
        await next_()


class ModulePermissionNode(MessageNode):
    """启停 / 单一服务 / 权限等级过滤。可被替换以实现自定义权限策略。"""

    name = "permission"
    order = 110

    def __init__(self, config_service: Any, gateway: Any, log=None) -> None:
        self.config_service = config_service
        self.gateway = gateway
        self.log = log or logger

    async def process(self, ctx: MessageContext, next_: Next) -> None:
        event = ctx.event
        allowed: list[BaseModule] = []
        for module in ctx.state.get("candidates", []) or []:
            if not check_module_enabled(module):
                continue
            if self._is_single_service_skipped(module, event):
                continue
            # 权限：每模块独立计算，避免上一个模块的等级/黑名单污染后续模块
            event.authority_level = None
            if not check_event_permission(event, module):
                continue
            module.authority_check = event.authority_check
            module.authority_level = event.authority_level
            module.authority_enabled = getattr(event, "authority_enabled", False)
            allowed.append(module)
        ctx.state["allowed"] = allowed
        await next_()

    def _is_single_service_skipped(self, module: BaseModule, event: BaseEvent) -> bool:
        return is_single_service_skipped(module, event, self.config_service, self.gateway)


class ModuleInvokeNode(MessageNode):
    """末端节点：逐个调用被允许的业务模块（1 级叶子）。"""

    name = "invoke"
    order = 120

    def __init__(self, log=None) -> None:
        self.log = log or logger

    async def process(self, ctx: MessageContext, next_: Next) -> None:
        event = ctx.event
        for module in ctx.state.get("allowed", []) or []:
            try:
                await module.handle(event)
            except Exception as e:
                self.log.exception(
                    f"[Dispatch] {module.module_name}(bot {module.bot_id}) 处理 {event.event_type} 异常: {e}"
                )
        await next_()
