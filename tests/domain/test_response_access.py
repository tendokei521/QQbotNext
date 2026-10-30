"""统一响应判定的纯函数契约。

只测对外语义（契约/关键边界/曾经坏过的回归），不测私有实现：
- 档位比较（含私聊降级、名册档位）；
- 黑白名单语义（空白名单 = 全拒、黑名单命中 = 拒）；
- 判定链合并（第一个不通过者决定 reason/decided_by；追加阶段会重算）；
- 群号解析（私聊 group_id=0 必须当"无群"，这是历史上私信整条不触发的坑）；
- 权限覆盖值的校验（坏值必须被拒绝，不能写进库再静默失效）。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.domain.response_access import (
    INBOUND_STAGES,
    PIPELINE_STAGES,
    REASON_GROUP_DISABLED,
    REASON_IDENTITY_INSUFFICIENT,
    REASON_OK,
    REASON_TRIGGER_NOT_MATCHED,
    REASON_USER_BLACKLISTED,
    REASON_USER_NOT_IN_WHITELIST,
    STAGE_IDENTITY,
    STAGE_SCENE,
    STAGE_TRIGGER,
    AccessCheck,
    account_check,
    build_decision,
    channel_of,
    group_scope_check,
    identity_check,
    scene_check,
    scope_reason,
    tier_allows,
    trigger_check,
    user_scope_check,
    validate_permission_values,
)

# ==================== 档位 ====================


def test_everyone_always_allowed():
    assert tier_allows("everyone", "member") is True
    assert identity_check("everyone", "member").allowed is True


def test_rank_order_roster_beats_group_role():
    assert tier_allows("group_admin", "group_admin") is True
    assert tier_allows("group_admin", "member") is False
    # 名册身份高于群角色：群主不满足 manager 要求，manager 满足群主要求
    assert tier_allows("manager", "group_owner") is False
    assert tier_allows("group_owner", "manager") is True
    assert tier_allows("owner", "manager") is False


def test_private_chat_downgrades_group_roles_only():
    """私聊没有群角色：群管理/群主档自动降级；名册档（manager/owner）不降级。"""
    assert tier_allows("group_admin", "member", is_private=True) is True
    assert tier_allows("owner", "member", is_private=True) is False
    assert tier_allows("manager", "member", is_private=True) is False


def test_unknown_tier_falls_back_to_member():
    assert tier_allows("中心管理员", "member") is True  # 非法要求 → member
    assert tier_allows("member", "未知身份") is True  # 非法实际 → member


# ==================== 黑白名单 ====================


def test_scope_reason_semantics():
    assert scope_reason("blacklist", True, kind="user") == "user_blacklisted"
    assert scope_reason("blacklist", False, kind="user") is None
    assert scope_reason("whitelist", True, kind="user") is None
    assert scope_reason("whitelist", False, kind="user") == "user_not_in_whitelist"
    assert scope_reason("", True, kind="user") is None  # 未设置 = 不限制


def test_empty_whitelist_denies_everyone():
    check = user_scope_check("whitelist", [], "42")
    assert check.allowed is False
    assert check.reason == REASON_USER_NOT_IN_WHITELIST


def test_blacklist_hit_is_denied_with_detail():
    check = user_scope_check("blacklist", ["42"], 42)
    assert check.allowed is False
    assert check.reason == REASON_USER_BLACKLISTED
    assert "user=42" in check.detail


def test_group_scope_check_uses_group_id():
    assert group_scope_check("whitelist", ["111"], "111").allowed is True
    assert group_scope_check("whitelist", ["111"], "222").allowed is False


# ==================== 判定链合并 ====================


def _decision(*checks: AccessCheck):
    return build_decision(account_id="acc1", capability="agent", channel_id="111", checks=checks)


def test_all_pass_marks_ok():
    decision = _decision(account_check(True), scene_check(True))
    assert decision.allowed is True
    assert decision.reason == REASON_OK
    assert decision.decided_by == ""


def test_first_denial_decides_reason_and_stage():
    decision = _decision(
        account_check(True),
        scene_check(False, source="channel", channel_id="111"),
        identity_check("owner", "member"),
    )
    assert decision.allowed is False
    assert decision.reason == REASON_GROUP_DISABLED
    assert decision.decided_by == STAGE_SCENE
    assert [item.stage for item in decision.denied] == [STAGE_SCENE, STAGE_IDENTITY]


def test_first_denial_can_be_filtered_by_owner_stage():
    """同一个决策被入站/流水线两处消费：各自只认自己那段。"""
    decision = _decision(
        scene_check(False, source="channel", channel_id="111"),
        identity_check("owner", "member"),
    )
    assert decision.first_denial(INBOUND_STAGES).reason == REASON_IDENTITY_INSUFFICIENT
    assert decision.first_denial(PIPELINE_STAGES).reason == REASON_GROUP_DISABLED
    assert STAGE_TRIGGER in PIPELINE_STAGES and STAGE_IDENTITY in INBOUND_STAGES


def test_with_checks_recomputes_verdict():
    decision = _decision(account_check(True))
    assert decision.allowed is True
    appended = decision.with_checks(trigger_check(False, mode="at"))
    assert appended.allowed is False
    assert appended.reason == REASON_TRIGGER_NOT_MATCHED
    assert appended.decided_by == STAGE_TRIGGER


def test_as_dict_has_labels_and_hint():
    decision = _decision(scene_check(False, source="channel", channel_id="111"))
    data = decision.as_dict()
    assert data["allowed"] is False
    assert data["decided_by_label"] == "场所开关"
    assert data["hint"], "每个原因码都必须能告诉用户去哪修"
    assert data["checks"][0]["label"]


# ==================== 群号解析 ====================


def test_private_event_group_id_zero_means_no_channel():
    """私聊事件的 group 对象存在但 group_id=0：必须当"无群"，否则私信整条不触发。"""
    event = SimpleNamespace(event_type="message_private", group=SimpleNamespace(group_id=0))
    assert channel_of(event) is None


def test_group_event_keeps_group_id():
    event = SimpleNamespace(event_type="message_group", group=SimpleNamespace(group_id=111))
    assert channel_of(event) == "111"


# ==================== 权限覆盖值校验 ====================


def test_validate_permission_values_rejects_bad_input():
    assert validate_permission_values({"role_min": "group_admin"}) == []
    assert validate_permission_values({"role_min": "管理员"}), "非法档位必须被拒绝，不能静默回落"
    assert validate_permission_values({"user_mode": "allow"}), "名单模式只允许 blacklist/whitelist"
    assert validate_permission_values({"user_list": ["42", ""]}), "名单不能含空项"
    assert validate_permission_values(None) == []
