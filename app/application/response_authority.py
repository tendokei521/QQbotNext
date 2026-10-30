"""统一响应判定器（ResponseAuthority）：运行时与 UI 预览共用的唯一判定入口。

## 职责

把「一条消息会不会被回、被哪一层拦、为什么」收敛到一个地方：

1. **取输入**：能力自身配置（Agent ``runtime.config`` / 模块配置）、
   账号/场景/群三层覆盖（``channel_overrides``，见 :mod:`app.domain.dialogue_policy`）、
   多账号响应权仲裁（:class:`app.application.responder_service.ResponderService`）、
   事件身份（``event.permission_role``，由名册 + 群角色算出）；
2. **按阶段判定**：账号启用 → 场所开关 → 谁响应 → 群范围 → 身份档位 → 用户例外
   → 免打扰（触发条件由流水线按消息内容补，见 ``ResponseDecision.with_checks``）；
3. **输出** :class:`app.domain.response_access.ResponseDecision`：有序判定链 +
   稳定原因码 + 每层来源，运行时用它跳过、日志用它解释、WebUI 用它画判定链。

## 不在这里做的事

- 不做事件分发（节点链拿决策自己决定跳过/继续）；
- 不构造"候选集合"（那是 ``ResponderService`` 的活，本类只消费它的决策）；
- 不写任何配置（纯读，因此可被多个节点/请求安全复用）。

## 多账号（正交层）

「谁响应」不决定"能不能聊"，只决定"由哪个号回"：本类把仲裁结果作为 **responder 阶段**
放进判定链，同时保留 ``responders/candidates/policy/degraded`` 字段供 UI 展示
「本群当前由谁响应、为什么」。这样既不会把两件事混成一份配置，也不会让用户
在"为什么不回"时看不到仲裁这一层（docs/multi-account-design.md §6）。
"""

from __future__ import annotations

from typing import Any

from app.application.dialogue_policy_service import DEFAULT_CAPABILITY, DialoguePolicyService
from app.core.logger import logger
from app.domain.response_access import (
    REASON_NO_RUNTIME,
    REASON_RESPONDER_SKIPPED,
    SOURCE_DEFAULT,
    STAGE_ACCOUNT,
    STAGE_RESPONDER,
    AccessCheck,
    ResponseDecision,
    account_check,
    build_decision,
    channel_of,
    enabled_of,
    group_scope_check,
    identity_check,
    is_private_event,
    normalize_tier,
    not_evaluated_check,
    quiet_check,
    remember_decision,
    responder_check,
    responder_policy_label,
    scene_check,
    tier_label,
    user_scope_check,
)

#: 模块声明支持"单一服务"时的旧判定来源标记（responder 未装配时的回落路径）
SOURCE_SINGLE_SERVICE = "single_service"


