"""响应权仲裁（Responder Election）：同群多账号时决定「谁响应」。

这是**纯函数**：输入「候选账号 + 策略」，输出决策；不读配置、不碰网关。
候选集合的构造（是否启用/在线/在群/权限通过）在应用层 ``ResponderService`` 完成，
这样本模块可以 100% 单测，UI 预览也能复用同一套语义。

策略（``channel_policies.policy``）：

================  ==================================================
``all``           候选全部响应（默认；账号各管各的）
``dedupe``        候选 ≥2 时只选一个（稳定顺序：调用方给的顺序）
``delegate``      固定 primary；primary 不可用时按 fallback 处理
``leader``        primary 优先，不可用自动由次选接管（同样按 fallback）
================  ==================================================

fallback（``channel_policies.fallback``）：

- ``open``（默认）：退化到 ``dedupe`` 选第一个候选，``degraded=True``，**功能不静默消失**；
- ``closed``：本轮不响应，``degraded=True``（显式配置者的选择，必须能在 UI/日志里看见）。

设计来源：``docs/multi-account-design.md`` §6。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

POLICY_ALL = "all"
POLICY_DEDUPE = "dedupe"
POLICY_DELEGATE = "delegate"
POLICY_LEADER = "leader"

POLICIES: tuple[str, ...] = (POLICY_ALL, POLICY_DEDUPE, POLICY_DELEGATE, POLICY_LEADER)

FALLBACK_OPEN = "open"
FALLBACK_CLOSED = "closed"

FALLBACKS: tuple[str, ...] = (FALLBACK_OPEN, FALLBACK_CLOSED)

#: 需要「指定账号」的策略（没有 primary 视为配置缺失）
_PRIMARY_POLICIES = frozenset({POLICY_DELEGATE, POLICY_LEADER})


def normalize_policy(value: Any) -> str:
    """把外部输入规整成合法策略；非法值退化为 ``all``（不因脏配置让功能消失）。"""
    text = str(value or "").strip().lower()
    return text if text in POLICIES else POLICY_ALL


def normalize_fallback(value: Any) -> str:
    """把外部输入规整成合法 fallback；非法值退化为 ``open``（宁可重复响应，也不静默）。"""
    text = str(value or "").strip().lower()
    return text if text in FALLBACKS else FALLBACK_OPEN


def policy_from_legacy(enabled: bool, service_account_id: Any = None) -> ResponderPolicy:
    """把旧的「单一服务开关 + 群级服务账号」映射成新策略（迁移/兼容层复用）。

    - 开关关闭 → ``all``（旧行为：不过滤）；
    - 开启且指定了服务账号 → ``delegate`` + 该 primary；
    - 开启但没指定服务账号 → ``all``（旧行为：不过滤，避免改变既有部署）。
    """
    primary = str(service_account_id) if service_account_id not in (None, "") else None
    if not enabled:
        return ResponderPolicy(capability_id="", channel_id=None, policy=POLICY_ALL)
    if primary:
        return ResponderPolicy(
            capability_id="", channel_id=None, policy=POLICY_DELEGATE, primary_account_id=primary
        )
    return ResponderPolicy(capability_id="", channel_id=None, policy=POLICY_ALL)


@dataclass(frozen=True)
class ResponderPolicy:
    """某能力在某会话里的响应权策略（存储见 ``channel_policies`` 表）。"""

    capability_id: str
    channel_id: str | None = None
    policy: str = POLICY_ALL
    primary_account_id: str | None = None
    fallback: str = FALLBACK_OPEN

    def __post_init__(self) -> None:
        if self.policy not in POLICIES:
            raise ValueError(f"未知响应权策略: {self.policy!r}")
        if self.fallback not in FALLBACKS:
            raise ValueError(f"未知 fallback: {self.fallback!r}")

    @property
    def needs_primary(self) -> bool:
        return self.policy in _PRIMARY_POLICIES


@dataclass(frozen=True)
class ResponderDecision:
    """一次仲裁的结果：给节点链判断「本账号能不能响应」，也给 UI 解释「为什么」。"""

    capability_id: str
    channel_id: str | None
    policy: str
    #: 按稳定顺序排列的候选账号（已去重）
    candidates: tuple[str, ...]
    #: 最终允许响应的账号
    responders: tuple[str, ...]
    #: 结构化原因：no-candidates / all / dedupe / primary / primary-unavailable:fallback-open ...
    reason: str
    #: 是否发生了降级（fallback 生效/配置缺失）；供告警与 UI 标记
    degraded: bool = False

    def allows(self, account_id: Any) -> bool:
        """该账号本轮是否允许响应（账号 id 统一按字符串比较）。"""
        return str(account_id) in self.responders

    @property
    def single(self) -> str | None:
        return self.responders[0] if len(self.responders) == 1 else None


def normalize_candidates(candidates: Iterable[Any]) -> tuple[str, ...]:
    """去重并保持调用方给的顺序（顺序 = 配置顺序，决定 ``dedupe`` 选谁）。"""
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate is None:
            continue
        account_id = str(candidate)
        if not account_id or account_id in seen:
            continue
        seen.add(account_id)
        ordered.append(account_id)
    return tuple(ordered)


def elect(
    policy: ResponderPolicy | None,
    candidates: Iterable[Any],
    *,
    capability_id: str | None = None,
    channel_id: str | None = None,
) -> ResponderDecision:
    """纯仲裁函数：策略 + 候选集合 → 决策。

    ``policy`` 为空时按 ``all`` 处理（兼容未配置策略的旧调用）。
    """
    ordered = normalize_candidates(candidates)
    cap = capability_id if capability_id is not None else (policy.capability_id if policy else "")
    chan = channel_id if channel_id is not None else (policy.channel_id if policy else None)
    active = policy or ResponderPolicy(capability_id=cap or "", channel_id=chan)

    def decision(
        responders: Sequence[str], reason: str, *, degraded: bool = False
    ) -> ResponderDecision:
        return ResponderDecision(
            capability_id=cap,
            channel_id=chan,
            policy=active.policy,
            candidates=ordered,
            responders=tuple(responders),
            reason=reason,
            degraded=degraded,
        )

    if active.policy == POLICY_ALL:
        return decision(ordered, "all")
    if not ordered:
        return decision((), "no-candidates")
    if active.policy == POLICY_DEDUPE:
        return decision((ordered[0],), "dedupe")

    # delegate / leader
    primary = str(active.primary_account_id) if active.primary_account_id not in (None, "") else None
    if primary and primary in ordered:
        return decision((primary,), "primary")
    reason = "missing-primary" if not primary else "primary-unavailable"
    if active.fallback == FALLBACK_CLOSED:
        return decision((), f"{reason}:closed", degraded=True)
    return decision((ordered[0],), f"{reason}:fallback-open", degraded=True)


def decision_as_dict(decision: ResponderDecision) -> dict[str, Any]:
    """序列化给 WebUI / 日志（保持字段稳定）。"""
    return {
        "capability_id": decision.capability_id,
        "channel_id": decision.channel_id,
        "policy": decision.policy,
        "candidates": list(decision.candidates),
        "responders": list(decision.responders),
        "reason": decision.reason,
        "degraded": decision.degraded,
    }
