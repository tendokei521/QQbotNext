"""统一判定器（ResponseAuthority）契约：运行时真正生效的那条判定链。

重点回归（此前都是"配置能写但运行时不生效"）：
- 群级 ``permission.role_min`` 必须真的决定"谁能触发"；
- 场所关闭 / 用户例外必须带来源层且能在判定链里看到；
- 多账号响应权是判定链的一层，但**不**和"能不能聊"合并成一份配置；
- 没有配置中心的旧运行时替身必须回落而不是崩。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.application.response_authority import ResponseAuthority
from app.domain.response_access import (
    REASON_GROUP_DISABLED,
    REASON_IDENTITY_INSUFFICIENT,
    REASON_RESPONDER_SKIPPED,
    REASON_USER_BLACKLISTED,
    STAGE_IDENTITY,
    STAGE_RESPONDER,
    STAGE_SCENE,
    STAGE_USER_SCOPE,
)


class _Config:
    def __init__(self, data=None, enabled=True, permission=None):
        self.raw_config = dict(data or {})
        self.enabled = enabled
        self.permission = permission or SimpleNamespace(
            group_mode="blacklist", group_list=[], user_mode="blacklist", user_list=[]
        )

    def get(self, key, default=None):
        return self.raw_config.get(key, default)


class _ConfigService:
    def __init__(self, overrides=None):
        self._overrides = overrides or {}

    def get_channel_override(self, account_id, capability="agent", channel_id=""):
        return self._overrides.get((str(account_id), str(capability), str(channel_id or "")))

    def get_module_config(self, capability, account_id):
        return {}

    def get_module_authority(self, capability, account_id):
        return {}


class _Runtime:
    def __init__(self, data=None, *, enabled=True, overrides=None, permission=None):
        self.bot_id = "acc1"
        self.config = _Config(data, enabled, permission)
        self.config_service = _ConfigService(overrides)


def _event(*, private=False, group_id=111, user_id=1, role="member", account_id="acc1"):
    event = SimpleNamespace(
        event_type="message_private" if private else "message_group",
        account_id=account_id,
        self_id=1,
        user_id=user_id,
        permission_role=role,
    )
    if not private:
        event.group = SimpleNamespace(group_id=group_id)
    return event


def _override(**kwargs):
    row = {"enabled": None, "config": {}, "permission": {}}
    row.update(kwargs)
    return row


# ==================== Agent：场所 / 身份 / 例外 ====================


def test_group_disabled_by_channel_reports_source():
    runtime = _Runtime({"group_enable": True}, overrides={("acc1", "agent", "111"): _override(enabled=False)})
    decision = ResponseAuthority(config_service=runtime.config_service, runtime=runtime).decide_agent(_event())

    assert decision.allowed is False
    assert decision.reason == REASON_GROUP_DISABLED
    assert decision.decided_by == STAGE_SCENE
    assert decision.enabled is False
    assert decision.enabled_source == "channel"


def test_role_min_override_now_actually_gates():
    """回归：``role_min`` 曾经只进白名单、从不被执行（配了不生效）。"""
    overrides = {("acc1", "agent", "111"): _override(permission={"role_min": "group_admin"})}
    runtime = _Runtime({"group_enable": True, "permission": "member"}, overrides=overrides)
    authority = ResponseAuthority(config_service=runtime.config_service, runtime=runtime)

    denied = authority.decide_agent(_event(role="member"))
    assert denied.allowed is False
    assert denied.reason == REASON_IDENTITY_INSUFFICIENT
    assert denied.decided_by == STAGE_IDENTITY
    assert denied.required_tier == "group_admin"
    assert denied.required_tier_source == "channel"

    allowed = authority.decide_agent(_event(role="group_admin"))
    assert allowed.allowed is True


def test_role_min_private_chat_still_downgrades():
    overrides = {("acc1", "agent", ""): _override(permission={"role_min": "group_admin"})}
    runtime = _Runtime({"private_enable": True}, overrides=overrides)
    decision = ResponseAuthority(config_service=runtime.config_service, runtime=runtime).decide_agent(
        _event(private=True, role="member")
    )
    assert decision.allowed is True, "私聊里「仅群管理」应自动降级，不能永远不通过"


def test_policy_user_blacklist_is_a_user_scope_denial():
    overrides = {
        ("acc1", "agent", "111"): _override(permission={"user_mode": "blacklist", "user_list": ["42"]})
    }
    runtime = _Runtime({"group_enable": True}, overrides=overrides)
    authority = ResponseAuthority(config_service=runtime.config_service, runtime=runtime)

    denied = authority.decide_agent(_event(user_id=42))
    assert denied.allowed is False
    assert denied.reason == REASON_USER_BLACKLISTED
    assert denied.decided_by == STAGE_USER_SCOPE
    assert authority.decide_agent(_event(user_id=7)).allowed is True


def test_no_config_service_falls_back_without_crash():
    """旧运行时替身（没有 config_service）必须走回落路径，不能抛异常。"""
    authority = ResponseAuthority(config_service=None, runtime=_Runtime({"group_enable": True}))
    decision = authority.decide_agent(_event())
    assert decision.allowed is True
    assert decision.enabled_source == "default"


def test_agent_disabled_is_account_stage():
    runtime = _Runtime({"group_enable": True}, enabled=False)
    decision = ResponseAuthority(config_service=runtime.config_service, runtime=runtime).decide_agent(_event())
    assert decision.allowed is False
    assert decision.decided_by == "account"


# ==================== 模块 ====================


class _Authority:
    def __init__(self, enabled=True, mode="blacklist", users=()):
        self.enabled = enabled
        self.permission = SimpleNamespace(
            group_mode=mode, group_list=[], user_mode="blacklist", user_list=list(users)
        )


class _Module:
    def __init__(self, *, enabled=True, permission="member", users=()):
        self.module_name = "demo"
        self.name = "Demo"
        self.permission = permission
        self.authority = _Authority(enabled, users=users)


def test_module_disabled_denied_at_account_stage():
    authority = ResponseAuthority(config_service=_ConfigService(), runtime=_Runtime({}))
    decision = authority.decide_module(_Module(enabled=False), _event())
    assert decision.allowed is False
    assert decision.decided_by == "account"


def test_module_permission_tier_enforced():
    authority = ResponseAuthority(config_service=_ConfigService(), runtime=_Runtime({}))
    module = _Module(permission="manager")
    assert authority.decide_module(module, _event(role="member")).allowed is False
    assert authority.decide_module(module, _event(role="manager")).allowed is True


def test_module_user_blacklist_enforced():
    authority = ResponseAuthority(config_service=_ConfigService(), runtime=_Runtime({}))
    module = _Module(users=["42"])
    decision = authority.decide_module(module, _event(user_id=42))
    assert decision.allowed is False
    assert decision.reason == REASON_USER_BLACKLISTED


# ==================== 多账号（正交层） ====================


class _Responder:
    """最小仲裁替身：只回一个固定决策（候选/响应者由用例指定）。"""

    def __init__(self, responders=("acc1",), reason="dedupe", policy="dedupe"):
        self._responders = tuple(responders)
        self._reason = reason
        self._policy = policy

    def preview_capability(self, capability_id, channel_id):
        from app.plugin.responder import ResponderDecision

        return ResponderDecision(
            capability_id=capability_id,
            channel_id=channel_id,
            policy=self._policy,
            candidates=("acc1", "acc2"),
            responders=self._responders,
            reason=self._reason,
        )

    def decide_agent(self, event):
        return self.preview_capability("agent", "111")

    def decide_module(self, module_name, event):
        return self.preview_capability(module_name, "111")


def test_responder_layer_denies_and_is_explained():
    runtime = _Runtime({"group_enable": True})
    authority = ResponseAuthority(
        config_service=runtime.config_service, runtime=runtime, responder=_Responder(responders=("acc2",))
    )
    decision = authority.decide_agent(_event(account_id="acc1"))
    assert decision.allowed is False
    assert decision.reason == REASON_RESPONDER_SKIPPED
    assert decision.decided_by == STAGE_RESPONDER
    check = decision.check(STAGE_RESPONDER)
    assert check is not None and "同群只回一次" in check.detail, "仲裁原因要给用户看得懂的说明"


def test_responder_selection_keeps_other_layers_intact():
    """被仲裁选中时，其余层照常判定：响应权不会绕过"群被关闭"。"""
    overrides = {("acc1", "agent", "111"): _override(enabled=False)}
    runtime = _Runtime({"group_enable": True}, overrides=overrides)
    authority = ResponseAuthority(
        config_service=runtime.config_service, runtime=runtime, responder=_Responder(responders=("acc1",))
    )
    decision = authority.decide_agent(_event())
    assert decision.allowed is False
    assert decision.reason == REASON_GROUP_DISABLED


def test_legacy_single_service_fallback_marks_source():
    runtime = _Runtime({"group_enable": True})
    authority = ResponseAuthority(
        config_service=runtime.config_service,
        runtime=runtime,
        legacy_skip=lambda event, module=None: True,
    )
    decision = authority.decide_agent(_event())
    assert decision.allowed is False
    check = decision.check(STAGE_RESPONDER)
    assert check.source == "single_service"


# ==================== 预览 / 判定链 ====================


def test_explain_marks_unknown_inputs_not_evaluated():
    runtime = _Runtime({"group_enable": True})
    out = ResponseAuthority(
        config_service=runtime.config_service, runtime=runtime
    ).explain(account_id="acc1", capability="agent", channel_id="111")

    assert out["allowed"] is True, "没给用户/身份时不能伪造失败"
    stages = {item["stage"]: item for item in out["checks"]}
    assert stages["identity"]["reason"] == "not_evaluated"
    assert stages["user_scope"]["reason"] == "not_evaluated"
    assert out["decided_by_label"] == ""


def test_explain_with_role_evaluates_identity():
    overrides = {("acc1", "agent", "111"): _override(permission={"role_min": "manager"})}
    runtime = _Runtime({"group_enable": True}, overrides=overrides)
    out = ResponseAuthority(config_service=runtime.config_service, runtime=runtime).explain(
        account_id="acc1", capability="agent", channel_id="111", role="member"
    )
    assert out["allowed"] is False
    assert out["decided_by"] == "identity"
    assert out["required_tier"] == "manager"
    assert out["hint"]


def test_explain_without_candidates_does_not_fake_a_denial():
    """预览时账号没连上（没有候选）不能报"响应权拦了"，否则用户会去改错的配置。"""

    class _OfflineResponder:
        def preview_capability(self, capability_id, channel_id):
            from app.plugin.responder import ResponderDecision

            return ResponderDecision(
                capability_id=capability_id,
                channel_id=channel_id,
                policy="delegate",
                candidates=(),
                responders=(),
                reason="no-candidates",
            )

    runtime = _Runtime({"group_enable": True})
    out = ResponseAuthority(
        config_service=runtime.config_service,
        runtime=runtime,
        responder=_OfflineResponder(),
    ).explain(account_id="acc1", capability="agent", channel_id="111")

    stages = {item["stage"]: item for item in out["checks"]}
    assert stages["responder"]["allowed"] is True
    assert stages["responder"]["reason"] == "not_evaluated"
    assert "没有在线候选" in stages["responder"]["detail"]
    assert out["responder"]["policy"] == "delegate"
