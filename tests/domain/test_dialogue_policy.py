"""对话策略解析（纯函数）：三层合并 + 白名单 + 来源标注。

这是「简单视图/高级视图」共用的唯一真相：两层 UI 都只是它的不同投影，
所以这里把边界钉死——否则 UI 的"继承自哪里"会与运行时不一致。
"""

from __future__ import annotations

from app.domain.dialogue_policy import (
    SOURCE_ACCOUNT,
    SOURCE_CHANNEL,
    SOURCE_DEFAULT,
    is_overridable,
    overridable_keys,
    resolve_policy,
)


def test_defaults_are_filtered_to_whitelist():
    resolved = resolve_policy(
        "agent",
        account_id="acc1",
        defaults={"trigger_at": True, "max_tokens": 4096, "not_a_key": 1},
        default_enabled=True,
    )
    assert resolved.enabled is True
    assert resolved.enabled_source == SOURCE_DEFAULT
    assert resolved.config["trigger_at"] is True
    # 派生字段：解析结果永远给出显式 trigger_mode / 概率，UI 与运行时都只读它
    assert resolved.config["trigger_mode"] == "at"
    assert resolved.config["trigger_probability"] == 100
    assert "max_tokens" not in resolved.config, "白名单外的键不该进入解析结果"


def test_account_then_channel_override_and_sources():
    resolved = resolve_policy(
        "agent",
        account_id="acc1",
        channel_id="111",
        defaults={"trigger_at": False, "reply_cooldown": 3},
        default_enabled=False,
        account_row={"enabled": True, "config": {"trigger_at": True}, "permission": {}},
        channel_row={"enabled": False, "config": {"reply_cooldown": 10}, "permission": {"role_min": "manager"}},
    )
    assert resolved.enabled is False
    assert resolved.enabled_source == SOURCE_CHANNEL
    assert resolved.config["trigger_at"] is True
    assert resolved.config["reply_cooldown"] == 10
    assert resolved.config["trigger_mode"] == "at", "旧键 trigger_at 要折算成显式 mode"
    assert resolved.source_of("trigger_mode") == SOURCE_ACCOUNT
    assert resolved.source_of("trigger_at") == SOURCE_ACCOUNT
    assert resolved.source_of("reply_cooldown") == SOURCE_CHANNEL
    assert resolved.permission == {"role_min": "manager"}
    assert resolved.source_of("role_min") == SOURCE_CHANNEL


def test_none_enabled_means_inherit():
    resolved = resolve_policy(
        "agent",
        account_id="acc1",
        defaults={"trigger_at": False},
        default_enabled=True,
        account_row={"enabled": None},
        channel_row={"enabled": None},
    )
    assert resolved.enabled is True
    assert resolved.enabled_source == SOURCE_DEFAULT


def test_channel_row_can_override_account_row_back():
    resolved = resolve_policy(
        "agent",
        account_id="acc1",
        channel_id="111",
        defaults={"trigger_at": False},
        default_enabled=True,
        account_row={"config": {"trigger_at": True}},
        channel_row={"config": {"trigger_at": False}},
    )
    assert resolved.config["trigger_at"] is False
    assert resolved.source_of("trigger_at") == SOURCE_CHANNEL


def test_permission_keys_are_limited():
    resolved = resolve_policy(
        "agent",
        account_id="acc1",
        channel_id="111",
        defaults={},
        account_row={"permission": {"role_min": "member", "unknown": "x"}},
    )
    assert resolved.permission == {"role_min": "member"}
    assert "unknown" not in resolved.permission


def test_overridable_whitelist_is_explicit():
    keys = overridable_keys("agent")
    for key in ("trigger_at", "trigger_keyword", "reply_cooldown", "interrupt_enable"):
        assert key in keys, f"{key} 应允许按群覆盖"
    for key in ("max_tokens", "temperature", "api_base"):
        assert not is_overridable("agent", key), f"{key} 不该允许按群覆盖"
    assert overridable_keys("unknown_capability") == ()


def test_empty_channel_id_is_treated_as_none():
    resolved = resolve_policy("agent", account_id="acc1", channel_id="", defaults={})
    assert resolved.channel_id is None


# ==================== 免打扰时段 ====================


def test_quiet_hours_validation():
    from app.domain.dialogue_policy import valid_quiet_hours

    assert valid_quiet_hours("") is True, "空串 = 不启用"
    assert valid_quiet_hours("23:00-08:00") is True
    assert valid_quiet_hours("00:00-23:59") is True
    assert valid_quiet_hours("24:00-08:00") is False
    assert valid_quiet_hours("23:60-08:00") is False
    assert valid_quiet_hours("晚上-早上") is False
    assert valid_quiet_hours("23:00~08:00") is False


def test_quiet_hours_same_day_and_cross_midnight():
    from app.domain.dialogue_policy import in_quiet_hours

    # 同日窗口 09:00-12:00
    assert in_quiet_hours("09:00-12:00", now_minutes=10 * 60) is True
    assert in_quiet_hours("09:00-12:00", now_minutes=12 * 60) is False, "右开区间"
    assert in_quiet_hours("09:00-12:00", now_minutes=8 * 60) is False

    # 跨零点 23:00-08:00
    assert in_quiet_hours("23:00-08:00", now_minutes=23 * 60) is True
    assert in_quiet_hours("23:00-08:00", now_minutes=2 * 60) is True
    assert in_quiet_hours("23:00-08:00", now_minutes=8 * 60) is False
    assert in_quiet_hours("23:00-08:00", now_minutes=12 * 60) is False


