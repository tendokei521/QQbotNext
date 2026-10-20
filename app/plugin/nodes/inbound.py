"""模块分发的内置节点（入站链）。

从原 dispatcher 的硬编码过滤抽取，行为保持一致：
- ModuleRouterNode     订阅匹配 + bot 归属 → ctx.state.candidates
- ModulePermissionNode 启停 + 单一服务 + 权限角色 → ctx.state.allowed
- ModuleInvokeNode     逐个调用业务模块 handle（1 级叶子）
- AgentNode            模块链之后的 LLM 兜底（模块可 event.llm.stop() 跳过）

这些节点依赖模块框架（app.plugin），故放在此而非 app/nodes。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger
from app.domain.events import BaseEvent
from app.plugin.authority import (
    check_module_enabled,
    check_module_permission,
    compute_event_permission,
    is_single_service_skipped,
    roster_lists,
)
from app.plugin.base import BaseModule
from app.plugin.nodes.base import MessageContext, MessageNode, Next
from app.plugin.registry import ModuleRegistry


class _AgentGate:
    """Agent 权限门控对象（对齐模块 authority 接口，供 check_* 复用）。

    数据来自框架级 AgentRuntime 配置（enabled / permission / permission），
    Agent 开关不依赖 llm_chat_v2 模块。
    """

    def __init__(self, runtime) -> None:
        self.permission = runtime.config.get("permission", "member")
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

    账号解析：一律用 ``event.account_id``（payload.self_id 优先、连接 bot_id 兜底）。
    连接是 index 级对象、可被换号复用，运行时的键必须跟账号走——否则换号后会
    出现"消息收到了但查不到运行时"（LLM 静默不回复）或"查到别的账号运行时"
    （用错人设/发错号）。每个门控分支都留 debug 日志，便于一句话定位被谁拦下。
    """

    name = "agent"
    order = 130  # 模块链（Router 100 / Permission 110 / Invoke 120）之后

    def __init__(self, agent_manager: Any, config_service: Any, gateway: Any, log=None) -> None:
        self.agent_manager = agent_manager
        self.config_service = config_service
        self.gateway = gateway
        self.log = log or logger

    def _account_logger(self, event):
        return self.log.add_info(f"#{getattr(event, 'bot_index', '?')}")

    async def process(self, ctx: MessageContext, next_: Next) -> None:
        event = ctx.event
        if event.event_type not in ("message_group", "message_private"):
            await next_()
            return
        account_id = getattr(event, "account_id", None) or event.bot_id
        if not account_id:
            self._account_logger(event).debug(
                "[Agent] 跳过：事件无账号（self_id/bot_id 均为空），无法定位 Agent 运行时"
            )
            await next_()
            return
        runtime = self.agent_manager.get_runtime(account_id) if self.agent_manager else None
        if runtime is None:
            # 账号有事件却查不到运行时：模块链消息会正常处理，但 LLM 永远不回复；
            # 这里必须留痕，否则表现为"账号换了之后 agent 配置就不生效了"。
            self._account_logger(event).warning(
                f"[Agent] 跳过：账号 {account_id} 无 Agent 运行时"
                f"（self_id={event.self_id} conn.bot_id={event.bot_id} "
                f"index={event.bot_index} 已装配账号={self._known_accounts()}）"
            )
            await next_()
            return
        # 门控：Agent 启停 / 单一服务 / 黑白名单（框架级配置）
        gate = _AgentGate(runtime)
        if not check_module_enabled(gate):
            self._account_logger(event).debug(f"[Agent] 跳过：账号 {account_id} 的 Agent 已在配置中禁用")
            await next_()
            return
        if is_single_service_skipped(gate, event, self.config_service, self.gateway):
            self._account_logger(event).debug(
                f"[Agent] 跳过：单一服务模式把本群响应权交给了 index "
                f"{self._single_service_index(event)}（当前 index={event.bot_index}）"
            )
            await next_()
            return
        # 名册身份（拥有者/管理者）优先于群角色：这两个名单是 Agent 自己的身份，
        # 跨群生效，所以必须由 Agent 配置提供
        _owner_roster, _manager_roster = roster_lists(getattr(runtime, "config", None))
        compute_event_permission(event, owner_list=_owner_roster, manager_list=_manager_roster)
        if not check_module_permission(gate, event):
            self._account_logger(event).debug(
                f"[Agent] 跳过：账号 {account_id} 权限不足"
                f"（role={getattr(event, 'permission_role', '?')} 策略={gate.permission}）"
            )
            await next_()
            return
        # 前面的模块已调用 event.stop() 强制终止 → 链已短路（含 LLM 兜底）
        if getattr(event, "_stopped", False):
            await next_()
            return
        # 模块已声明跳过 LLM 回复（event.llm.stop()）→ 不视为触发 LLM，不重置主动消息
        if getattr(event, "_llm_stop", False):
            self._account_logger(event).debug(f"[Agent] 跳过：模块已声明 event.llm.stop()（账号 {account_id}）")
            await next_()
            return
        # 非阻塞提交到 LLM 流水线（由 LlmPipeline 后台执行，避免卡住模块 Worker）
        pipeline = getattr(runtime, "llm_pipeline", None)
        if pipeline is not None:
            pipeline.submit(event)
        else:
            from app.agent.loop.chat import handle as agent_handle

            await agent_handle(runtime, event)
        await next_()

    def _single_service_index(self, event) -> Any:
        try:
            webui = self.config_service.get_webui_config() or {}
            group_id = str(getattr(getattr(event, "group", None), "group_id", "") or "")
            multi = (webui.get("multi_group", {}) or {}).get("groups", {}) or {}
            return (multi.get(group_id) or {}).get("service_bot_index")
        except Exception as e:
            # 仅用于把"被单一服务拦下"写进日志；读不到就把目标账号留空，不影响主流程
            self._account_logger(event).debug(f"[Agent] 读取单一服务配置失败（仅影响日志展示）: {e}")
            return None

    def _known_accounts(self) -> list[str]:
        """已装配的 Agent 运行时账号清单（仅用于诊断日志）。"""
        manager = self.agent_manager
        if manager is None:
            return []
        try:
            return sorted(str(k) for k in (manager.runtimes() or {}).keys())
        except Exception as e:
            self.log.debug(f"[Agent] 读取已装配账号清单失败（仅影响日志展示）: {e}")
            return []


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
            if "*" not in module.subscribe and event.event_type not in module.subscribe:
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

    def __init__(self, config_service: Any, gateway: Any, log=None, agent_manager: Any = None) -> None:
        self.config_service = config_service
        self.gateway = gateway
        self.log = log or logger
        self.agent_manager = agent_manager

    # ---------- 名册身份 ----------

    def _agent_config(self, account_id) -> dict:
        """读该账号的 Agent 配置（``agent_owner_list`` / ``agent_manager_list`` 在里头）。

        读不到就返回空 dict：名册缺席只意味着「谁都算不著名册身份」，
        不该让权限判断抛异常。
        """
        if account_id is None:
            return {}
        try:
            from app.agent.config import AGENT_CONFIG_MODULE
        except Exception:  # noqa: BLE001 - 极端情况下退回字面量，行为不变
            AGENT_CONFIG_MODULE = "agent"
        try:
            return self.config_service.get_module_config(AGENT_CONFIG_MODULE, account_id) or {}
        except Exception:  # noqa: BLE001
            return {}

    def _rosters(self, event) -> tuple[set[str], set[str]]:
        """取本次事件的 (拥有者名册, 管理者名册)。

        优先用 Agent 运行时的配置（已合并默认值、迁移过遗留配置），没有运行时
        再直接读配置中心。**模块侧必须拿到它**：``permission = "manager"/"owner"``
        的模块靠这份名册判定身份，缺了名册这两个档位永远不可能通过。
        """
        account_id = getattr(event, "account_id", None)
        if self.agent_manager is not None and account_id is not None:
            try:
                runtime = self.agent_manager.get_runtime(account_id)
            except Exception as e:  # noqa: BLE001
                self.log.debug(f"[Dispatch] 取 Agent 运行时失败（名册降级为配置中心）: {e}")
                runtime = None
            if runtime is not None:
                return roster_lists(getattr(runtime, "config", None))
        return roster_lists(self._agent_config(account_id))

    async def process(self, ctx: MessageContext, next_: Next) -> None:
        event = ctx.event
        # 先一次性计算事件角色，所有模块共用，避免重复计算。
        # 名册身份（拥有者/管理者）必须一起算：它跨群生效，且与群角色一样是
        # 「谁有资格」的一部分——此前这里没传名册，导致模块的 owner/manager 档
        # 与 Agent 侧判定不一致（AgentNode 传了、模块侧没传），表现为"模块永远不响应"。
        owner_list, manager_list = self._rosters(event)
        compute_event_permission(event, owner_list=owner_list, manager_list=manager_list)

        allowed: list[BaseModule] = []
        for module in ctx.state.get("candidates", []) or []:
            if not check_module_enabled(module):
                continue
            if self._is_single_service_skipped(module, event):
                continue
            # 模块级权限过滤：黑白名单 + permission 角色
            if not check_module_permission(module, event):
                continue
            module.permission_granted = True
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
            # 某模块已调用 event.stop() 强制终止 → 跳出，不再调用后续模块
            if ctx.cancelled or getattr(event, "_stopped", False):
                break
            try:
                if hasattr(module, "process_event"):
                    await module.process_event(event)
                else:
                    await module.handle(event)
            except Exception as e:
                self.log.exception(
                    f"[Dispatch] {module.module_name}(bot {module.bot_id}) 处理 {event.event_type} 异常: {e}"
                )
        await next_()
