"""配置写路径：**先落库成功，再换内存**；落盘失败必须报错而不是静默成功。

历史问题：

- `binding_store.set_binding` / `_persist_binding`：先改内存、再 fire-and-forget 落盘，
  落盘异常被 `except` 吞成一行日志 → API 返回 200 但重启后配置消失；
- `channel_override_store._persist_channel_override` 同样吞异常；
- `group_store.upsert_known_groups` 先改内存再跑事务，事务失败内存与库不一致；
- `ConfigService._schedule_persist` 的任务无人持有引用、失败无告警。

修法：异步写路径先落库（失败抛出，内存不动）；同步写路径的后台落盘纳入
TaskManager，失败重试一次并告警。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time

import pytest


async def _fail_query(_sql: str, _params=()):
    raise sqlite3.OperationalError("disk I/O error")


async def test_save_binding_failure_keeps_memory_unchanged(config_service, db, monkeypatch):
    monkeypatch.setattr(config_service.db, "execute", _fail_query)

    with pytest.raises(sqlite3.OperationalError):
        await config_service.save_binding("demo", "global", enabled=True)

    assert config_service.get_binding("demo", "global") is None, "落库失败不能留下内存里的假配置"
    row = await db.fetchone(
        "SELECT capability_id FROM bindings WHERE capability_id = ?", ("demo",)
    )
    assert row is None


async def test_save_channel_override_failure_keeps_old_row(config_service, db):
    await config_service.save_channel_override("acc1", "agent", "111", enabled=True)

    # 用不可写的事务制造落库失败（monkeypatch 数据库层）
    class _Boom:
        async def execute(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("disk I/O error")

    original = config_service.db.execute
    config_service.db.execute = _Boom().execute  # type: ignore[method-assign]
    try:
        with pytest.raises(sqlite3.OperationalError):
            await config_service.save_channel_override("acc1", "agent", "111", enabled=False)
    finally:
        config_service.db.execute = original  # type: ignore[method-assign]

    assert config_service.get_channel_override("acc1", "agent", "111")["enabled"] is True


async def test_upsert_known_groups_failure_keeps_memory_unchanged(config_service, db, monkeypatch):
    await config_service.upsert_known_groups("acc1", [
        {"group_id": 111, "group_name": "旧群", "member_count": 10},
    ])

    async def _boom(_sqls):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(config_service.db, "run_in_transaction", _boom)

    with pytest.raises(sqlite3.OperationalError):
        await config_service.upsert_known_groups("acc1", [
            {"group_id": 222, "group_name": "新群", "member_count": 5},
        ])

    groups = {g["group_id"]: g for g in config_service.list_known_groups("acc1")}
    assert "222" not in groups, "事务失败不能把新群写进内存"
    assert groups["111"]["in_group"] is True, "事务失败不能把旧群标记成已退群"


async def test_save_channel_policies_failure_keeps_old_rows(config_service, monkeypatch):
    await config_service.save_channel_policies("agent", [
        {"channel_id": "111", "policy": "delegate", "primary_account_id": "acc1"},
    ])

    async def _boom(_sqls):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(config_service.db, "run_in_transaction", _boom)

    with pytest.raises(sqlite3.OperationalError):
        await config_service.save_channel_policies("agent", [
            {"channel_id": "222", "policy": "all"},
        ])

    assert config_service.get_channel_policy("agent", "111") is not None
    assert config_service.get_channel_policy("agent", "222") is None


async def test_save_bots_failure_keeps_old_accounts(config_service, monkeypatch):
    await config_service.save_bots([{"id": "a", "name": "甲", "ws_url": "ws://a"}])

    async def _boom(_sqls):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(config_service.db, "run_in_transaction", _boom)

    with pytest.raises(sqlite3.OperationalError):
        await config_service.save_bots([{"id": "b", "name": "乙", "ws_url": "ws://b"}])

    assert [b["id"] for b in config_service.get_bots()] == ["a"]


async def test_provider_and_role_preset_failure_keeps_memory(config_service, monkeypatch):
    monkeypatch.setattr(config_service.db, "execute", _fail_query)

    with pytest.raises(sqlite3.OperationalError):
        await config_service.save_provider_preset("p1", {"name": "P", "provider": "openai"})
    assert config_service.get_provider_preset("p1") is None

    with pytest.raises(sqlite3.OperationalError):
        await config_service.save_role_preset("r1", {"name": "R", "system_prompt": "x"})
    assert config_service.get_role_preset("r1") is None


async def test_schedule_persist_retries_then_logs_error(config_service, monkeypatch, caplog):
    """同步写路径的后台落盘：失败重试一次，最终失败打 error（不能静默）。"""
    monkeypatch.setattr(config_service.db, "execute", _fail_query)

    with caplog.at_level(logging.WARNING):
        config_service.set_binding("demo", "global", enabled=True)
        # 内存立即生效（同步接口的约定），落库在后台重试
        assert config_service.get_binding("demo", "global")["enabled"] is True
        await asyncio.sleep(0.8)

    assert "配置落盘失败" in caplog.text
    assert "已重试" in caplog.text


async def test_updated_at_uses_wall_clock(config_service, db):
    """updated_at 必须是 Unix 秒（墙钟），不能是事件循环的 monotonic 秒。"""
    await config_service.save_binding("demo", "global", enabled=True)
    await config_service.save_channel_override("acc1", "agent", "111", enabled=True)

    now = int(time.time())
    binding = await db.fetchone(
        "SELECT updated_at FROM bindings WHERE capability_id = ? AND scope_type = ? AND scope_id = ?",
        ("demo", "global", ""),
    )
    override = await db.fetchone(
        "SELECT updated_at FROM channel_overrides WHERE account_id = ? AND channel_id = ?",
        ("acc1", "111"),
    )

    for row in (binding, override):
        assert row is not None
        assert row["updated_at"] > 1_600_000_000, "明显是 loop.time() 的小数值"
        assert abs(row["updated_at"] - now) <= 60
    assert config_service.get_channel_override("acc1", "agent", "111")["updated_at"] > 1_600_000_000
