"""`apply_ops` 失败回滚的隔离性：只回滚本次涉及的 key，不能抹掉并发写入。

历史问题（`config_service.apply_ops`）：先 ``deepcopy`` 整个内存缓存作快照，
一旦 SQLite 事务失败就整表恢复——事务等待/失败期间另一处已经成功的配置写入
（比如另一个页面保存了角色预设）会被一起还原掉，内存与数据库从此不一致。

修法：按 key 记录旧值，失败只还原本次 op 触及的 key。
"""

from __future__ import annotations

import sqlite3

import pytest

from app.infrastructure.config.config_service import ConfigOp


async def test_apply_ops_rolls_back_only_its_own_keys(config_service, monkeypatch):
    await config_service.save_role_preset("keep", {"name": "保留", "system_prompt": "x"})

    async def _boom(_sqls):
        # 模拟「事务等待期间另一处配置写入已经成功」：同一任务内重入锁允许
        await config_service.save_role_preset(
            "other", {"name": "并发写入", "system_prompt": "y"}
        )
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(config_service.db, "run_in_transaction", _boom)

    with pytest.raises(sqlite3.OperationalError):
        await config_service.apply_ops([
            ConfigOp("role_preset", ("r1",), {"name": "本次事务", "system_prompt": "z"}),
        ])

    assert config_service.get_role_preset("r1") is None, "本次事务的改动必须回滚"
    assert config_service.get_role_preset("other") is not None, "并发写入不能被整表回滚抹掉"
    assert config_service.get_role_preset("keep") is not None


async def test_apply_ops_rollback_restores_previous_value(config_service, monkeypatch):
    """本次 op 覆盖已有值时，失败要还原成旧值（不是删掉）。"""
    await config_service.save_binding("demo", "global", enabled=True)

    async def _boom(_sqls):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(config_service.db, "run_in_transaction", _boom)

    with pytest.raises(sqlite3.OperationalError):
        await config_service.apply_ops([
            ConfigOp("binding", ("demo", "global", ""), {
                "enabled": False, "config": {"x": 1}, "permission": {}, "priority": 100,
            }),
        ])

    binding = config_service.get_binding("demo", "global")
    assert binding is not None and binding["enabled"] is True
    assert binding["config"] == {}


async def test_apply_ops_success_still_atomic(config_service, db):
    """成功路径不变：内存与库同时可见。"""
    await config_service.apply_ops([
        ConfigOp("provider_model", ("m1",), {
            "preset_id": "p1", "model": "deepseek-chat", "enabled": True,
            "created_at": 1, "updated_at": 1,
        }),
        ConfigOp("channel_override", ("acc1", "agent", "111"), {
            "enabled": False, "config": {}, "permission": {}, "muted_until": 0,
        }),
    ])

    assert config_service.get_provider_model("m1")["model"] == "deepseek-chat"
    assert config_service.get_channel_override("acc1", "agent", "111")["enabled"] is False
    assert await db.fetchone("SELECT id FROM provider_models WHERE id = ?", ("m1",))
    assert await db.fetchone(
        "SELECT account_id FROM channel_overrides WHERE account_id = ? AND channel_id = ?",
        ("acc1", "111"),
    )
