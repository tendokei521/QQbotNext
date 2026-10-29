"""存量账号的提示词方案迁移：空/拼错 → chat（幂等）。"""

from __future__ import annotations

from app.application.agent_prompt_preset_migration import migrate_prompt_preset


async def test_empty_or_unknown_preset_falls_back_to_chat(container, config_service):
    await config_service.save_bots([
        {"id": "a", "name": "空", "ws_url": "ws://127.0.0.1:4001/"},
        {"id": "b", "name": "拼错", "ws_url": "ws://127.0.0.1:4002/"},
        {"id": "c", "name": "已选", "ws_url": "ws://127.0.0.1:4003/"},
    ])
    await config_service.save_module_config("agent", "a", {"prompt_preset": ""})
    await config_service.save_module_config("agent", "b", {"prompt_preset": "role_play"})
    await config_service.save_module_config("agent", "c", {"prompt_preset": "assistant"})

    updated = await migrate_prompt_preset(config_service)

    assert updated == 2
    assert config_service.get_module_config("agent", "a")["prompt_preset"] == "chat"
    assert config_service.get_module_config("agent", "b")["prompt_preset"] == "chat"
    assert config_service.get_module_config("agent", "c")["prompt_preset"] == "assistant"


async def test_migration_is_idempotent(container, config_service):
    await config_service.save_bots([{"id": "a", "name": "空", "ws_url": "ws://127.0.0.1:4001/"}])
    await config_service.save_module_config("agent", "a", {"prompt_preset": ""})

    first = await migrate_prompt_preset(config_service)
    second = await migrate_prompt_preset(config_service)

    assert first == 1
    assert second == 0
