"""账号作用域数据的生命周期：**改名要搬、删号要清、孤儿要收**。

账号 id 是 `bindings(account)` / `channel_overrides` / `known_groups` /
`channel_policies.primary_account_id` / `account_active_bundle` / `bot_accounts`
的主键。历史实现只改 `bots` 表，于是：

- 改名后新 id 看不到旧设置（页面上全变成「继承」）；
- 删号后旧行还在，以后复用同一个 id 会被幽灵配置串上。

本文件把「改名搬全、删号清净、启动收孤儿」三件事钉死。
"""

from __future__ import annotations

import json

from app.infrastructure.config.config_service import ConfigService
from app.infrastructure.persistence.database import Database


async def _open(tmp_path, settings):
    db = Database(settings.db_path)
    await db.connect()
    cfg = ConfigService(db, tmp_path / "root")
    await cfg.init()
    return db, cfg


async def _seed_account_data(cfg, account_id: str = "old") -> None:
    """给账号铺满每一类账号作用域数据。"""
    await cfg.save_binding("llm_memory", "account", account_id, enabled=True)
    await cfg.save_channel_override(account_id, "agent", "__group__", enabled=True)
    await cfg.save_channel_override(account_id, "agent", "111", enabled=False)
    await cfg.upsert_known_groups(account_id, [
        {"group_id": 111, "group_name": "前端交流群", "member_count": 24},
    ])
    await cfg.save_channel_policies("agent", [
        {"channel_id": "111", "policy": "delegate", "primary_account_id": account_id},
    ])
    await cfg.save_bot_account(account_id, 10001, "主号")
    await cfg.db.execute(
        "INSERT OR REPLACE INTO account_active_bundle "
        "(account_id, bundle_id, activation_id, updated_at) VALUES (?,?,?,?)",
        (account_id, "bundle-1", "activation-1", 1),
    )


async def test_rename_moves_all_account_scoped_data(tmp_path, settings):
    """改账号 id：绑定 / 群级覆盖 / 群列表 / 响应权 / 套装激活 / 登录快照全跟着搬。"""
    db, cfg = await _open(tmp_path, settings)
    try:
        await cfg.add_bot({"id": "old", "name": "老号", "ws_url": "ws://old"})
        await _seed_account_data(cfg, "old")
        await cfg.set_modules_default_enabled("old", True)   # 模拟升级前的老账号

        result = await cfg.rename_account("old", "new")

        assert result["moved"] is True
        assert [b["id"] for b in cfg.get_bots()] == ["new"]
        assert cfg.get_modules_default_enabled("new") is True, "改名不是新建，默认开关要跟过来"

        # 每一项都搬到了 new，且 old 名下没有残留
        assert cfg.get_binding("llm_memory", "account", "new") is not None
        assert cfg.get_binding("llm_memory", "account", "old") is None
        assert cfg.get_channel_override("new", "agent", "111")["enabled"] is False
        assert cfg.get_channel_override("new", "agent", "__group__")["enabled"] is True
        assert cfg.get_channel_override("old", "agent", "111") is None
        assert [g["group_id"] for g in cfg.list_known_groups("new")] == ["111"]
        assert cfg.list_known_groups("old") == []
        assert cfg.get_channel_policy("agent", "111")["primary_account_id"] == "new"
        assert cfg.get_bot_account("new")["user_id"] == "10001"
        assert cfg.get_bot_account("old") is None
        rows = await db.fetchall(
            "SELECT account_id FROM account_active_bundle WHERE account_id = ?", ("new",)
        )
        assert len(rows) == 1
    finally:
        await db.close()


async def test_delete_removes_all_account_scoped_data(tmp_path, settings):
    """删号：作用域数据全清；群响应权只置空（群级配置不能跟着消失）。"""
    db, cfg = await _open(tmp_path, settings)
    try:
        await cfg.add_bot({"id": "gone", "name": "要删的", "ws_url": "ws://gone"})
        await _seed_account_data(cfg, "gone")

        assert await cfg.delete_bot("gone") is True

        assert cfg.get_bots() == []
        assert cfg.get_binding("llm_memory", "account", "gone") is None
        assert cfg.list_channel_overrides("gone") == []
        assert cfg.list_known_groups("gone") == []
        assert cfg.get_bot_account("gone") is None
        assert cfg.get_channel_policy("agent", "111")["primary_account_id"] is None
        rows = await db.fetchall("SELECT account_id FROM account_active_bundle")
        assert rows == []
    finally:
        await db.close()


async def test_startup_purges_orphan_account_data_with_backup(tmp_path, settings):
    """历史遗留的孤儿行（bots 里没有该账号）→ 启动时清理，并留一份备份。"""
    db = Database(settings.db_path)
    await db.connect()
    root = tmp_path / "root"
    try:
        cfg = ConfigService(db, root)
        await cfg.init()
        await cfg.add_bot({"id": "live", "name": "在用的", "ws_url": "ws://live"})
        # 直接往库里写"账号已不存在"的孤儿行（模拟旧版本改名/删号留下的）
        await db.execute(
            "INSERT OR REPLACE INTO bindings "
            "(capability_id, scope_type, scope_id, enabled, config_json, permission_json, "
            "priority, updated_at) VALUES ('llm_memory','account','ghost',1,'{}','{}',100,0)"
        )
        await db.execute(
            "INSERT OR REPLACE INTO channel_overrides "
            "(account_id, capability, channel_id, enabled, config_json, permission_json, "
            "muted_until, updated_at) VALUES ('ghost','agent','111',0,'{}','{}',0,0)"
        )
        await db.execute(
            "INSERT OR REPLACE INTO known_groups "
            "(account_id, group_id, name, member_count, max_member_count, in_group, updated_at) "
            "VALUES ('ghost','111','幽灵群',1,1,1,0)"
        )
        await db.execute(
            "INSERT OR REPLACE INTO channel_policies "
            "(capability_id, channel_id, policy, primary_account_id, fallback, updated_at) "
            "VALUES ('agent','111','delegate','ghost','open',0)"
        )

        # 重启（新 ConfigService 走完整 init）→ 孤儿被清掉，live 账号不受影响
        cfg2 = ConfigService(db, root)
        await cfg2.init()

        assert cfg2.get_binding("llm_memory", "account", "ghost") is None
        assert cfg2.list_channel_overrides("ghost") == []
        assert cfg2.list_known_groups("ghost") == []
        assert cfg2.get_channel_policy("agent", "111")["primary_account_id"] is None
        assert [b["id"] for b in cfg2.get_bots()] == ["live"]

        backup = root / "data" / "orphan-account-data.json"
        assert backup.exists(), "清理前要留备份"
        data = json.loads(backup.read_text(encoding="utf-8"))
        assert "ghost" in data["accounts"]
        assert data["channel_overrides"], "备份里要有被删掉的行"
    finally:
        await db.close()


async def test_live_account_data_is_not_touched_by_purge(tmp_path, settings):
    """有 bots 行的账号，数据一条都不能被孤儿清理误删。"""
    db, cfg = await _open(tmp_path, settings)
    try:
        await cfg.add_bot({"id": "live", "name": "在用的", "ws_url": "ws://live"})
        await _seed_account_data(cfg, "live")

        cfg2 = ConfigService(db, tmp_path / "root")
        await cfg2.init()

        assert cfg2.get_binding("llm_memory", "account", "live") is not None
        assert cfg2.get_channel_override("live", "agent", "111")["enabled"] is False
        assert [g["group_id"] for g in cfg2.list_known_groups("live")] == ["111"]
    finally:
        await db.close()
