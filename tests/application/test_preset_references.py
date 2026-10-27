"""P4-5：删除预设时清理引用（真实消费点是 `provider_model_pool`）。

历史坑：`list_references` 只查 `provider_model_id`，而运行期真正消费的是
`provider_model_pool`；删预设只清前者 → 池里留死 id，运行期静默降级。
角色预设删除则完全不清 `persona_preset_id`（组装静默回退 system_prompt）。
"""

from __future__ import annotations

from app.application.provider_model_service import ProviderModelService
from app.application.provider_preset_service import ProviderPresetService
from app.application.role_preset_service import RolePresetService


async def _seed_agent(container, config_service, **config):
    await config_service.save_bots([
        {"id": "acc1", "name": "主号", "ws_url": "ws://127.0.0.1:3001"},
    ])
    await config_service.save_module_config("agent", "acc1", config)


async def test_list_references_covers_model_pool(container, config_service):
    await config_service.save_provider_preset("pp1", {
        "id": "pp1", "name": "P", "provider": "openai",
        "config": {"api_base": "https://x/v1", "api_key": ""}, "enabled": True,
        "created_at": 1, "updated_at": 1,
    })
    await config_service.save_provider_model("pm1", {
        "id": "pm1", "preset_id": "pp1", "model": "m", "enabled": True,
        "created_at": 1, "updated_at": 1,
    })
    await _seed_agent(container, config_service, provider_model_pool=["pm1"])

    refs = container.get(ProviderModelService).list_references("pm1")
    assert ("acc1", "pm1") in refs, "池里的引用必须算引用"


async def test_delete_preset_cleans_model_pool(container, config_service):
    await config_service.save_provider_preset("pp1", {
        "id": "pp1", "name": "P", "provider": "openai",
        "config": {"api_base": "https://x/v1", "api_key": ""}, "enabled": True,
        "created_at": 1, "updated_at": 1,
    })
    await config_service.save_provider_model("pm1", {
        "id": "pm1", "preset_id": "pp1", "model": "m", "enabled": True,
        "created_at": 1, "updated_at": 1,
    })
    await _seed_agent(container, config_service, provider_model_pool=["pm1", "pm-other"])

    await container.get(ProviderPresetService).delete_preset("pp1")

    config = config_service.get_module_config("agent", "acc1")
    assert config["provider_model_pool"] == ["pm-other"], "删预设后池里不能留死 id"


async def test_delete_role_preset_clears_persona_reference(container, config_service):
    await config_service.save_role_preset("role1", {
        "id": "role1", "name": "群聊搭子", "system_prompt": "人设",
        "created_at": 1, "updated_at": 1,
    })
    await _seed_agent(container, config_service, persona_preset_id="role1")

    await container.get(RolePresetService).delete_preset("role1")

    assert "persona_preset_id" not in config_service.get_module_config("agent", "acc1")
