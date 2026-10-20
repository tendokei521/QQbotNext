"""Provider 预设独立管理测试。"""

import pytest

from app.infrastructure.config.config_service import ConfigService
from app.services.provider_preset_service import PASSWORD_MASK, ProviderPresetService


@pytest.fixture
async def preset_service(config_service):
    return ProviderPresetService(config_service)


async def test_create_list_update_preset(preset_service):
    created = await preset_service.create_preset({
        "name": "DeepSeek 测试",
        "provider": "openai",
        "config": {"api_base": "https://api.deepseek.com/", "api_key": "sk-secret", "retry_attempts": 5},
    })
    assert created["id"]
    assert created["config"]["api_base"] == "https://api.deepseek.com"  # 尾部斜杠清洗
    assert created["config"]["api_key"] == "sk-secret"

    listed = preset_service.list_presets()
    assert len(listed) == 1
    assert listed[0]["config"]["api_key"] == "sk-secret"

    raw = preset_service.get_preset(created["id"], masked=False)
    assert raw["config"]["api_key"] == "sk-secret"

    updated = await preset_service.update_preset(created["id"], {
        "name": "DeepSeek 改名",
        "config": {"api_key": PASSWORD_MASK},
    })
    assert updated["name"] == "DeepSeek 改名"
    assert preset_service.get_preset(created["id"], masked=False)["config"]["api_key"] == "sk-secret"


async def test_delete_preset_clears_references(preset_service, config_service):
    created = await preset_service.create_preset({
        "name": "被引用",
        "provider": "openai",
        "config": {"api_base": "https://api.deepseek.com", "api_key": "sk-ref"},
    })
    await config_service.save_module_config("agent", 100, {
        "provider_preset_id": created["id"],
    })

    await preset_service.delete_preset(created["id"])

    assert preset_service.get_preset(created["id"]) is None
    cfg = config_service.get_module_config("agent", 100)
    assert "provider_preset_id" not in cfg


async def test_migrate_legacy_agent_config(preset_service, config_service):
    await config_service.save_module_config("agent", 7, {
        "api_key": "sk-legacy",
        "api_base": "https://api.deepseek.com/",
        "provider": "openai",
        "model": "deepseek-chat",
        "retry_attempts": 4,
    })
    migrated = await preset_service.migrate_legacy_agent_configs()
    assert migrated == 1

    cfg = config_service.get_module_config("agent", 7)
    assert cfg.get("provider_preset_id")
    assert "api_key" not in cfg
    assert "api_base" not in cfg

    preset = preset_service.get_preset(cfg["provider_preset_id"], masked=False)
    assert preset["config"]["api_key"] == "sk-legacy"
    assert preset["config"]["api_base"] == "https://api.deepseek.com"

    # 幂等：再次迁移不应重复创建
    assert await preset_service.migrate_legacy_agent_configs() == 0