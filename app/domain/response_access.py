"""统一响应判定（Response Access）：一条消息「会不会被回、被哪一层拦、为什么」。

## 为什么需要它

「回不回」曾经散落在四处各自判定：``inbound`` 直读 ``runtime.config`` 判权限、
``pipeline`` 判场景/触发/用户例外、``responder`` 判多账号谁响应、模块自己判
``authority``。同一句话有四个答案来源，用户看到的界面写着「允许」，运行时却
不回复，而且跳过原因只有 ``debug`` 日志。

本模块给出**唯一的判定模型**：把判定拆成有序的阶段，每个阶段产出一条
``AccessCheck``（是否通过 + 稳定原因码 + 来源层 + 细节），合并成
``ResponseDecision``。运行时（入站节点 / LLM 流水线）、WebUI 预览、日志排障
用的是同一份语义与同一份 ``sources``。

## 阶段与三层语义（docs/permission-dialogue-policy-design.md §2）

============  ==========================================================
``account``   账号/能力是否启用（Agent 开关、模块 enabled）
``scene``     场所：这个群 / 这个私聊让不让它说话（含静音）
``responder`` 多账号：同群里由谁响应（正交层，不改变上面语义，只参与合并）
``group_scope`` 群黑白名单（模块/Agent 的 authority，前置 scope）
``identity``  身份：发信人档位是否够（``role_min`` 可被账号/群覆盖）
``user_scope`` 用户黑白名单 / 例外名单
``quiet_hours`` 免打扰时段
``trigger``   这一条消息是否命中触发条件（@ / 关键词 / 概率）
============  ==========================================================

前三个阶段回答「这个群能不能聊」，``identity`` / ``user_scope`` 回答「这个人能不能
触发」，``trigger`` 回答「这条消息是不是在叫我」。多账号的「谁响应」只是其中一
环：它**不**决定能不能聊，只决定由哪个号回（docs/multi-account-design.md §6）。

## 约束

- 纯函数、纯数据：不读配置、不碰网关、不做 IO，可单测、可给 UI 预览复用；
- 每个阶段都必须能回答「生效的是哪一层」（``source``：default/account/scene/channel）；
- 新增跳过分支必须走这里，不要再各自 ``return``（与 ``core/skip_log.py`` 同一口径）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from app.domain.dialogue_policy import (  # noqa: F401 - decide_trigger 为对外转出
    SOURCE_DEFAULT,
    decide_trigger,
    in_quiet_hours,
)

# ==================== 身份档位 ====================
#
# 「档位」是事件发信人的身份等级，和「谁能触发」是同一件事的两面：
# 要求 ``group_admin`` = 至少群管理才能触发。名册身份（manager/owner）跨群生效。

TIERS: tuple[str, ...] = ("everyone", "member", "group_admin", "group_owner", "manager", "owner")

TIER_RANK: dict[str, int] = {
    "member": 0,
    "group_admin": 1,
    "group_owner": 2,
    "manager": 3,
    "owner": 4,
}

#: 旧名字（``app/plugin/authority.py`` 历史导出），保留以免第三方插件读崩
PERMISSIONS: tuple[str, ...] = TIERS
ROLE_RANK: dict[str, int] = TIER_RANK


def normalize_tier(value: Any, default: str = "member") -> str:
    """把外部输入规整成合法档位；非法/空值退化为 ``default``（不因脏配置拒绝所有人）。"""
    text = str(value or "").strip().lower()
    return text if text in TIERS else default


def tier_allows(required: Any, actual: Any, *, is_private: bool = False) -> bool:
    """档位是否满足要求。

    私聊没有群角色概念：``group_admin`` / ``group_owner`` 自动降级为 ``member``，
    避免「仅群管理」在私聊里永远不通过；``manager`` / ``owner`` 是名册身份，不降级。
    """
    need = normalize_tier(required, "member")
    have = normalize_tier(actual, "member")
    if need == "everyone":
        return True
    if is_private and need in ("group_admin", "group_owner"):
        need = "member"
    return TIER_RANK.get(have, 0) >= TIER_RANK.get(need, 0)


# ==================== 判定阶段 ====================

STAGE_ACCOUNT = "account"
STAGE_SCENE = "scene"
STAGE_RESPONDER = "responder"
STAGE_GROUP_SCOPE = "group_scope"
STAGE_IDENTITY = "identity"
STAGE_USER_SCOPE = "user_scope"
STAGE_QUIET = "quiet_hours"
STAGE_TRIGGER = "trigger"

#: 展示顺序（判定链从"外层"到"内层"）
STAGE_ORDER: tuple[str, ...] = (
    STAGE_ACCOUNT,
    STAGE_SCENE,
    STAGE_RESPONDER,
    STAGE_GROUP_SCOPE,
    STAGE_IDENTITY,
    STAGE_USER_SCOPE,
    STAGE_QUIET,
    STAGE_TRIGGER,
)

STAGE_LABELS: dict[str, str] = {
    STAGE_ACCOUNT: "账号启用",
    STAGE_SCENE: "场所开关",
    STAGE_RESPONDER: "谁响应",
    STAGE_GROUP_SCOPE: "群范围",
    STAGE_IDENTITY: "身份档位",
    STAGE_USER_SCOPE: "用户例外",
    STAGE_QUIET: "免打扰",
    STAGE_TRIGGER: "触发条件",
}

#: 阶段归属：同一个决策对象被两处消费，各自只执行自己那一段（避免重复判定）。
#:
#: - ``INBOUND``：入站节点（模块链 / Agent 兜底之前）——拦下就不进模块链，也不进 LLM；
#: - ``PIPELINE``：LLM 流水线（真正请求模型之前）——命令仍要先执行（如 ``#llm on``
#:   必须能在被关闭的群里把自己打开），所以场景/例外/免打扰/触发只能在流水线判。
INBOUND_STAGES: frozenset[str] = frozenset(
    {STAGE_ACCOUNT, STAGE_RESPONDER, STAGE_GROUP_SCOPE, STAGE_IDENTITY}
)
PIPELINE_STAGES: frozenset[str] = frozenset(
    {STAGE_SCENE, STAGE_USER_SCOPE, STAGE_QUIET, STAGE_TRIGGER}
)

# ==================== 稳定原因码 ====================

REASON_OK = "ok"
#: 该阶段本轮不适用 / 预览时无法评估（不拦截）
REASON_NOT_EVALUATED = "not_evaluated"

REASON_ACCOUNT_DISABLED = "account_disabled"
REASON_SCENE_DISABLED = "scene_disabled"
REASON_GROUP_DISABLED = "group_disabled"
REASON_PRIVATE_DISABLED = "private_disabled"
REASON_MUTED = "muted"
REASON_NO_RUNTIME = "no_runtime"

REASON_RESPONDER_SKIPPED = "responder_skipped"
REASON_NO_CANDIDATES = "no_candidates"

REASON_GROUP_NOT_IN_WHITELIST = "group_not_in_whitelist"
REASON_GROUP_BLACKLISTED = "group_blacklisted"

REASON_IDENTITY_INSUFFICIENT = "identity_insufficient"

REASON_USER_NOT_IN_WHITELIST = "user_not_in_whitelist"
REASON_USER_BLACKLISTED = "user_blacklisted"

REASON_QUIET_HOURS = "quiet_hours"
REASON_TRIGGER_NOT_MATCHED = "trigger_not_matched"

#: 原因码 → 「去哪修」提示（docs/UI_COPY.md 口径：一句话 + 指到页面）
SKIP_HINTS: dict[str, str] = {
    REASON_ACCOUNT_DISABLED: "在「Agent → 基础配置」打开该账号的 Agent",
    REASON_SCENE_DISABLED: "在「权限与回复 → 群聊开关」打开该群，或在「响应场景」打开对应场景",
    REASON_GROUP_DISABLED: "在「权限与回复 → 群聊开关」把该群设为允许，或在「响应场景」打开「群聊回复」",
    REASON_PRIVATE_DISABLED: "在「响应场景」打开「私聊回复」",
    REASON_MUTED: "静音到点自动恢复；在「权限与回复」可以提前取消",
    REASON_NO_RUNTIME: "到「账号管理」确认该账号已装配 Agent 运行时",
    REASON_RESPONDER_SKIPPED: "到「多账号 → 响应权策略」把本账号设为服务账号，或把策略改为「各自响应」",
    REASON_NO_CANDIDATES: "同群没有其它可用账号；检查账号在线、在群与 Agent 是否启用",
    REASON_GROUP_NOT_IN_WHITELIST: "在模块权限 / Agent 权限里把该群加入白名单",
    REASON_GROUP_BLACKLISTED: "在模块权限 / Agent 权限里把该群移出黑名单",
    REASON_IDENTITY_INSUFFICIENT: "在「权限与回复」调整触发身份档位，或把发信人加入拥有者/管理者名册",
    REASON_USER_NOT_IN_WHITELIST: "在「权限与回复 → 高级规则」的用户白名单里加入该用户",
    REASON_USER_BLACKLISTED: "在「权限与回复 → 高级规则」的用户例外里删掉该用户",
    REASON_QUIET_HOURS: "在「权限与回复 → 高级规则」调整免打扰时段",
    REASON_TRIGGER_NOT_MATCHED: "在「权限与回复 → 高级规则」调整触发方式（@ / 关键词 / 概率）",
}


def hint_for(reason: Any) -> str:
    """原因码 → 提示语（未知原因返回空串，不猜）。"""
    return SKIP_HINTS.get(str(reason or ""), "")


def enabled_of(target: Any) -> bool:
    """能力/模块是否启用：读 ``authority.enabled``，读不到按启用处理。

    这是模块与框架级 Agent 共用的"账号/能力启用"语义（``plugin.authority``
    与 ``application.response_authority`` 都从这里取，避免两处漂移）。
    """
    authority = getattr(target, "authority", None)
    if authority is not None and hasattr(authority, "enabled"):
        return bool(authority.enabled)
    return True


def channel_of(event: Any) -> str | None:
    """事件所属会话（群号）。私聊/无群 → None。

    注意：私聊事件的 ``group`` 对象**存在但 group_id=0**（OneBot 私有消息没有群号），
    早先只判断 ``None/""`` 会把它当成"群 0"：于是响应权候选在群 0 里找，
    永远为空 → 私信整条不触发。``0`` 必须按"无群"处理。
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


