"""旧 system_prompt 自动迁移成角色预设：只在"确实是旧式人设"时发生一次。

## 背景（线上现象）

`GET /api/agent/config` 里有一段"兼容旧格式"迁移，原来只看"账号里有没有
system_prompt"。但后来 persona 改由角色预设维护，账号里的 `system_prompt` 只是
代码维护的兜底副本（应用人格/启动对齐都会写它），旧保存路径还会把内置默认值落成
显式键。于是**每次打开 AI 设置都会凭空多出一份「{bot_id}角色设定」**；用户删掉后
下次读配置又重建。

这里锁住收窄后的口径：
1. 绑定了人格预设 / 内容等于内置默认值 / 已迁移过 → 都不再创建；
2. 真正的旧式人设只迁移一次，删掉后不再出现；
3. 同名但用户自建的预设不被覆盖。
"""

from __future__ import annotations

from app.application.role_preset_service import RolePresetService


async def _legacy_service(config_service) -> RolePresetService:
    return RolePresetService(config_service)


async def _seed_agent(config_service, bot_id: str, **config) -> None:
    await config_service.save_module_config("agent", bot_id, config)


async def _names(config_service) -> list[str]:
    return [p["name"] for p in config_service.list_role_presets()]


async def test_bound_persona_never_creates_compat_preset(config_service):
    """账号已绑定人格预设：副本只是兜底快照，迁移它只会多一份重复。"""
    await config_service.save_role_preset("persona1", {
        "id": "persona1", "name": "群聊搭子", "system_prompt": "用户写的人设",
        "created_at": 1, "updated_at": 1,
    })
    await _seed_agent(
        config_service, "acc1",
        persona_preset_id="persona1", system_prompt="用户写的人设",
    )

    service = await _legacy_service(config_service)
    result = await service.ensure_legacy_prompt("acc1")

    assert result is None
    assert await _names(config_service) == ["群聊搭子"]


async def test_default_prompt_is_not_treated_as_legacy_persona(config_service):
    """旧保存路径会把内置默认值落成显式键——那不是用户写的人设。"""
    from app.agent.config import DEFAULT_LLM_CONFIG

    await _seed_agent(
        config_service, "acc1",
        system_prompt=DEFAULT_LLM_CONFIG["system_prompt"],
    )

    service = await _legacy_service(config_service)
    result = await service.ensure_legacy_prompt("acc1")

    assert result is None
    assert await _names(config_service) == []


async def test_genuine_legacy_prompt_migrates_exactly_once(config_service):
    """真正的旧式人设（未绑定预设、内容非默认）迁移成一份角色预设，重复调用不重复建。"""
    await _seed_agent(config_service, "acc1", system_prompt="我自己的老人设：说话简短。")

    service = await _legacy_service(config_service)
    created = await service.ensure_legacy_prompt("acc1")
    again = await service.ensure_legacy_prompt("acc1")

    assert created is not None
    assert created["name"] == "acc1角色设定"
    assert created["description"] == RolePresetService.LEGACY_PRESET_DESCRIPTION
    assert created["system_prompt"] == "我自己的老人设：说话简短。"
    assert again is None, "已写过迁移标记的账号不该再跑一次迁移"
    assert await _names(config_service) == ["acc1角色设定"]


async def test_deleted_compat_preset_is_not_recreated(config_service):
    """用户删掉迁移出来的预设后，读配置不能再把它长回来（一次性迁移标记）。"""
    await _seed_agent(config_service, "acc1", system_prompt="我自己的老人设：说话简短。")

    service = await _legacy_service(config_service)
    created = await service.ensure_legacy_prompt("acc1")
    assert created is not None
    await service.delete_preset(created["id"])

    # 账号配置里的 system_prompt 副本还在（它是删预设后的兜底来源）——不能因此重建预设
    result = await service.ensure_legacy_prompt("acc1")

    assert result is None
    assert await _names(config_service) == []


async def test_user_preset_with_same_name_is_not_overwritten(config_service):
    """同名但用户自建的预设不动，也不重复创建。"""
    await config_service.save_role_preset("mine", {
        "id": "mine", "name": "acc1角色设定", "description": "我自己的",
        "system_prompt": "用户自己写的人设", "created_at": 1, "updated_at": 1,
    })
    await _seed_agent(config_service, "acc1", system_prompt="旧的 system_prompt")

    service = await _legacy_service(config_service)
    result = await service.ensure_legacy_prompt("acc1")

    assert result is None
    preserved = config_service.get_role_preset("mine")
    assert preserved is not None
    assert preserved["system_prompt"] == "用户自己写的人设", "不许覆盖用户自建的同名预设"
    assert len(config_service.list_role_presets()) == 1


async def test_existing_compat_preset_is_updated_to_current_prompt(config_service):
    """已存在的迁移预设（内部描述）跟随旧人设内容更新，而不是再建一份。"""
    await config_service.save_role_preset("auto", {
        "id": "auto", "name": "acc1角色设定",
        "description": RolePresetService.LEGACY_PRESET_DESCRIPTION,
        "system_prompt": "旧内容", "created_at": 1, "updated_at": 1,
    })
    await _seed_agent(config_service, "acc1", system_prompt="新内容")

    service = await _legacy_service(config_service)
    updated = await service.ensure_legacy_prompt("acc1")

    assert updated is not None and updated["id"] == "auto"
    assert updated["system_prompt"] == "新内容"
    assert len(config_service.list_role_presets()) == 1