class ResponseAuthority:
    """统一判定器。只读依赖：配置中心、Agent 管理器、响应权仲裁器。"""

    def __init__(
        self,
        *,
        config_service: Any = None,
        agent_manager: Any = None,
        responder: Any = None,
        registry: Any = None,
        runtime: Any = None,
        log: Any = None,
        legacy_skip: Any = None,
    ) -> None:
        self.config_service = config_service
        self.agent_manager = agent_manager
        self.responder = responder
        self.registry = registry
        #: 直接持有运行时（流水线路径用）：避免再经 agent_manager 反查
        self.runtime = runtime
        self.log = log or logger
        #: responder 未装配时的旧「单一服务」判定：``callable(event, module=None) -> bool``
        self.legacy_skip = legacy_skip

    # ==================== 对外入口 ====================

    def decide_agent(self, event: Any, *, now_minutes: int | None = None) -> ResponseDecision:
        """框架级 Agent：这条消息本账号会不会回（含场景/身份/例外/多账号）。"""
        account_id = self._account_of(event)
        channel_id = channel_of(event)
        private = is_private_event(event)
        runtime = self._runtime(account_id)
        config = getattr(runtime, "config", None)
        policy = self._policy(account_id, DEFAULT_CAPABILITY, channel_id, runtime)

        checks: list[AccessCheck] = []
        if config is None:
            checks.append(
                AccessCheck(STAGE_ACCOUNT, False, REASON_NO_RUNTIME, SOURCE_DEFAULT, "账号没有 Agent 运行时")
            )
            return build_decision(
                account_id=account_id,
                capability=DEFAULT_CAPABILITY,
                channel_id=channel_id,
                checks=checks,
            )

        checks.append(
            account_check(bool(getattr(config, "enabled", True)), source="agent_config", detail="Agent 开关")
        )

        # 场所层：有策略层就用解析结果（含静音），否则回落 group_enable/private_enable
        if policy is not None:
            enabled, enabled_source = bool(policy.enabled), str(policy.enabled_source)
        else:
            enabled, enabled_source = self._fallback_scene(config, private)
        checks.append(
            scene_check(enabled, source=enabled_source, channel_id=channel_id, is_private=private)
        )

        # 多账号：本账号本轮是否被选中
        responder_info = self._responder_decision(event, account_id, DEFAULT_CAPABILITY)
        checks.append(responder_info["check"])

        # 群范围 / 用户例外（Agent authority，跨群生效）
        permission = getattr(config, "permission", None)
        group_id = str(getattr(getattr(event, "group", None), "group_id", "") or "") or None
        if group_id and permission is not None:
            checks.append(
                group_scope_check(
                    getattr(permission, "group_mode", ""),
                    getattr(permission, "group_list", []),
                    group_id,
                    source="agent_config",
                )
            )

        # 身份档位：policy.permission.role_min 可覆盖账号级 permission（此前是死键）
        required, required_source = self._required_tier(policy, config)
        actual = normalize_tier(getattr(event, "permission_role", ""), "member")
        checks.append(
            identity_check(required, actual, is_private=private, source=required_source, actual_source="role")
        )

        # 用户例外：Agent authority 名单 + 策略层名单（账号层/群层各一份，来源可区分）
        user_id = getattr(event, "user_id", "")
        if permission is not None:
            checks.append(
                user_scope_check(
                    getattr(permission, "user_mode", ""),
                    getattr(permission, "user_list", []),
                    user_id,
                    source="agent_config",
                )
            )
        policy_permission = getattr(policy, "permission", {}) if policy is not None else {}
        if policy_permission.get("user_mode"):
            checks.append(
                user_scope_check(
                    policy_permission.get("user_mode"),
                    policy_permission.get("user_list"),
                    user_id,
                    source=policy.source_of("user_mode") if policy is not None else SOURCE_DEFAULT,
                )
            )

        # 免打扰：策略层的 quiet_hours（账号级/群级覆盖）
        window = ""
        if policy is not None:
            window = str(policy.config.get("quiet_hours") or "")
        else:
            window = str(self._config_get(config, "quiet_hours", "") or "")
        if window:
            checks.append(quiet_check(window, now_minutes=now_minutes))

        return build_decision(
            account_id=account_id,
            capability=DEFAULT_CAPABILITY,
            channel_id=channel_id,
            checks=checks,
            enabled=enabled,
            enabled_source=enabled_source,
            required_tier=required,
            required_tier_source=required_source,
            actual_tier=actual,
            config=dict(policy.config) if policy is not None else {},
            sources=dict(policy.sources) if policy is not None else {},
            responder_policy=responder_info["policy"],
            responders=responder_info["responders"],
            candidates=responder_info["candidates"],
            degraded=responder_info["degraded"],
        )

    def decide_module(self, module: Any, event: Any) -> ResponseDecision:
        """业务模块：这个模块实例本轮会不会处理这条消息。

        模块的群级覆盖（``channel_overrides`` 的 capability=模块名）还没开放
        （design §10.5 v2），所以这里只走 authority + responder，语义与改造前一致。
        """
        account_id = self._account_of(event)
        channel_id = channel_of(event)
        private = is_private_event(event)
        name = str(getattr(module, "module_name", "") or getattr(module, "name", "") or "?")
        authority = getattr(module, "authority", None)
        permission = getattr(authority, "permission", None) if authority is not None else None

        checks: list[AccessCheck] = [
            account_check(enabled_of(module), source="module_authority", detail=f"module={name}")
        ]
        responder_info = self._responder_decision(event, account_id, name, module=module)
        checks.append(responder_info["check"])

        group_id = str(getattr(getattr(event, "group", None), "group_id", "") or "") or None
        if group_id and permission is not None:
            checks.append(
                group_scope_check(
                    getattr(permission, "group_mode", ""),
                    getattr(permission, "group_list", []),
                    group_id,
                    source="module_authority",
                )
            )

        required = normalize_tier(getattr(module, "permission", "member"), "member")
        actual = normalize_tier(getattr(event, "permission_role", ""), "member")
        checks.append(
            identity_check(required, actual, is_private=private, source="module", actual_source="role")
        )

        if permission is not None:
            checks.append(
                user_scope_check(
                    getattr(permission, "user_mode", ""),
                    getattr(permission, "user_list", []),
                    getattr(event, "user_id", ""),
                    source="module_authority",
                )
            )

        return build_decision(
            account_id=account_id,
            capability=name,
            channel_id=channel_id,
            checks=checks,
            required_tier=required,
            actual_tier=actual,
            responder_policy=responder_info["policy"],
            responders=responder_info["responders"],
            candidates=responder_info["candidates"],
            degraded=responder_info["degraded"],
        )

    # ==================== UI 预览 / 判定链 ====================

    def explain(
        self,
        *,
        account_id: Any,
        capability: str = DEFAULT_CAPABILITY,
        channel_id: Any = None,
        user_id: Any = None,
        role: Any = None,
        now_minutes: int | None = None,
    ) -> dict:
        """给定账号/群/用户，算一遍完整的判定链（不依赖事件，供 WebUI 预览）。

        与真实路径的差别只有两处，且都显式标成 ``not_evaluated``：
        - 没给 ``user_id`` → 用户例外阶段不评估；
        - 没给 ``role`` → 身份档位不评估（不伪造一个 member 去误判）。
        """
        aid = str(account_id or "")
        cap = str(capability or DEFAULT_CAPABILITY)
        channel = None if channel_id in (None, "") else str(channel_id)
        private = channel is None
        module = None
        if cap != DEFAULT_CAPABILITY and self.registry is not None:
            try:
                module = self.registry.get(cap, aid)
            except Exception as e:  # noqa: BLE001 —— 预览不能因注册表异常整体失败
                self.log.debug(f"[Access] 预览取模块实例失败（按无模块处理）: {e}")
                module = None

        runtime = self._runtime(aid)
        config = getattr(runtime, "config", None)
        policy = None if module is not None else self._policy(aid, cap, channel, runtime)

        checks: list[AccessCheck] = []
        scene_enabled_value: Any = True
        scene_source: str = SOURCE_DEFAULT
        if module is not None:
            checks.append(
                account_check(enabled_of(module), source="module_authority", detail=f"module={cap}")
            )
            responder_info = self._responder_preview(account_id=aid, capability=cap, channel_id=channel)
            checks.append(responder_info["check"])
            permission = getattr(getattr(module, "authority", None), "permission", None)
            required = normalize_tier(getattr(module, "permission", "member"), "member")
            required_source = "module"
        elif config is not None:
            checks.append(
                account_check(bool(getattr(config, "enabled", True)), source="agent_config", detail="Agent 开关")
            )
            if policy is not None:
                scene_enabled_value, scene_source = bool(policy.enabled), str(policy.enabled_source)
            else:
                scene_enabled_value, scene_source = self._fallback_scene(config, private)
            checks.append(
                scene_check(
                    scene_enabled_value, source=scene_source, channel_id=channel, is_private=private
                )
            )
            responder_info = self._responder_preview(account_id=aid, capability=cap, channel_id=channel)
            checks.append(responder_info["check"])
            permission = getattr(config, "permission", None)
            required, required_source = self._required_tier(policy, config)
        else:
            checks.append(
                AccessCheck(STAGE_ACCOUNT, False, REASON_NO_RUNTIME, SOURCE_DEFAULT, "账号没有 Agent 运行时")
            )
            responder_info = {
                "check": not_evaluated_check(STAGE_RESPONDER),
                "policy": "all",
                "responders": (),
                "candidates": (),
                "degraded": False,
            }
            permission = None
            required, required_source = "member", SOURCE_DEFAULT

        scope_source = "agent_config" if module is None else "module_authority"
        if channel and permission is not None:
            checks.append(
                group_scope_check(
                    getattr(permission, "group_mode", ""),
                    getattr(permission, "group_list", []),
                    channel,
                    source=scope_source,
                )
            )

        if role is not None:
            actual = normalize_tier(role, "member")
            checks.append(
                identity_check(required, actual, is_private=private, source=required_source, actual_source="输入")
            )
        else:
            actual = ""
            checks.append(not_evaluated_check("identity", detail=f"未指定发信人身份（要求 {tier_label(required)}）"))

        if user_id is not None and user_id != "":
            if permission is not None:
                checks.append(
                    user_scope_check(
                        getattr(permission, "user_mode", ""),
                        getattr(permission, "user_list", []),
                        user_id,
                        source=scope_source,
                    )
                )
            policy_permission = getattr(policy, "permission", {}) if policy is not None else {}
            if policy_permission.get("user_mode"):
                checks.append(
                    user_scope_check(
                        policy_permission.get("user_mode"),
                        policy_permission.get("user_list"),
                        user_id,
                        source=policy.source_of("user_mode") if policy is not None else SOURCE_DEFAULT,
                    )
                )
        else:
            checks.append(not_evaluated_check("user_scope", detail="未指定用户（不评估用户例外）"))

        window = str(policy.config.get("quiet_hours") or "") if policy is not None else ""
        if window:
            checks.append(quiet_check(window, now_minutes=now_minutes))

        decision = build_decision(
            account_id=aid,
            capability=cap,
            channel_id=channel,
            checks=checks,
            enabled=bool(scene_enabled_value),
            enabled_source=scene_source,
            required_tier=required,
            required_tier_source=required_source,
            actual_tier=actual or "member",
            config=dict(policy.config) if policy is not None else {},
            sources=dict(policy.sources) if policy is not None else {},
            responder_policy=responder_info["policy"],
            responders=responder_info["responders"],
            candidates=responder_info["candidates"],
            degraded=responder_info["degraded"],
        )
        out = decision.as_dict()
        # 预览额外附带「多账号候选」，UI 一次拿全，不用再发请求
        out["candidates"] = list(decision.candidates)
        return out

    # ==================== 内部：策略 / 运行时 ====================

    def _runtime(self, account_id: Any) -> Any:
        if self.runtime is not None:
            return self.runtime
        manager = self.agent_manager
        if manager is None or account_id in (None, ""):
            return None
        try:
            return manager.get_runtime(account_id)
        except Exception as e:  # noqa: BLE001 —— 运行时缺席按"没有配置"处理
            self.log.debug(f"[Access] 取运行时失败 {account_id}: {e}")
            return None

    def _policy(self, account_id: Any, capability: str, channel_id: Any, runtime: Any):
        """解析生效策略；读不到（无配置中心/旧运行时替身）返回 None（调用方回落）。"""
        service = self.config_service
        if service is None or not hasattr(service, "get_channel_override"):
            return None
        try:
            return DialoguePolicyService(service, runtime=runtime).resolve(
                account_id, capability, channel_id
            )
        except Exception as e:  # noqa: BLE001 —— 策略解析失败不能阻断对话
            self.log.debug(f"[Access] 策略解析失败，回落账号级配置: {e}")
            return None

    @staticmethod
    def _fallback_scene(config: Any, private: bool) -> tuple[bool, str]:
        """没有策略层时的场所开关（升级零行为漂移：等价 group_enable/private_enable）。"""
        getter = getattr(config, "get", None)
        if not callable(getter):
            return bool(private), SOURCE_DEFAULT
        if private:
            return bool(getter("private_enable", True)), SOURCE_DEFAULT
        return bool(getter("group_enable", False)), SOURCE_DEFAULT

    @staticmethod
    def _config_get(config: Any, key: str, default: Any = None) -> Any:
        getter = getattr(config, "get", None)
        if not callable(getter):
            return default
        try:
            return getter(key, default)
        except Exception:  # noqa: BLE001
            return default

    def _required_tier(self, policy: Any, config: Any) -> tuple[str, str]:
        """生效要求档位：``policy.permission.role_min``（账号/场景/群层）优先。"""
        role_min = None
        source = SOURCE_DEFAULT
        if policy is not None:
            permission = getattr(policy, "permission", {}) or {}
            role_min = permission.get("role_min")
            if role_min:
                source = policy.source_of("role_min")
        if role_min:
            return normalize_tier(role_min, "member"), source
        base = self._config_get(config, "permission", "member")
        return normalize_tier(base, "member"), SOURCE_DEFAULT

    # ==================== 内部：多账号仲裁 ====================

    def _responder_decision(
        self, event: Any, account_id: Any, capability: str, *, module: Any = None
    ) -> dict:
        """真实事件的仲裁：responder 优先，未装配时退回旧的单一服务判定。"""
        if self.responder is not None:
            try:
                decision = (
                    self.responder.decide_module(capability, event)
                    if module is not None
                    else self.responder.decide_agent(event)
                )
            except Exception as e:  # noqa: BLE001 —— 仲裁失败 fail-open 且留痕（A5）
                self.log.warning(f"[Access] 响应权仲裁失败（本轮放行）: {e}")
                return _responder_bundle(
                    not_evaluated_check(STAGE_RESPONDER, detail=f"仲裁异常：{e}")
                )
            allowed = decision.allows(account_id)
            bundle = _responder_bundle(
                responder_check(
                    allowed,
                    policy=decision.policy,
                    responders=decision.responders,
                    candidates=decision.candidates,
                    reason=decision.reason,
                    degraded=decision.degraded,
                    account_id=account_id,
                ),
                decision=decision,
            )
            remember_decision(event, decision)
            return bundle

        if self.legacy_skip is not None:
            try:
                skipped = bool(self.legacy_skip(event, module))
            except Exception as e:  # noqa: BLE001
                self.log.debug(f"[Access] 单一服务回落判定异常（按不跳过处理）: {e}")
                skipped = False
            if skipped:
                return _responder_bundle(
                    AccessCheck(
                        STAGE_RESPONDER,
                        False,
                        REASON_RESPONDER_SKIPPED,
                        SOURCE_SINGLE_SERVICE,
                        "单一服务：本群响应权已交给其它账号",
                    )
                )
            return _responder_bundle(
                not_evaluated_check(
                    STAGE_RESPONDER, detail="单一服务：本账号负责本群", source=SOURCE_SINGLE_SERVICE
                )
            )

        return _responder_bundle(not_evaluated_check(STAGE_RESPONDER, detail="未启用多账号仲裁"))

    def _responder_preview(self, *, account_id: Any, capability: str, channel_id: Any) -> dict:
        """预览：按当前在线/在群/启用状态算候选（不看事件级权限）。

        没有候选时**不判"不会回"**：预览环境的账号本来就没连上（离线/未在群），
        据此下结论会把"机器人没上线"误报成"响应权拦了"。此时标记 ``not_evaluated``，
        但把策略/候选/响应者一并带出去，UI 仍能解释"这一层是什么、会选谁"。
        """
        if self.responder is None:
            return _responder_bundle(not_evaluated_check(STAGE_RESPONDER, detail="未启用多账号仲裁"))
        try:
            decision = self.responder.preview_capability(str(capability), channel_id)
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"[Access] 响应权预览失败: {e}")
            return _responder_bundle(not_evaluated_check(STAGE_RESPONDER, detail=f"预览失败：{e}"))
        if not decision.candidates:
            return _responder_bundle(
                not_evaluated_check(
                    STAGE_RESPONDER,
                    detail=(
                        f"当前没有在线候选（策略：{responder_policy_label(decision.policy)}），"
                        "响应权不参与判定"
                    ),
                ),
                decision=decision,
            )
        allowed = bool(account_id) and str(account_id) in decision.responders
        return _responder_bundle(
            responder_check(
                allowed,
                policy=decision.policy,
                responders=decision.responders,
                candidates=decision.candidates,
                reason=decision.reason,
                degraded=decision.degraded,
                account_id=account_id,
            ),
            decision=decision,
        )

    # ==================== 内部：其它 ====================

    def _account_of(self, event: Any) -> str:
        """本轮账号：流水线持有的运行时就是当前账号（事件缺 account_id 时不能误判）。"""
        if self.runtime is not None:
            bot_id = getattr(self.runtime, "bot_id", None)
            if bot_id not in (None, ""):
                return str(bot_id)
        account_id = getattr(event, "account_id", None)
        if account_id in (None, ""):
            account_id = getattr(event, "self_id", None)
        return str(account_id or "?")


def _responder_bundle(check: AccessCheck, *, decision: Any = None) -> dict:
    """把仲裁结果整理成判定器内部用的统一小结构。"""
    return {
        "check": check,
        "policy": str(getattr(decision, "policy", "all") or "all"),
        "responders": tuple(getattr(decision, "responders", ()) or ()),
        "candidates": tuple(getattr(decision, "candidates", ()) or ()),
        "degraded": bool(getattr(decision, "degraded", False)),
    }