def is_private_event(event: Any) -> bool:
    return str(getattr(event, "event_type", "")) == "message_private"


def validate_permission_values(permission: Mapping[str, Any] | None) -> list[str]:
    """校验权限覆盖值（``role_min`` / ``user_mode`` / ``user_list``）。

    白名单只解决"哪些键能覆盖"；值合不合法必须单独校验，否则会写进库、
    运行时静默失效（``role_min="管理员"`` 这种值会被当成默认档位）。
    """
    errors: list[str] = []
    data = permission if isinstance(permission, Mapping) else {}
    if "role_min" in data:
        if str(data.get("role_min") or "").strip().lower() not in TIERS:
            errors.append("触发身份只能是：" + "、".join(TIERS))
    if "user_mode" in data:
        if str(data.get("user_mode") or "").strip().lower() not in ("", "blacklist", "whitelist"):
            errors.append("用户名单模式只能是 blacklist / whitelist / 空（不限制）")
    if "user_list" in data:
        values = data.get("user_list")
        if not isinstance(values, list) or any(not str(item).strip() for item in values):
            errors.append("用户名单应为 QQ 号数组（不能有空项）")
    return errors



# ==================== 数据结构 ====================


@dataclass(frozen=True)
class AccessCheck:
    """一个判定阶段的结果：通过与否 + 稳定原因 + 生效来源层。"""

    stage: str
    allowed: bool
    reason: str = REASON_OK
    #: 生效来源：default / account / scene / channel / muted / responder
    source: str = SOURCE_DEFAULT
    #: 人类可读细节（日志与 UI 展示用，不参与判定）
    detail: str = ""

    @property
    def label(self) -> str:
        return STAGE_LABELS.get(self.stage, self.stage)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "label": self.label,
            "allowed": self.allowed,
            "reason": self.reason,
            "source": self.source,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ResponseDecision:
    """一条入站消息的完整判定：给运行时判断、给日志排障、给 UI 展示判定链。"""

    account_id: str
    capability: str
    channel_id: str | None
    allowed: bool
    #: 拦截它的第一个阶段的原因码；全部通过 = ``ok``
    reason: str = REASON_OK
    #: 决定性阶段（第一个不通过的阶段名；全部通过 = ``""``）
    decided_by: str = ""
    checks: tuple[AccessCheck, ...] = ()

    # 便捷字段（从 checks 派生 / 供调用方直接读，避免再遍历）
    enabled: bool = True
    enabled_source: str = SOURCE_DEFAULT
    #: 生效的要求档位（可能来自 policy.permission.role_min）
    required_tier: str = "member"
    required_tier_source: str = SOURCE_DEFAULT
    actual_tier: str = "member"

    #: 本轮生效的可覆盖配置（触发方式/频控/免打扰……）
    config: Mapping[str, Any] = field(default_factory=dict)
    #: 配置键 → 来源层
    sources: Mapping[str, str] = field(default_factory=dict)

    # 多账号仲裁（正交层）
    responder_policy: str = "all"
    responders: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    degraded: bool = False

    # ---------- 查询 ----------

    def check(self, stage: str) -> AccessCheck | None:
        for item in self.checks:
            if item.stage == stage:
                return item
        return None

    @property
    def denied(self) -> tuple[AccessCheck, ...]:
        return tuple(item for item in self.checks if not item.allowed)

    def first_denial(self, stages: frozenset[str] | None = None) -> AccessCheck | None:
        """第一个不通过的阶段（可限定阶段集合：入站/流水线各管自己那段）。"""
        for item in self.checks:
            if item.allowed:
                continue
            if stages is not None and item.stage not in stages:
                continue
            return item
        return None

    def source_of(self, key: str) -> str:
        return str(self.sources.get(key, SOURCE_DEFAULT))

    def with_checks(self, *extra: AccessCheck) -> ResponseDecision:
        """追加阶段（流水线补「触发条件」这类需要消息内容的判定）。"""
        return apply_checks(
            replace(self, checks=tuple(self.checks) + tuple(extra)),
        )

    def as_dict(self) -> dict[str, Any]:
        return decision_as_dict(self)


