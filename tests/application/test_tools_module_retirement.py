"""「工具扩展」模块退役迁移的契约测试。

锁四件事：

1. 旧模块启用 + 旧模块内总开关开 → 新 agent 键为 True；
2. 旧模块被禁用（或模块内总开关关）→ 新 agent 键为 False（保留用户的选择）；
3. 搬完删掉旧模块的 binding 行，并写 kv 标记（只跑一次，之后用户改的值不被覆盖）；
4. agent 里原来的同名键只是"整份默认值回写"的残渣，必须被有效值覆盖。
"""

from __future__ import annotations

import pytest

from app.application import tools_module_retirement as retirement
from app.infrastructure.config.config_service import ConfigService


async def _seed_account(config_service: ConfigService, bot_id: str) -> None:
    await config_service.save_bots([
        {"id": bot_id, "name": bot_id, "ws_url": "ws://127.0.0.1:4001/", "conn_mode": "reverse"},
    ])


async def _set_legacy(
    config_service: ConfigService,
    bot_id: str,
    *,
    enabled: bool,
    module_switch: bool | None = None,
) -> None:
    """造一条旧「工具扩展」模块的 account 级 binding。"""
    await config_service.save_binding(
        retirement.RETIRED_MODULE,
        "account",
        bot_id,
        enabled=enabled,
        config={} if module_switch is None else {retirement.TARGET_KEY: module_switch},
    )


def _agent_cfg(config_service: ConfigService, bot_id: str) -> dict:
    return config_service.get_module_config("agent", bot_id)


@pytest.fixture(autouse=True)
async def _clean(config_service):
    """每个用例前清掉 kv 标记（迁移是全局一次性的）。"""
    await config_service.set_kv(retirement.MIGRATION_FLAG, "")
    yield


async def test_enabled_legacy_module_turns_tools_on(config_service):
    await _seed_account(config_service, "acc1")
    await _set_legacy(config_service, "acc1", enabled=True)

    report = await retirement.migrate_tools_module_config(config_service)

    assert report["accounts"] == 1 and report["enabled"] == 1
    assert _agent_cfg(config_service, "acc1")[retirement.TARGET_KEY] is True


async def test_disabled_legacy_module_keeps_tools_off(config_service):
    await _seed_account(config_service, "acc1")
    await _set_legacy(config_service, "acc1", enabled=False)

    await retirement.migrate_tools_module_config(config_service)

    assert _agent_cfg(config_service, "acc1")[retirement.TARGET_KEY] is False


async def test_module_switch_off_wins_over_module_enabled(config_service):
    await _seed_account(config_service, "acc1")
    await _set_legacy(config_service, "acc1", enabled=True, module_switch=False)

    await retirement.migrate_tools_module_config(config_service)

    assert _agent_cfg(config_service, "acc1")[retirement.TARGET_KEY] is False


async def test_old_binding_rows_are_removed_and_migration_runs_once(config_service):
    await _seed_account(config_service, "acc1")
    await _set_legacy(config_service, "acc1", enabled=True)

    await retirement.migrate_tools_module_config(config_service)

    assert config_service.list_bindings(retirement.RETIRED_MODULE) == []
    assert await config_service.get_kv(retirement.MIGRATION_FLAG, "") == "1"

    # 用户在「高级选项」关掉工具后重启：迁移标记阻止再次覆盖
    config_service.set_module_config("agent", "acc1", {
        **_agent_cfg(config_service, "acc1"),
        retirement.TARGET_KEY: False,
    })
    again = await retirement.migrate_tools_module_config(config_service)
    assert again["accounts"] == 0
    assert _agent_cfg(config_service, "acc1")[retirement.TARGET_KEY] is False


async def test_artifact_value_is_overwritten_by_effective_legacy_value(config_service):
    """agent 里早存下的同名键（旧默认 false）不能压过"旧模块其实开着"。"""
    await _seed_account(config_service, "acc1")
    config_service.set_module_config("agent", "acc1", {retirement.TARGET_KEY: False})
    await _set_legacy(config_service, "acc1", enabled=True)

    await retirement.migrate_tools_module_config(config_service)

    assert _agent_cfg(config_service, "acc1")[retirement.TARGET_KEY] is True