def test_quiet_hours_invalid_or_empty_never_silences():
    from app.domain.dialogue_policy import in_quiet_hours

    assert in_quiet_hours("", now_minutes=10 * 60) is False
    assert in_quiet_hours("写错了", now_minutes=10 * 60) is False, "配置写错不该导致永久沉默"
    assert in_quiet_hours("10:00-10:00", now_minutes=10 * 60) is False, "空窗口不静默"


def test_validate_override_values_rejects_bad_values():
    from app.domain.dialogue_policy import validate_override_values

    assert validate_override_values({"trigger_at": True, "quiet_hours": "23:00-08:00"}) == []
    errors = validate_override_values({
        "trigger_at": "yes",
        "trigger_keyword": [""],
        "reply_cooldown": -1,
        "quiet_hours": "晚上",
    })
    assert len(errors) == 4, errors


# ==================== 触发方式（trigger_mode） ====================


def test_trigger_mode_is_derived_from_legacy_keys():
    from app.domain.dialogue_policy import trigger_mode_from

    assert trigger_mode_from({"trigger_at": True, "trigger_keyword": []}) == "at"
    assert trigger_mode_from({"trigger_at": False, "trigger_keyword": ["AI"]}) == "keyword"
    assert trigger_mode_from({"trigger_at": True, "trigger_keyword": ["AI"]}) == "both"
    assert trigger_mode_from({"trigger_at": False, "trigger_keyword": []}) == "none"
    # 没配置过触发键（新账号默认配置为空）→ 产品默认「只回 @」
    assert trigger_mode_from({}) == "at"


def test_explicit_trigger_mode_wins_over_legacy_keys():
    from app.domain.dialogue_policy import trigger_mode_from

    assert trigger_mode_from({"trigger_mode": "all", "trigger_at": False}) == "all"
    assert trigger_mode_from({"trigger_mode": "  NONE  ", "trigger_at": True}) == "none"
    # 非法显式值 → 退回旧键折算（不会变成"半生效"）
    assert trigger_mode_from({"trigger_mode": "whatever", "trigger_at": True}) == "at"


def test_trigger_probability_is_clamped_and_defaulted():
    from app.domain.dialogue_policy import trigger_probability_of

    assert trigger_probability_of({}) == 100
    assert trigger_probability_of({"trigger_probability": 0}) == 0
    assert trigger_probability_of({"trigger_probability": 250}) == 100
    assert trigger_probability_of({"trigger_probability": -5}) == 0
    assert trigger_probability_of({"trigger_probability": "abc"}) == 100


def test_legacy_trigger_keys_dual_write():
    from app.domain.dialogue_policy import legacy_trigger_keys

    assert legacy_trigger_keys("at") == {"trigger_at": True}
    assert legacy_trigger_keys("both") == {"trigger_at": True}
    assert legacy_trigger_keys("all") == {"trigger_at": True}
    assert legacy_trigger_keys("keyword") == {"trigger_at": False}
    assert legacy_trigger_keys("none") == {"trigger_at": False}


def test_override_values_validate_trigger_keys():
    from app.domain.dialogue_policy import validate_override_values

    assert validate_override_values({"trigger_mode": "all", "trigger_probability": 30}) == []
    assert validate_override_values({"trigger_mode": "sometimes"}), "非法枚举必须报错"
    assert validate_override_values({"trigger_probability": 101}), "概率越界必须报错"
    assert validate_override_values({"trigger_probability": True}), "bool 不是合法概率"


def test_trigger_mode_override_source_is_channel():
    resolved = resolve_policy(
        "agent",
        account_id="acc1",
        channel_id="111",
        defaults={"trigger_at": True},
        account_row={"config": {}},
        channel_row={"config": {"trigger_mode": "keyword", "trigger_keyword": ["摸鱼"]}},
    )
    assert resolved.config["trigger_mode"] == "keyword"
    assert resolved.source_of("trigger_mode") == SOURCE_CHANNEL


def test_decide_trigger_all_modes():
    from app.domain.dialogue_policy import decide_trigger

    base = {"at_matched": False, "keyword_matched": False, "has_text": True}
    assert decide_trigger("at", 100, **{**base, "at_matched": True}) is True
    assert decide_trigger("at", 100, **base) is False
    assert decide_trigger("keyword", 100, **{**base, "keyword_matched": True}) is True
    assert decide_trigger("both", 100, **{**base, "keyword_matched": True}) is True
    assert decide_trigger("none", 100, **{**base, "at_matched": True}) is False
    assert decide_trigger("all", 100, **base) is True
    assert decide_trigger("all", 0, **base) is False
    # 概率抽签可注入，0/100 之外也能确定性测试
    assert decide_trigger("all", 30, **base, rng=lambda: 0.1) is True
    assert decide_trigger("all", 30, **base, rng=lambda: 0.9) is False
    # 没有正文一律不触发（纯图片由调用方放行）
    assert decide_trigger("all", 100, **{**base, "has_text": False}) is False