def apply_checks(decision: ResponseDecision) -> ResponseDecision:
    """按 ``checks`` 重算 ``allowed`` / ``reason`` / ``decided_by``（纯整理）。"""
    first_deny = next((item for item in decision.checks if not item.allowed), None)
    if first_deny is None:
        return replace(decision, allowed=True, reason=REASON_OK, decided_by="")
    return replace(
        decision,
        allowed=False,
        reason=first_deny.reason,
        decided_by=first_deny.stage,
    )


def build_decision(
    *,
    account_id: Any,
    capability: str,
    channel_id: Any = None,
    checks: Sequence[AccessCheck],
    enabled: bool = True,
    enabled_source: str = SOURCE_DEFAULT,
    required_tier: str = "member",
    required_tier_source: str = SOURCE_DEFAULT,
    actual_tier: str = "member",
    config: Mapping[str, Any] | None = None,
    sources: Mapping[str, str] | None = None,
    responder_policy: str = "all",
    responders: Iterable[Any] = (),
    candidates: Iterable[Any] = (),
    degraded: bool = False,
) -> ResponseDecision:
    """从有序检查合并成决策（第一个不通过者决定 reason / decided_by）。"""
    decision = ResponseDecision(
        account_id=str(account_id or "?"),
        capability=str(capability or "agent"),
        channel_id=None if channel_id in (None, "") else str(channel_id),
        allowed=True,
        checks=tuple(checks),
        enabled=bool(enabled),
        enabled_source=str(enabled_source or SOURCE_DEFAULT),
        required_tier=normalize_tier(required_tier, "member"),
        required_tier_source=str(required_tier_source or SOURCE_DEFAULT),
        actual_tier=normalize_tier(actual_tier, "member"),
        config=dict(config or {}),
        sources=dict(sources or {}),
        responder_policy=str(responder_policy or "all"),
        responders=tuple(str(item) for item in responders if item not in (None, "")),
        candidates=tuple(str(item) for item in candidates if item not in (None, "")),
        degraded=bool(degraded),
    )
    return apply_checks(decision)


