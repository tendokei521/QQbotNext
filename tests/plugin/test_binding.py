"""作用域绑定解析：global → account → channel，最具体者胜。"""

from __future__ import annotations

import pytest

from app.plugin.binding import (
    SCOPE_ACCOUNT,
    SCOPE_CHANNEL,
    SCOPE_GLOBAL,
    SOURCE_DEFAULT,
    Binding,
    BindingResolver,
)


def _resolver(*bindings, default_enabled=True):
    def provider(capability_id):
        return [b for b in bindings if b.capability_id == capability_id]

    return BindingResolver(provider, default_enabled=default_enabled)


def test_no_binding_uses_defaults():
    resolved = _resolver().resolve("demo", account_id="acc1", default_config={"a": 1})

    assert resolved.enabled is True
    assert resolved.enabled_source == SOURCE_DEFAULT
    assert resolved.config == {"a": 1}
    assert resolved.source_of("a") == SOURCE_DEFAULT


def test_global_binding_applies_to_every_account():
    resolver = _resolver(Binding("demo", SCOPE_GLOBAL, enabled=False, config={"a": 1}))

    for account in ("acc1", "acc2"):
        resolved = resolver.resolve("demo", account_id=account)
        assert resolved.enabled is False
        assert resolved.enabled_source == SCOPE_GLOBAL
        assert resolved.config == {"a": 1}


def test_account_overrides_global_per_key():
    resolver = _resolver(
        Binding("demo", SCOPE_GLOBAL, enabled=True, config={"a": 1, "b": 2}),
        Binding("demo", SCOPE_ACCOUNT, "acc1", enabled=False, config={"b": 20}),
    )

    acc1 = resolver.resolve("demo", account_id="acc1")
    acc2 = resolver.resolve("demo", account_id="acc2")

    assert acc1.enabled is False and acc1.enabled_source == SCOPE_ACCOUNT
    assert acc1.config == {"a": 1, "b": 20}
    assert acc1.source_of("a") == SCOPE_GLOBAL
    assert acc1.source_of("b") == SCOPE_ACCOUNT

    assert acc2.enabled is True and acc2.enabled_source == SCOPE_GLOBAL
    assert acc2.config == {"a": 1, "b": 2}


def test_channel_overrides_account():
    resolver = _resolver(
        Binding("demo", SCOPE_GLOBAL, enabled=True),
        Binding("demo", SCOPE_ACCOUNT, "acc1", config={"x": 1}),
        Binding("demo", SCOPE_CHANNEL, "g1", enabled=False, config={"x": 9}),
    )

    resolved = resolver.resolve("demo", account_id="acc1", channel_id="g1")

    assert resolved.enabled is False
    assert resolved.enabled_source == SCOPE_CHANNEL
    assert resolved.config == {"x": 9}
    assert resolved.source_of("x") == SCOPE_CHANNEL


def test_unrelated_account_or_channel_is_ignored():
    resolver = _resolver(
        Binding("demo", SCOPE_ACCOUNT, "acc2", enabled=False),
        Binding("demo", SCOPE_CHANNEL, "g9", enabled=False),
    )

    resolved = resolver.resolve("demo", account_id="acc1", channel_id="g1")

    assert resolved.enabled is True
    assert resolved.enabled_source == SOURCE_DEFAULT


def test_deeper_scope_without_enabled_keeps_inherited_value():
    """深层只覆盖配置键时，enabled 继续沿用宽作用域，而不是被重置成默认值。"""
    resolver = _resolver(
        Binding("demo", SCOPE_ACCOUNT, "acc1", enabled=False, config={"a": 1}),
        Binding("demo", SCOPE_CHANNEL, "g1", enabled=None, config={"a": 2}),
    )

    resolved = resolver.resolve("demo", account_id="acc1", channel_id="g1")

    assert resolved.enabled is False
    assert resolved.enabled_source == SCOPE_ACCOUNT
    assert resolved.config == {"a": 2}


def test_permission_merges_by_scope():
    resolver = _resolver(
        Binding("demo", SCOPE_GLOBAL, permission={"user_mode": "blacklist", "user_list": ["1"]}),
        Binding("demo", SCOPE_ACCOUNT, "acc1", permission={"user_list": ["2"]}),
    )

    assert resolver.permission("demo", account_id="acc1") == {
        "user_mode": "blacklist",
        "user_list": ["2"],
    }
    assert resolver.permission("demo", account_id="acc2") == {
        "user_mode": "blacklist",
        "user_list": ["1"],
    }


def test_account_id_none_skips_account_scope():
    resolver = _resolver(Binding("demo", SCOPE_ACCOUNT, "None", enabled=False))

    resolved = resolver.resolve("demo")

    assert resolved.enabled is True
    assert resolved.enabled_source == SOURCE_DEFAULT


def test_capabilities_are_isolated():
    resolver = _resolver(
        Binding("a", SCOPE_GLOBAL, enabled=False),
        Binding("b", SCOPE_GLOBAL, enabled=True),
    )

    assert resolver.enabled("a", account_id="x") is False
    assert resolver.enabled("b", account_id="x") is True


def test_unknown_scope_rejected():
    with pytest.raises(ValueError):
        Binding("demo", "tenant")


def test_default_enabled_is_configurable():
    resolver = _resolver(default_enabled=False)

    assert resolver.enabled("demo", account_id="acc1") is False
