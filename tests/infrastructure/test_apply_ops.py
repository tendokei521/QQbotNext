"""P3-1：`ConfigService.apply_ops`——套装应用/回滚的原子写入底座。

现状问题：`save_*` 每个对象独立落库；应用一套配置要改"预设/模型/绑定/模块开关"，
中途失败会留半套。这里锁定三条：①全部成功才可见；②失败回滚内存并抛错；
③删除 op 也走同一事务。
"""

from __future__ import annotations

import pytest

from app.infrastructure.config.config_service import ConfigOp


async def test_apply_ops_writes_everything_atomically(container, config_service, db):
    ops = [
        ConfigOp("role_preset", ("role-1",), {
            "name": "群聊搭子", "description": "", "system_prompt": "你是一个群友。",
            "created_at": 1, "updated_at": 1,
        }),
        ConfigOp("provider_preset", ("preset-1",), {
            "name": "DeepSeek", "provider": "openai",
            "config": {"api_base": "https://api.deepseek.com/v1", "api_key": "sk-x"},
            "enabled": True, "created_at": 1, "updated_at": 1,
        }),
        ConfigOp("provider_model", ("model-1",), {
            "preset_id": "preset-1", "model": "deepseek-chat", "enabled": True,
            "created_at": 1, "updated_at": 1,
        }),
        ConfigOp("binding", ("agent", "account", "acc1"), {
            "enabled": True,
            "config": {"persona_preset_id": "role-1", "provider_model_pool": ["model-1"]},
            "permission": {},
            "priority": 100,
        }),
        ConfigOp("channel_override", ("acc1", "agent", "111"), {
            "enabled": False, "config": {}, "permission": {}, "muted_until": 0,
        }),
    ]

    await config_service.apply_ops(ops)

    assert config_service.get_role_preset("role-1")["name"] == "群聊搭子"
    assert config_service.get_provider_preset("preset-1")["provider"] == "openai"
    assert config_service.get_provider_model("model-1")["model"] == "deepseek-chat"
    assert config_service.get_module_config("agent", "acc1")["persona_preset_id"] == "role-1"
    assert config_service.get_channel_override("acc1", "agent", "111")["enabled"] is False

    # 落库（不只是内存）
    assert await db.fetchone("SELECT id FROM role_presets WHERE id = ?", ("role-1",))
    assert await db.fetchone("SELECT id FROM provider_models WHERE id = ?", ("model-1",))
    assert await db.fetchone(
        "SELECT capability_id FROM bindings WHERE capability_id = ? AND scope_type = ? AND scope_id = ?",
        ("agent", "account", "acc1"),
    )
    assert await db.fetchone(
        "SELECT account_id FROM channel_overrides WHERE account_id = ? AND channel_id = ?",
        ("acc1", "111"),
    )


async def test_apply_ops_rolls_back_memory_on_failure(container, config_service, monkeypatch):
    from app.infrastructure.persistence.database import Database

    db = container.get(Database)

    async def _boom(_sqls):
        raise RuntimeError("磁盘满了")

    monkeypatch.setattr(db, "run_in_transaction", _boom)

    with pytest.raises(RuntimeError, match="磁盘满了"):
        await config_service.apply_ops([
            ConfigOp("role_preset", ("role-x",), {
                "name": "临时", "system_prompt": "x", "created_at": 1, "updated_at": 1,
            }),
            ConfigOp("binding", ("agent", "account", "acc1"), {
                "enabled": True, "config": {"group_enable": True}, "permission": {},
            }),
        ])

    # 内存必须回到应用前（不能留半套配置）
    assert config_service.get_role_preset("role-x") is None
    assert config_service.get_module_config("agent", "acc1") == {}


async def test_apply_ops_deletes_rows(container, config_service, db):
    await config_service.save_role_preset("role-del", {
        "id": "role-del", "name": "删掉", "system_prompt": "x", "created_at": 1, "updated_at": 1,
    })

    await config_service.apply_ops([ConfigOp("role_preset", ("role-del",), None)])

    assert config_service.get_role_preset("role-del") is None
    assert await db.fetchone("SELECT id FROM role_presets WHERE id = ?", ("role-del",)) is None


async def test_apply_ops_rejects_unknown_kind(container, config_service):
    with pytest.raises(ValueError, match="未知的配置操作类型"):
        await config_service.apply_ops([ConfigOp("nope", ("x",), {})])


async def test_apply_ops_empty_is_noop(container, config_service):
    await config_service.apply_ops([])