#: 事件上携带本轮判定的属性名：入站节点算一次，流水线直接复用（不要各算一份）
DECISION_ATTR = "response_decision"


def remember_decision(event: Any, decision: ResponseDecision) -> None:
    """把本轮判定挂到事件上（流水线/统计/排障复用同一份结论）。"""
    try:
        setattr(event, DECISION_ATTR, decision)
    except Exception:  # noqa: BLE001 —— 事件对象不允许设属性时忽略（判定仍然返回）
        pass


def stored_decision(event: Any) -> ResponseDecision | None:
    """取事件上已算好的判定（没有则 None，调用方自行决定是否重算）。"""
    decision = getattr(event, DECISION_ATTR, None)
    return decision if isinstance(decision, ResponseDecision) else None


def decision_as_dict(decision: ResponseDecision | None) -> dict[str, Any]:
    """序列化给 WebUI / 日志（字段稳定，前端直接渲染判定链）。"""
    if decision is None:
        return {}
    return {
        "account_id": decision.account_id,
        "capability": decision.capability,
        "channel_id": decision.channel_id,
        "allowed": decision.allowed,
        "reason": decision.reason,
        "decided_by": decision.decided_by,
        "decided_by_label": STAGE_LABELS.get(decision.decided_by, decision.decided_by),
        "hint": hint_for(decision.reason),
        "checks": [item.as_dict() for item in decision.checks],
        "enabled": decision.enabled,
        "enabled_source": decision.enabled_source,
        "required_tier": decision.required_tier,
        "required_tier_source": decision.required_tier_source,
        "actual_tier": decision.actual_tier,
        "config": dict(decision.config),
        "sources": dict(decision.sources),
        "responder": {
            "policy": decision.responder_policy,
            "responders": list(decision.responders),
            "candidates": list(decision.candidates),
            "degraded": decision.degraded,
        },
    }


