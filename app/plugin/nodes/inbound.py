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

from app.application.response_authority import ResponseAuthority
from app.core.logger import logger
from app.core.skip_log import log_skip
from app.domain.events import BaseEvent
from app.domain.response_access import (
    INBOUND_STAGES,
    REASON_ACCOUNT_DISABLED,
    hint_for,
    remember_decision,
    stored_decision,
)
from app.plugin.authority import (
    check_module_enabled,
    check_module_permission,
    compute_event_permission,
    is_single_service_skipped,
    roster_lists,
)
from app.plugin.base import BaseModule
from app.plugin.commands import (
    CommandArgsError,
    best_match,
    bind_arguments,
    iter_module_commands,
    usage_line,
)
from app.plugin.nodes.base import MessageContext, MessageNode, Next
from app.plugin.registry import ModuleRegistry

try:  # 框架级 Agent 配置命名空间（"agent"）；极端情况下退回字面量，行为不变
    from app.agent.config import AGENT_CONFIG_MODULE
except Exception:  # noqa: BLE001
    AGENT_CONFIG_MODULE = "agent"


class _AgentGate:
    """Agent 权限门控对象（对齐模块 authority 接口，供 check_* 复用）。

    数据来自框架级 AgentRuntime 配置（enabled / permission / permission），
    Agent 开关不依赖 llm_chat_v2 模块。
    """

    def __init__(self, runtime) -> None:
        self.permission = runtime.config.get("permission", "member")
        self.sign = "LLM Agent"
        self.module_name = "llm_chat_v2"
        # 单一服务：框架级 Agent 恒声明支持；配置键必须对齐 WebUI 存的 "agent"
        # （module_name 是 llm_chat_v2 时代的遗留值，直接用它查配置会永远查不到）
        self.supports_single_service = True
        self.single_service_key = AGENT_CONFIG_MODULE
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

    账号解析：一律用 ``event.account_id``（用户在添加账号时设定的 id，不随换 QQ 变化）。
    运行时的键必须跟账号 id 走——否则换号后会
    出现"消息收到了但查不到运行时"（LLM 静默不回复）或"查到别的账号运行时"
    （用错人设/发错号）。每个门控分支都留 debug 日志，便于一句话定位被谁拦下。
    """

    name = "agent"
    order = 130  # 模块链（Router 100 / Permission 110 / Invoke 120）之后

    def __init__(self, agent_manager: Any, config_service: Any, gateway: Any, log=None, *, responder: Any = None) -> None:
        self.agent_manager = agent_manager
        self.config_service = config_service
        self.gateway = gateway
        self.log = log or logger
        #: 响应权仲裁器（docs/multi-account-design.md §6）；为空时退回旧的单一服务判定
        self.responder = responder
        #: 唯一判定入口（账号/场所/响应权/群范围/身份/用户例外/免打扰都从这里出）
        self.access = ResponseAuthority(
            config_service=config_service,
            agent_manager=agent_manager,
            responder=responder,
            log=self.log,
            legacy_skip=self._legacy_single_service,
        )

    def _account_logger(self, event):
        return self.log.add_info(getattr(event, "account_name", "") or "?")

    # ---------- 统一判定 ----------

    def _decision(self, event):
        """本轮判定：入站节点算一次，流水线直接复用（不重复算）。"""
        decision = stored_decision(event)
        if decision is not None:
            return decision
        if self.access is None:
            return None
        try:
            decision = self.access.decide_agent(event)
        except Exception as e:  # noqa: BLE001 —— 判定失败按"放行"处理，绝不吞消息
            self.log.debug(f"[Access] 判定异常（本轮放行）: {e}")
            return None
        remember_decision(event, decision)
        return decision

    def _legacy_single_service(self, event, module=None) -> bool:
        """responder 未装配时的旧「单一服务」判定（声明制，见 authority.py）。"""
        target = module
        if target is None:
            account_id = getattr(event, "account_id", None)
            runtime = None
            if self.agent_manager is not None and account_id is not None:
                try:
                    runtime = self.agent_manager.get_runtime(account_id)
                except Exception:  # noqa: BLE001
                    runtime = None
            if runtime is None:
                return False
            target = _AgentGate(runtime)
        return is_single_service_skipped(target, event, self.config_service, self.gateway)

    def _log_decision_skip(self, event, denial) -> None:
        """把"被哪一层拦下"写进用户可见日志（统一格式 + 限流，见 core/skip_log）。"""
        detail = f"stage={denial.stage} · source={denial.source}"
        if denial.detail:
            detail += f" · {denial.detail}"
        group_id = getattr(getattr(event, "group", None), "group_id", "")
        if group_id:
            detail += f" · group={group_id}"
        log_skip(
            getattr(event, "account_id", "") or self._account_of(event),
            denial.reason,
            detail.strip(" ·"),
            hint=hint_for(denial.reason),
        )

    @staticmethod
    def _account_of(event) -> str:
        return str(getattr(event, "account_id", "") or getattr(event, "self_id", "") or "?")

    async def process(self, ctx: MessageContext, next_: Next) -> None:
        """路由入口。外面包一层统计：**一次入站消息一条 `msg_in`**。"""
        try:
            await self._route(ctx, next_)
        finally:
            self._record_inbound(ctx)

    async def _route(self, ctx: MessageContext, next_: Next) -> None:
        event = ctx.event
        if event.event_type not in ("message_group", "message_private"):
            await next_()
            return
        account_id = getattr(event, "account_id", None)
        if not account_id:
            self._account_logger(event).debug(
                "[Agent] 跳过：事件未携带账号 id，无法定位 Agent 运行时"
            )
            await next_()
            return
        runtime = self.agent_manager.get_runtime(account_id) if self.agent_manager else None
        if runtime is None:
            # 账号有事件却查不到运行时：模块链消息会正常处理，但 LLM 永远不回复；
            # 这里必须留痕，否则表现为"账号换了之后 agent 配置就不生效了"。
            self._account_logger(event).warning(
                f"[Agent] 跳过：账号 {account_id} 无 Agent 运行时"
                f"（QQ={event.self_id} 已装配账号={self._known_accounts()}）"
            )
            await next_()
            return
        # 名册身份（拥有者/管理者）优先于群角色：这两个名单是 Agent 自己的身份，
        # 跨群生效，所以必须由 Agent 配置提供；统一判定器要读 event.permission_role。
        _owner_roster, _manager_roster = roster_lists(getattr(runtime, "config", None))
        compute_event_permission(event, owner_list=_owner_roster, manager_list=_manager_roster)

        # 唯一判定入口：账号启用 → 场所 → 谁响应 → 群范围 → 身份 → 用户例外 → 免打扰。
        # 入站只执行 INBOUND_STAGES 那几段（场景/例外/免打扰留给流水线，命令要先能跑）。
        decision = self._decision(event)
        if decision is not None:
            ctx.state["response_decision"] = decision
            denial = decision.first_denial(INBOUND_STAGES)
            if denial is not None:
                self._log_decision_skip(event, denial)
                await next_()
                return
        else:
            # 兜底（旧运行时替身 / 判定异常）：保持改造前的逐项门控，行为不变
            gate = _AgentGate(runtime)
            if not check_module_enabled(gate):
                self._account_logger(event).debug(
                    f"[Agent] 跳过：账号 {account_id} 的 Agent 已在配置中禁用"
                )
                await next_()
                return
            if is_single_service_skipped(gate, event, self.config_service, self.gateway):
                self._account_logger(event).debug(
                    f"[Agent] 跳过：单一服务模式把本群响应权交给了账号 "
                    f"{self._single_service_index(event)}（当前账号={account_id}）"
                )
                await next_()
                return
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

    def _record_inbound(self, ctx: MessageContext) -> None:
        """记一条 `msg_in` —— 仅当消息没交给流水线时。

        交给流水线的那些由 `pipeline._run` 记账（那里才知道真实结局：
        指令 / 触发未通过 / 真的进了 LLM）。这里只负责模块拦下 / 门控拦下的。

        历史 bug：曾用 `event._llm_job` 判结局，而 `submit()` 在触发判定之前
        就把它挂上了 —— 于是每条群消息都被算成「需响应」。
        """
        try:
            from app.agent.usage.messages import STAT_OWNER_ATTR, record_inbound

            event = ctx.event
            if getattr(event, STAT_OWNER_ATTR, "") == "pipeline":
                return
            record_inbound(event, self._outcome_of(event))
        except Exception as e:  # noqa: BLE001 —— 统计是旁路，绝不阻断消息处理
            self.log.debug(f"[Stats] 入站记账失败（已忽略）: {e}")

    @staticmethod
    def _outcome_of(event) -> str:
        """判定这条入站消息的结局。

        指令与「真正进 LLM」的判定在这里做（`record_inbound` 只在没交给流水线时调用）；
        交给流水线的消息由 `pipeline._run` 记账（那里才知道真实结局）。
        """
        from app.agent.commands import is_command

        try:
            text = str(getattr(event, "raw_text", "") or "")
            if not text:
                from app.domain.trigger import raw_user_text

                text = raw_user_text(event)
        except Exception as e:  # noqa: BLE001 —— 取不到正文就当没有
            logger.debug(f"[Stats] 读取消息正文失败（按空文本处理）: {e}")
            text = ""
        if text and is_command(text):
            return "command"
        if getattr(event, "_llm_job", None) is not None:
            return "llm"
        if getattr(event, "_stopped", False) or getattr(event, "_llm_stop", False):
            return "module"
        return "ignored"

    def _single_service_index(self, event) -> Any:
        try:
            webui = self.config_service.get_webui_config() or {}
            group_id = str(getattr(getattr(event, "group", None), "group_id", "") or "")
            multi = (webui.get("multi_group", {}) or {}).get("groups", {}) or {}
            return (multi.get(group_id) or {}).get("service_bot_id")
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
            # 账号归属：有明确账号只派发给该账号的实例；全局(None)实例不处理事件
            if event.account_id:
                if not module.bot_id or module.bot_id != event.account_id:
                    continue
            else:
                if module.bot_id:
                    continue
            # 该账号下已关闭的模块不进候选：关闭是用户显式状态（静默），
            # 之前它每 60s 会在权限节点刷一条 "xxx:account_disabled" info，
            # 被当成"出问题了"的噪音；关闭模块也不应该在每个群里参与判定。
            if not check_module_enabled(module):
                continue
            candidates.append(module)
        ctx.state["candidates"] = candidates
        await next_()


class ModulePermissionNode(MessageNode):
    """启停 / 响应权仲裁 / 权限等级过滤。可被替换以实现自定义权限策略。"""

    name = "permission"
    order = 110

    def __init__(self, config_service: Any, gateway: Any, log=None, agent_manager: Any = None,
                 registry: Any = None, responder: Any = None) -> None:
        self.config_service = config_service
        self.gateway = gateway
        self.log = log or logger
        self.agent_manager = agent_manager
        #: 模块实例来源（仲裁候选集合需要看其它账号的实例）；为空时退回旧判定
        self.registry = registry
        #: 响应权仲裁器（docs/multi-account-design.md §6）
        self.responder = responder
        #: 唯一判定入口（模块路径：authority 启用 + 谁响应 + 群/用户范围 + 身份档位）
        self.access = ResponseAuthority(
            config_service=config_service,
            agent_manager=agent_manager,
            responder=responder,
            registry=registry,
            log=self.log,
            legacy_skip=self._legacy_single_service,
        )

    def _legacy_single_service(self, event, module=None) -> bool:
        """responder 未装配时：旧的「单一服务」声明制判定（模块维度）。"""
        if module is None:
            return False
        return self._is_single_service_skipped(module, event)

    # ---------- 名册身份 ----------

    def _agent_config(self, account_id) -> dict:
        """读该账号的 Agent 配置（``agent_owner_list`` / ``agent_manager_list`` 在里头）。

        读不到就返回空 dict：名册缺席只意味着「谁都算不著名册身份」，
        不该让权限判断抛异常。
        """
        if account_id is None:
            return {}
        try:
            return self.config_service.get_module_config(AGENT_CONFIG_MODULE, account_id) or {}
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[inbound] 225: 已忽略异常: {e}")
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
            name = str(getattr(module, "module_name", "?") or "?")
            # 统一判定入口：authority 启用 → 谁响应 → 群/用户范围 → 身份档位。
            # 被拦下的原因从 debug 升级为限流 info（"模块为什么不响应"可查）。
            decision = self.access.decide_module(module, event)
            deny = decision.first_denial(INBOUND_STAGES)
            if deny is not None:
                # 模块在该账号下关闭 = 明确的用户状态，不是"回不回的故障"：
                # 只留 debug 技术日志，避免每分钟一条 info 把真实问题淹没。
                level = "debug" if deny.reason == REASON_ACCOUNT_DISABLED else "info"
                log_skip(
                    getattr(event, "account_id", "") or "?",
                    f"{name}:{deny.reason}",
                    f"stage={deny.stage} · source={deny.source} · {deny.detail}",
                    hint=hint_for(deny.reason),
                    level=level,
                )
                continue
            module.permission_granted = True
            allowed.append(module)
        ctx.state["allowed"] = allowed
        await next_()

    def _is_single_service_skipped(self, module: BaseModule, event: BaseEvent) -> bool:
        return is_single_service_skipped(module, event, self.config_service, self.gateway)


class ModuleCommandNode(MessageNode):
    """统一命令解析与分发（模块层）。

    链序：``router(100) → permission(110) → command(115) → invoke(120) → agent(130)``。
    位置是有讲究的：

    - 在**权限过滤之后**：``permission="owner"`` 的模块，命令一样受权限约束
      （不会出现"权限拦住了模块钩子，命令却照跑"）；
    - 在**模块通用钩子之前**：命令先落定，``@module_hook`` 的观察者（如 group_log）仍会收到事件；
    - 在**LLM 兜底之前**：所以命中命令能直接截断 LLM。

    **隐藏参数 ``skip_llm`` 默认 True**：命中命令就 ``event.llm.stop()``，本轮不进 LLM；
    命令处理器确实想再让模型说点什么时，声明时显式写 ``skip_llm=False``。
    处理器抛异常时同样截断 LLM（否则模型会对着报错文本瞎编）。
    """

    name = "command"
    order = 115

    def __init__(self, log=None) -> None:
        self.log = log or logger

    async def process(self, ctx: MessageContext, next_: Next) -> None:
        event = ctx.event
        if event.event_type not in ("message_group", "message_private"):
            await next_()
            return
        text = getattr(event, "text", "") or ""
        if not text.strip():
            await next_()
            return

        entries = iter_module_commands(ctx.state.get("allowed") or [])
        if not entries:
            await next_()
            return

        match = best_match(text, entries)
        if match is None:
            await next_()
            return

        invocation = match.as_invocation()
        event.command = invocation
        ctx.state["command"] = invocation
        self.log.debug(f"[Command] {invocation} → {match.module_name}")

        # 按签名绑参（GreedyStr / 类型转换 / 默认值）；参数不合法就回用法提示并截断 LLM
        try:
            kwargs = bind_arguments(match.spec.params, match.args)
        except CommandArgsError as e:
            usage = usage_line(invocation.name, match.spec.params, prefix=invocation.prefix or "/")
            self.log.debug(f"[Command] {match.module_name} {invocation.name} 参数不合法: {e}")
            await self._reply(event, f"⚠️ {e}\n用法：{usage}")
            event.llm.stop()
            await next_()
            return
        invocation.params = kwargs

        try:
            await match.handler(event, **kwargs)
        except Exception as e:  # noqa: BLE001 —— 命令异常不能掀翻节点链
            self.log.exception(f"[Command] {match.module_name} 处理 {invocation.name} 异常: {e}")
            event.llm.stop()
            await next_()
            return

        if invocation.skip_llm:
            event.llm.stop()
        await next_()

    async def _reply(self, event, text: str) -> None:
        """回一条提示；发送失败只留痕，不影响节点链。"""
        reply = getattr(event, "reply", None)
        if reply is None:
            return
        try:
            await reply(text)
        except Exception as e:  # noqa: BLE001
            self.log.debug(f"[Command] 用法提示发送失败（忽略）: {e}")


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
                    f"[Dispatch] {module.module_name}(账号 {event.account_name or module.bot_id}) 处理 {event.event_type} 异常: {e}"
                )
        await next_()
