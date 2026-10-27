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
    assert resolved.config == {"trigger_at": True}
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
    assert resolved.config == {"trigger_at": True, "reply_cooldown": 10}
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