# ==================== 各阶段判定（纯函数） ====================


def account_check(enabled: Any, *, source: str = SOURCE_DEFAULT, detail: str = "") -> AccessCheck:
    """账号/能力是否启用。"""
    return AccessCheck(
        STAGE_ACCOUNT,
        bool(enabled),
        REASON_OK if enabled else REASON_ACCOUNT_DISABLED,
        source,
        detail,
    )


def scene_check(
    enabled: Any,
    *,
    source: str = SOURCE_DEFAULT,
    channel_id: Any = None,
    is_private: bool = False,
) -> AccessCheck:
    """场所层：群聊/私聊是否允许它说话（静音也是一种来源）。"""
    ok = bool(enabled)
    if ok:
        reason = REASON_OK
    elif str(source) == "muted":
        reason = REASON_MUTED
    else:
        reason = REASON_PRIVATE_DISABLED if is_private else REASON_GROUP_DISABLED
    detail = "私聊" if is_private else (f"group={channel_id}" if channel_id not in (None, "") else "")
    return AccessCheck(STAGE_SCENE, ok, reason, source, detail)


def responder_check(
    allowed: Any,
    *,
    policy: str = "all",
    responders: Iterable[Any] = (),
    candidates: Iterable[Any] = (),
    reason: str = "",
    degraded: bool = False,
    account_id: Any = None,
) -> AccessCheck:
    """多账号：本账号这一轮是否被响应权策略选中。

    ``reason`` 是仲裁器给出的结构化原因（``dedupe`` / ``primary`` /
    ``primary-unavailable:fallback-open`` / ``no-candidates`` …），原样放进 detail，
    UI 与日志据此解释「为什么是这个号」。
    """
    ok = bool(allowed)
    detail_parts = [f"policy={policy}", f"account={account_id}"]
    if reason:
        detail_parts.append(f"reason={reason}")
    if candidates:
        detail_parts.append("candidates=" + ",".join(str(item) for item in candidates))
    if degraded:
        detail_parts.append("degraded=true")
    if ok:
        check_reason = REASON_OK
    elif str(reason or "") == "no-candidates":
        check_reason = REASON_NO_CANDIDATES
    else:
        check_reason = REASON_RESPONDER_SKIPPED
    source = str(reason or "responder")
    return AccessCheck(STAGE_RESPONDER, ok, check_reason, source, " · ".join(detail_parts))


def group_scope_check(
    mode: Any,
    values: Any,
    group_id: Any,
    *,
    source: str = SOURCE_DEFAULT,
) -> AccessCheck:
    """群黑白名单（模块/Agent 的 authority 前置 scope）。"""
    mode_text = str(mode or "").strip().lower()
    listed = _listed(values, group_id)
    reason = scope_reason(mode_text, listed, kind="group")
    detail = f"group={group_id} · mode={mode_text or 'unset'}"
    if reason is None:
        return AccessCheck(STAGE_GROUP_SCOPE, True, REASON_OK, source, detail)
    return AccessCheck(STAGE_GROUP_SCOPE, False, reason, source, detail)


