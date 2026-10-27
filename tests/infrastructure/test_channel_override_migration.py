"""R4-1：旧账号级配置迁移到 channel_overrides，且行为逐条等价。

旧模型（全部账号级）：
- group_enable / private_enable（场景开关）
- group_mode + group_list（群白/黑名单）
- user_mode + user_list（用户白/黑名单）

新模型分三层：账号层（用户名单）、场景层（__group__/__private__）、群层（真实群号）。
迁移必须保证"迁移前不回的地方迁移后也不回"。
"""

from __future__ import annotations

from app.application.dialogue_policy_migration import migrate_legacy_dialog_policies
from app.application.dialogue_policy_service import DialoguePolicyService
from app.domain.dialogue_policy import SCENE_GROUP, SCENE_PRIVATE


async def _seed(container, config_service, agent_config: dict):
    await config_service.save_bots([
        {"id": "acc1", "name": "主号", "ws_url": "ws://127.0.0.1:3001", "auto_connect": False},
    ])
    await config_service.save_module_config("agent", "acc1", agent_config)


async def test_whitelist_migration_preserves_behavior(container, config_service):
    await _seed(container, config_service, {
        "group_enable": True,
        "private_enable": False,
        "group_mode": "whitelist",
        "group_list": ["111", "222"],
        "user_mode": "blacklist",
        "user_list": ["999"],
    })

    assert await migrate_legacy_dialog_policies(config_service) is True

    # 场景层：白名单模式下群聊默认关；私聊按旧 private_enable
    assert config_service.get_channel_override("acc1", "agent", SCENE_GROUP)["enabled"] is False
    assert config_service.get_channel_override("acc1", "agent", SCENE_PRIVATE)["enabled"] is False
    # 群层：列出的群开，未列出的没有覆盖（继承场景层的 False）
    assert config_service.get_channel_override("acc1", "agent", "111")["enabled"] is True
    assert config_service.get_channel_override("acc1", "agent", "222")["enabled"] is True
    assert config_service.get_channel_override("acc1", "agent", "333") is None
    # 账号层：用户名单
    account = config_service.get_channel_override("acc1", "agent", "")
    assert account["permission"] == {"user_mode": "blacklist", "user_list": ["999"]}

    policy = DialoguePolicyService(config_service)
    assert policy.resolve("acc1", "agent", "111").enabled is True
    assert policy.resolve("acc1", "agent", "333").enabled is False, "白名单外的群必须不回"
    assert policy.resolve("acc1", "agent", None).enabled is False, "私聊按旧配置关闭"

    assert await migrate_legacy_dialog_policies(config_service) is False, "迁移必须幂等"


async def test_blacklist_migration_preserves_behavior(container, config_service):
    await _seed(container, config_service, {
        "group_enable": True,
        "private_enable": True,
        "group_mode": "blacklist",
        "group_list": ["111"],
        "user_mode": "blacklist",
        "user_list": [],
    })

    assert await migrate_legacy_dialog_policies(config_service) is True

    assert config_service.get_channel_override("acc1", "agent", SCENE_GROUP)["enabled"] is True
    assert config_service.get_channel_override("acc1", "agent", "111")["enabled"] is False
    assert config_service.get_channel_override("acc1", "agent", "222") is None

    policy = DialoguePolicyService(config_service)
    assert policy.resolve("acc1", "agent", "111").enabled is False
    assert policy.resolve("acc1", "agent", "222").enabled is True
    assert policy.resolve("acc1", "agent", None).enabled is True


async def test_group_disabled_keeps_every_group_off(container, config_service):
    await _seed(container, config_service, {
        "group_enable": False,
        "private_enable": True,
        "group_mode": "blacklist",
        "group_list": ["111"],
    })

    assert await migrate_legacy_dialog_policies(config_service) is True

    policy = DialoguePolicyService(config_service)
    assert policy.resolve("acc1", "agent", "111").enabled is False
    assert policy.resolve("acc1", "agent", "222").enabled is False, "旧 group_enable=False 时所有群都不回"


async def test_migration_waits_until_an_account_exists(container, config_service):
    """没有账号时不写迁移标记：新装用户之后建号也能被迁移。"""
    assert await migrate_legacy_dialog_policies(config_service) is False

    await _seed(container, config_service, {"group_enable": True, "private_enable": True})
    assert await migrate_legacy_dialog_policies(config_service) is True
    assert config_service.get_channel_override("acc1", "agent", SCENE_GROUP)["enabled"] is True
