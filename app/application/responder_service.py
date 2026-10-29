"""跨账号响应权仲裁服务：唯一允许同时看 registry / gateway / 策略的地方。

职责（docs/multi-account-design.md §6）：

1. 构造候选集合：对某能力 + 某事件，列出「真的会响应」的账号——
   已装配实例 ∧ 模块启用 ∧ 连接在线 ∧ 在群 ∧ 权限通过；
2. 交由纯函数 :func:`app.plugin.responder.elect` 按策略选人；
3. 记录决策（reason/degraded）并对降级做限频告警。

**不在这里**做事件分发与权限短路：节点链拿到 ``ResponderDecision`` 后自行决定跳过/继续。
本服务只读共享状态，不写任何配置，因此可以安全地被多个节点复用。
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

from app.application.policy_store import PolicyStore
from app.core.logger import logger
from app.plugin.authority import check_module_enabled, check_module_permission
from app.plugin.responder import (
    ResponderDecision,
    ResponderPolicy,
    decision_as_dict,
    elect,
)

#: 框架级 Agent 的能力名（与 ``app.agent.config.AGENT_CONFIG_MODULE`` 一致；
#: 这里不 import agent 模块，避免 application → agent 的加载顺序耦合）
AGENT_CAPABILITY = "agent"

#: 降级告警的同一 (能力, 会话, 原因) 最小间隔（秒）
DEFAULT_WARN_INTERVAL = 300.0
#: 决策记录保留条数（诊断/UI 预览用，非持久化）
MAX_REMEMBERED = 512


class ResponderService:
    """响应权仲裁器。只读依赖：策略存储、模块注册表、网关、账号配置、Agent 管理器。"""

    def __init__(
        self,
        *,
        policy_store: PolicyStore,
        registry: Any,
        gateway: Any,
        config_service: Any,
        agent_manager: Any = None,
        log: Any = None,
        warn_interval: float = DEFAULT_WARN_INTERVAL,
        clock: Any = time.monotonic,
    ) -> None:
        self.policy_store = policy_store
        self.registry = registry
        self.gateway = gateway
        self.config_service = config_service
        self.agent_manager = agent_manager
        self.log = log or logger
        self.warn_interval = float(warn_interval)
        self._clock = clock
        self._warned: dict[tuple[str, str, str], float] = {}
        self._last: dict[tuple[str, str | None], ResponderDecision] = {}
        #: 最近一次事件里被降级的决策（测试/诊断用）
        self.last_degraded: ResponderDecision | None = None

    # ==================== 对外入口 ====================

    def decide_module(self, module_name: str, event: Any) -> ResponderDecision:
        """业务模块的响应权决策（候选来自模块注册表 + 网关）。"""
        channel_id = channel_of(event)
        candidates = self._module_candidates(module_name, event, channel_id)
        return self._decide(module_name, channel_id, candidates)

    def decide_agent(self, event: Any) -> ResponderDecision:
        """框架级 Agent 的响应权决策（候选来自 Agent 运行时）。"""
        channel_id = channel_of(event)
        candidates = self._agent_candidates(event, channel_id)
        return self._decide(self._agent_capability(), channel_id, candidates)

    def preview(
        self, capability_id: str, channel_id: str | None, candidates: Iterable[Any]
    ) -> ResponderDecision:
        """纯预览：按当前策略与给定候选算一次决策（不写记录、不告警）。"""
        policy = self._policy(capability_id, channel_id)
        return elect(policy, candidates, capability_id=capability_id, channel_id=channel_id)

    def preview_capability(
        self, capability_id: str, channel_id: str | None
    ) -> ResponderDecision:
        """UI 预览：按当前启用/在线/在群状态算候选（不含事件级权限），返回决策。

        与 :meth:`decide_module` 的区别是它没有事件，因此无法评估黑白名单/角色；
        预览用途足够，且不允许把「预览」结果用于真实拦截。
        """
        if capability_id == self._agent_capability():
            candidates = self._preview_agent_candidates(channel_id)
        else:
            candidates = self._preview_module_candidates(capability_id, channel_id)
        return self.preview(capability_id, channel_id, candidates)

    def last_decision(self, capability_id: str, channel_id: str | None) -> dict | None:
        decision = self._last.get((capability_id, channel_id))
        return decision_as_dict(decision) if decision is not None else None

    # ==================== 候选集合 ====================

    @staticmethod
    def _module_capability(module_name: str) -> str:
        return str(module_name)

    @staticmethod
    def _agent_capability() -> str:
        return AGENT_CAPABILITY

    def _accounts(self) -> list[str]:
        """按配置顺序返回账号 id（``dedupe`` 的稳定顺序由它决定）。"""
        try:
            return [str(bot.get("id")) for bot in self.config_service.get_bots() if bot.get("id")]
        except Exception as e:  # noqa: BLE001 —— 读配置失败按"无候选"处理，不影响消息主流程
            self.log.debug(f"[Responder] 读取账号清单失败（按无候选处理）: {e}")
            return []

    def _online_in_channel(self, account_id: str, channel_id: str | None) -> bool:
        """账号是否在线、且（群聊时）确实在该群。私聊不校验群成员。"""
        conn = getattr(self.gateway, "connections", {}).get(str(account_id))
        if conn is None or getattr(conn, "status", "") != "connected":
            return False
        if channel_id is None:
            return True
        groups = {str(group_id) for group_id in (getattr(conn, "all_group_list", None) or [])}
        return str(channel_id) in groups

    def _module_candidates(
        self, module_name: str, event: Any, channel_id: str | None
    ) -> list[str]:
        current = getattr(event, "account_id", None)
        if channel_id is None:
            # 私聊没有"同群多账号"概念：只可能是本账号自己
            if not current:
                return []
            instance = self.registry.get(module_name, current)
            if instance is None or not check_module_enabled(instance):
                return []
            return [str(current)] if check_module_permission(instance, event) else []

        candidates: list[str] = []
        for account_id in self._accounts():
            instance = self.registry.get(module_name, account_id)
            if instance is None or not check_module_enabled(instance):
                continue
            if not self._online_in_channel(account_id, channel_id):
                continue
            if not check_module_permission(instance, event):
                continue
            candidates.append(str(account_id))
        return candidates

    def _agent_candidates(self, event: Any, channel_id: str | None) -> list[str]:
        manager = self.agent_manager
        if manager is None:
            return []
        current = getattr(event, "account_id", None)

        def eligible(account_id: str, runtime: Any) -> bool:
            if runtime is None:
                return False
            config = getattr(runtime, "config", None)
            if not bool(getattr(config, "enabled", True)):
                return False
            if not bool(getattr(runtime, "online", False)):
                return False
            return self._online_in_channel(account_id, channel_id)

        if channel_id is None:
            if not current:
                return []
            runtime = manager.get_runtime(current)
            return [str(current)] if eligible(str(current), runtime) else []

        candidates: list[str] = []
        try:
            runtimes = manager.runtimes() or {}
        except Exception as e:  # noqa: BLE001 —— 运行时表异常按无候选处理
            self.log.debug(f"[Responder] 读取 Agent 运行时失败（按无候选处理）: {e}")
            return []
        for account_id, runtime in runtimes.items():
            if eligible(str(account_id), runtime):
                candidates.append(str(account_id))
        return candidates

    # ---------- 预览候选（无事件，不看权限） ----------

    def _preview_module_candidates(
        self, capability_id: str, channel_id: str | None
    ) -> list[str]:
        if not channel_id:
            return []
        candidates: list[str] = []
        for account_id in self._accounts():
            instance = self.registry.get(capability_id, account_id)
            if instance is None or not check_module_enabled(instance):
                continue
            if self._online_in_channel(account_id, channel_id):
                candidates.append(account_id)
        return candidates

    def _preview_agent_candidates(self, channel_id: str | None) -> list[str]:
        manager = self.agent_manager
        if manager is None or not channel_id:
            return []
        try:
            runtimes = manager.runtimes() or {}
        except Exception as e:  # noqa: BLE001
            self.log.debug(f"[Responder] 读取 Agent 运行时失败（预览按无候选处理）: {e}")
            return []
        candidates: list[str] = []
        for account_id, runtime in runtimes.items():
            config = getattr(runtime, "config", None)
            if not bool(getattr(config, "enabled", True)):
                continue
            if not bool(getattr(runtime, "online", False)):
                continue
            if self._online_in_channel(str(account_id), channel_id):
                candidates.append(str(account_id))
        return candidates

    # ==================== 决策 ====================

    def _policy(self, capability_id: str, channel_id: str | None) -> ResponderPolicy | None:
        try:
            return self.policy_store.get(capability_id, channel_id)
        except Exception as e:  # noqa: BLE001 —— 策略读失败退化为 all（不静默、不重复）
            self.log.warning(f"[Responder] 读取策略失败（退化为 all）: {e}")
            return None

    def _decide(
        self, capability_id: str, channel_id: str | None, candidates: Iterable[str]
    ) -> ResponderDecision:
        policy = self._policy(capability_id, channel_id)
        decision = elect(
            policy, candidates, capability_id=capability_id, channel_id=channel_id
        )
        self._remember(decision)
        if decision.degraded:
            self.last_degraded = decision
            self._warn_degraded(decision)
        return decision

    def _remember(self, decision: ResponderDecision) -> None:
        key = (decision.capability_id, decision.channel_id)
        self._last[key] = decision
        if len(self._last) > MAX_REMEMBERED:
            self._last.pop(next(iter(self._last)))

    def _warn_degraded(self, decision: ResponderDecision) -> None:
        """降级必须可见：同一 (能力, 会话, 原因) 在窗口内只告警一次，避免刷屏。"""
        key = (decision.capability_id, str(decision.channel_id), decision.reason)
        now = self._clock()
        last = self._warned.get(key)
        if last is not None and now - last < self.warn_interval:
            return
        self._warned[key] = now
        if len(self._warned) > MAX_REMEMBERED:
            self._warned.pop(next(iter(self._warned)))
        self.log.warning(
            f"[Responder] {decision.capability_id}@{decision.channel_id} 降级："
            f"{decision.reason}（候选={list(decision.candidates)}，"
            f"实际响应={list(decision.responders)}）"
        )


def channel_of(event: Any) -> str | None:
    """事件所属会话（群号）。私聊/无群 → None。

    注意：私聊事件的 ``group`` 对象**存在但 group_id=0**（OneBot 私有消息没有群号），
    早先只判断 ``None/""`` 会把它当成"群 0"：于是 Agent 的响应权候选在群 0 里找，
    永远为空 → ``[Responder] Agent 跳过`` → 私信整条不触发。``0`` 必须按"无群"处理。
    """
    if str(getattr(event, "event_type", "")) == "message_private":
        return None
    group = getattr(event, "group", None)
    group_id = getattr(group, "group_id", None) if group is not None else None
    try:
        if not int(group_id or 0):
            return None
    except (TypeError, ValueError):
        if group_id in (None, ""):
            return None
    return str(group_id)