def user_scope_check(
    mode: Any,
    values: Any,
    user_id: Any,
    *,
    source: str = SOURCE_DEFAULT,
) -> AccessCheck:
    """用户黑白名单 / 例外名单。"""
    mode_text = str(mode or "").strip().lower()
    listed = _listed(values, user_id)
    reason = scope_reason(mode_text, listed, kind="user")
    detail = f"user={user_id} · mode={mode_text or 'unset'}"
    if reason is None:
        return AccessCheck(STAGE_USER_SCOPE, True, REASON_OK, source, detail)
    return AccessCheck(STAGE_USER_SCOPE, False, reason, source, detail)


def identity_check(
    required: Any,
    actual: Any,
    *,
    is_private: bool = False,
    source: str = SOURCE_DEFAULT,
    actual_source: str = "role",
) -> AccessCheck:
    """身份档位：发信人够不够触发（``role_min`` 可被账号/群覆盖）。"""
    need = normalize_tier(required, "member")
    have = normalize_tier(actual, "member")
    ok = tier_allows(need, have, is_private=is_private)
    detail = f"需要 {need}（{source}）· 实际 {have}（{actual_source}）"
    return AccessCheck(
        STAGE_IDENTITY, ok, REASON_OK if ok else REASON_IDENTITY_INSUFFICIENT, source, detail
    )


def quiet_check(window: Any, *, now_minutes: int | None = None) -> AccessCheck:
    """免打扰时段（非法/空窗口一律不静默，避免写错一次就永久沉默）。"""
    text = str(window or "").strip()
    hit = in_quiet_hours(text, now_minutes) if text else False
    detail = f"window={text}" if text else "未启用"
    return AccessCheck(STAGE_QUIET, not hit, REASON_QUIET_HOURS if hit else REASON_OK, "channel", detail)


def trigger_check(
    matched: Any,
    *,
    mode: str = "",
    probability: int | None = None,
    detail: str = "",
) -> AccessCheck:
    """触发条件：这一条消息是否在叫我（@ / 关键词 / 概率）。"""
    ok = bool(matched)
    parts = [f"mode={mode}"] if mode else []
    if probability is not None and mode == "all":
        parts.append(f"probability={probability}")
    if detail:
        parts.append(detail)
    return AccessCheck(
        STAGE_TRIGGER, ok, REASON_OK if ok else REASON_TRIGGER_NOT_MATCHED, "channel", " · ".join(parts)
    )


def not_evaluated_check(stage: str, *, detail: str = "本轮不适用", source: str = SOURCE_DEFAULT) -> AccessCheck:
    """该阶段本轮没有输入（如预览时不知道发信人身份）；不拦截。"""
    return AccessCheck(stage, True, REASON_NOT_EVALUATED, source, detail)


# ==================== 触发判定（复用 dialogue_policy 的唯一实现） ====================
#
# 触发方式是「规则」层的一部分，纯函数已由 ``domain/dialogue_policy`` 提供；
# 这里只做转出，避免两处判定漂移（历史上 @ / 关键词就有两份实现）。


# ==================== 内部小工具 ====================


def _as_id_set(value: Any) -> set[str]:
    """把名单（list/字符串/None）规整成字符串集合。"""
    if not value:
        return set()
    if isinstance(value, str):
        value = value.replace(",", "\n").splitlines()  # noqa: PLW2901 - 允许 "1,2,3" 写法
    if not isinstance(value, Iterable):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def _listed(values: Any, item_id: Any) -> bool:
    key = str(item_id or "").strip()
    return bool(key) and key in _as_id_set(values)


def scope_reason(mode: str, listed: bool, *, kind: str) -> str | None:
    """黑白名单语义：``whitelist`` 未命中拦截；``blacklist`` 命中拦截；其它放行。"""
    if mode == "whitelist":
        return None if listed else f"{kind}_not_in_whitelist"
    if mode == "blacklist":
        return f"{kind}_blacklisted" if listed else None
    return None
