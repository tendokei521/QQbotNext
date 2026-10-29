"""触发方式迁移：旧账号的 trigger_at/keyword 复制成账号级覆盖（幂等）。"""

from __future__ import annotations

from app.application.dialogue_policy_migration import (
    TRIGGER_MIGRATION_FLAG,
    migrate_trigger_overrides,
)


async def test_legacy_trigger_keys_become_account_override(container, config_service):
    await config_service.save_bots([
        {"id": "acc1", "name": "主号", "ws_url": "ws://127.0.0.1:4001/"},
    ])
    await config_service.save_module_config("agent", "acc1", {
        "trigger_at": True, "trigger_keyword": ["AI", "助手"],
    })

    migrated = await migrate_trigger_overrides(config_service)

    assert migrated == 1
    row = config_service.get_channel_override("acc1", "agent", "")
    assert row is not None
    assert row["config"]["trigger_mode"] == "both"
    assert row["config"]["trigger_at"] is True
    assert row["config"]["trigger_keyword"] == ["AI", "助手"]
    assert row["config"]["trigger_probability"] == 100


async def test_explicit_trigger_mode_is_preserved(container, config_service):
    await config_service.save_bots([
        {"id": "acc1", "name": "主号", "ws_url": "ws://127.0.0.1:4001/"},
    ])
    await config_service.save_module_config("agent", "acc1", {
        "trigger_mode": "all", "trigger_probability": 30,
    })

    await migrate_trigger_overrides(config_service)

    row = config_service.get_channel_override("acc1", "agent", "")
    assert row["config"]["trigger_mode"] == "all"
    assert row["config"]["trigger_probability"] == 30


async def test_migration_is_idempotent(container, config_service):
    await config_service.save_bots([
        {"id": "acc1", "name": "主号", "ws_url": "ws://127.0.0.1:4001/"},
    ])
    await config_service.save_module_config("agent", "acc1", {"trigger_at": False})

    first = await migrate_trigger_overrides(config_service)
    second = await migrate_trigger_overrides(config_service)

    assert first == 1
    assert second == 0
    assert await config_service.get_kv(TRIGGER_MIGRATION_FLAG, "") == "1"
