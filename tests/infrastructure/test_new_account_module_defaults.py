"""新账号的模块默认关：**加个号 ≠ 全功能上线**。

规则（`account_store.get_modules_default_enabled` / `binding_store.module_default_enabled`）：

1. 新建账号：没被显式打开的模块一律不响应（enabled 默认 False）；
2. 老账号（升级前已存在）：默认开，升级零行为漂移；
3. 显式开关（account 层 binding）覆盖默认值，双向都算；
4. **框架级 Agent 不受影响**：关掉它新账号配完向导也完全不回复，要静默用 Agent 自己的开关。
"""

from __future__ import annotations

from app.infrastructure.config.config_service import ConfigService
from app.infrastructure.persistence.database import Database


async def test_new_account_modules_off_but_agent_on(config_service):
    """新账号：模块默认关；Agent 默认开。"""
    account_id = await config_service.add_bot({"id": "fresh", "ws_url": "ws://a"})

    assert config_service.get_modules_default_enabled(account_id) is False
    assert config_service.get_module_authority("demo", account_id)["enabled"] is False
    assert config_service.get_module_authority("agent", account_id)["enabled"] is True


async def test_onebot_tools_module_is_on_for_new_accounts(config_service):
    """「工具扩展」（OneBot 工具 / MCP）是新账号默认关的例外：装好就能用。"""
    account_id = await config_service.add_bot({"id": "fresh", "ws_url": "ws://a"})

    assert config_service.get_module_authority("llm_tools_extra", account_id)["enabled"] is True
    # 其它业务模块仍然默认关（不能被这条例外带偏）
    assert config_service.get_module_authority("llm_memory", account_id)["enabled"] is False


async def test_onebot_tools_default_can_still_be_turned_off(config_service):
    """默认开 ≠ 不能关：显式关闭仍然生效。"""
    account_id = await config_service.add_bot({"id": "fresh", "ws_url": "ws://a"})

    config_service.set_module_authority("llm_tools_extra", account_id, {"enabled": False})

    assert config_service.get_module_authority("llm_tools_extra", account_id)["enabled"] is False


async def test_explicit_switches_override_default(config_service):
    """用户显式打开/关闭的模块，不受账号默认值影响（两个方向都要能覆盖）。"""
    account_id = await config_service.add_bot({"id": "fresh", "ws_url": "ws://a"})

    config_service.set_module_authority("demo", account_id, {"enabled": True})
    assert config_service.get_module_authority("demo", account_id)["enabled"] is True

    config_service.set_module_authority("demo", account_id, {"enabled": False})
    assert config_service.get_module_authority("demo", account_id)["enabled"] is False


async def test_global_switch_still_wins(config_service):
    """global 层是显式的全体配置：开了就对所有账号生效（含新账号）。"""
    account_id = await config_service.add_bot({"id": "fresh", "ws_url": "ws://a"})
    await config_service.save_binding("demo", "global", enabled=True)

    assert config_service.get_module_authority("demo", account_id)["enabled"] is True


async def test_legacy_account_stays_enabled_after_reload(tmp_path):
    """老账号（库里 modules_default_enabled=1）重启后仍是默认开。"""
    root = tmp_path / "root"
    root.mkdir()
    db = Database(tmp_path / "app.db")
    await db.connect()
    try:
        await ConfigService(db, root).init()
        await db.execute(
            "INSERT INTO bots (account_id, name, ws_url, auto_connect, conn_mode, "
            "modules_default_enabled, sort_order) VALUES (?,?,?,?,?,?,?)",
            ("old", "老号", "ws://old", 0, "forward", 1, 0),
        )

        cfg = ConfigService(db, root)
        await cfg.init()

        assert cfg.get_modules_default_enabled("old") is True
        assert cfg.get_module_authority("demo", "old")["enabled"] is True
    finally:
        await db.close()


async def test_old_bots_table_gets_column_without_flipping_defaults(tmp_path):
    """升级路径：老库 bots 表没有该列 → 补列后默认 1，存量账号行为不变。"""
    root = tmp_path / "root"
    root.mkdir()
    db = Database(tmp_path / "legacy.db")
    await db.connect()
    try:
        # 模拟升级前的 bots 表（无 modules_default_enabled 列）
        await db.execute("DROP TABLE bots")
        await db.execute(
            "CREATE TABLE bots ("
            "account_id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', "
            "ws_url TEXT NOT NULL DEFAULT '', owner_id TEXT, "
            "auto_connect INTEGER NOT NULL DEFAULT 0, "
            "conn_mode TEXT NOT NULL DEFAULT 'forward', sort_order INTEGER NOT NULL DEFAULT 0)"
        )
        await db.execute(
            "INSERT INTO bots (account_id, name, ws_url, auto_connect, conn_mode, sort_order) "
            "VALUES ('old', '老号', 'ws://old', 0, 'forward', 0)"
        )

        cfg = ConfigService(db, root)
        await cfg.init()

        assert "modules_default_enabled" in await db.table_columns("bots")
        assert cfg.get_modules_default_enabled("old") is True
        assert cfg.get_module_authority("demo", "old")["enabled"] is True
    finally:
        await db.close()
