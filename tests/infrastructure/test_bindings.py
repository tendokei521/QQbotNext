"""作用域绑定存储与迁移：global/account 解析、兼容门面、channel_policies、一次性迁移。"""

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


# ==================== 解析 ====================


async def test_global_binding_is_inherited_by_account(tmp_path, settings):
    db, cfg = await _open(tmp_path, settings)
    try:
        await cfg.save_binding("demo", "global", config={"a": 1, "b": 2})
        await cfg.save_binding("demo", "account", "acc1", config={"b": 20})

        assert cfg.get_module_config("demo", "acc1") == {"a": 1, "b": 20}
        assert cfg.get_module_config("demo", "acc2") == {"a": 1, "b": 2}
        assert cfg.get_module_config("demo", None) == {"a": 1, "b": 2}
        assert cfg.get_all_module_configs("demo") == {"acc1": {"b": 20}}
    finally:
        await db.close()


async def test_binding_view_exposes_inheritance_sources(tmp_path, settings):
    db, cfg = await _open(tmp_path, settings)
    try:
        await cfg.save_binding("demo", "global", enabled=True, config={"a": 1})
        await cfg.save_binding("demo", "account", "acc1", enabled=False, config={"b": 2})

        view = cfg.get_binding_view("demo", account_id="acc1")

        assert view["enabled"] is False
        assert view["enabled_source"] == "account"
        assert view["config"] == {"a": 1, "b": 2}
        assert view["sources"] == {"a": "global", "b": "account"}
    finally:
        await db.close()


async def test_account_authority_overrides_but_keeps_config(tmp_path, settings):
    db, cfg = await _open(tmp_path, settings)
    try:
        cfg.set_module_config("demo", "acc1", {"key": "value"})
        cfg.set_module_authority("demo", "acc1", {
            "enabled": False,
            "group_mode": "whitelist",
            "group_list": ["10"],
            "user_mode": "blacklist",
            "user_list": [],
        })

        authority = cfg.get_module_authority("demo", "acc1")
        assert authority["enabled"] is False
        assert authority["group_mode"] == "whitelist"
        # 权限写入不得覆盖业务配置
        assert cfg.get_module_config("demo", "acc1") == {"key": "value"}
    finally:
        await db.close()


async def test_bindings_survive_reload(tmp_path, settings):
    db, cfg = await _open(tmp_path, settings)
    try:
        await cfg.save_binding("demo", "account", "acc1", enabled=False, config={"a": 1})
        await cfg.save_channel_policies("demo", [
            {"channel_id": "10", "policy": "dedupe", "fallback": "closed"},
        ])
    finally:
        await db.close()

    db, cfg = await _open(tmp_path, settings)
    try:
        binding = cfg.get_binding("demo", "account", "acc1")
        assert binding["enabled"] is False
        assert binding["config"] == {"a": 1}
        policy = cfg.get_channel_policy("demo", "10")
        assert policy["policy"] == "dedupe"
        assert policy["fallback"] == "closed"
    finally:
        await db.close()


async def test_unknown_scope_rejected(tmp_path, settings):
    db, cfg = await _open(tmp_path, settings)
    try:
        try:
            cfg.set_binding("demo", "tenant", "x")
        except ValueError:
            pass
        else:  # pragma: no cover - 显式失败信息
            raise AssertionError("未知作用域应当抛 ValueError")
    finally:
        await db.close()


# ==================== channel policies ====================


async def test_channel_policies_replace_and_filter(tmp_path, settings):
    db, cfg = await _open(tmp_path, settings)
    try:
        await cfg.save_channel_policies("demo", [
            {"channel_id": "10", "policy": "delegate", "primary_account_id": "acc2"},
            {"channel_id": "11", "policy": "leader", "primary_account_id": "acc1"},
        ])
        await cfg.save_channel_policies("other", [
            {"channel_id": "10", "policy": "all"},
        ])

        assert len(cfg.list_channel_policies()) == 3
        assert [p["channel_id"] for p in cfg.list_channel_policies("demo")] == ["10", "11"]

        # 整值替换：只保留新列表中的群
        await cfg.save_channel_policies("demo", [
            {"channel_id": "12", "policy": "dedupe"},
        ])
        assert [p["channel_id"] for p in cfg.list_channel_policies("demo")] == ["12"]
        assert cfg.get_channel_policy("demo", "10") is None
    finally:
        await db.close()


# ==================== 一次性迁移 ====================


async def _seed_legacy(db: Database, *, webui: dict | None = None) -> None:
    now = 1
    await db.execute(
        "INSERT OR REPLACE INTO module_config (module_name, bot_id, config_json, updated_at) VALUES (?,?,?,?)",
        ("demo", "acc1", json.dumps({"a": 1}), now),
    )
    await db.execute(
        "INSERT OR REPLACE INTO module_config (module_name, bot_id, config_json, updated_at) VALUES (?,?,?,?)",
        ("demo", None, json.dumps({"a": 0, "g": True}), now),
    )
    await db.execute(
        "INSERT OR REPLACE INTO module_authority "
        "(module_name, bot_id, enabled, group_mode, group_list, user_mode, user_list, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("demo", "acc1", 0, "whitelist", json.dumps(["10"]), "blacklist", json.dumps([]), now),
    )
    for key, value in (webui or {}).items():
        await db.execute(
            "INSERT OR REPLACE INTO webui_config (key, value_json) VALUES (?,?)",
            (key, json.dumps(value)),
        )


async def test_migrates_legacy_tables_and_single_service(tmp_path, settings):
    db = Database(settings.db_path)
    await db.connect()
    await _seed_legacy(db, webui={
        "single_service": {"demo": True},
        "multi_group": {"show_all": False, "groups": {"10": {"service_bot_id": "acc2"}}},
    })
    cfg = ConfigService(db, tmp_path / "root")
    await cfg.init()  # 迁移发生在 init 内

    # 旧两表 → bindings（account + global 都迁；同 capability/scope 合并）
    account = cfg.get_binding("demo", "account", "acc1")
    assert account["enabled"] is False
    assert account["config"] == {"a": 1}
    assert account["permission"]["group_mode"] == "whitelist"
    global_binding = cfg.get_binding("demo", "global")
    assert global_binding["config"] == {"a": 0, "g": True}

    # 旧 single_service + multi_group → channel_policies（delegate + 指定账号）
    policy = cfg.get_channel_policy("demo", "10")
    assert policy["policy"] == "delegate"
    assert policy["primary_account_id"] == "acc2"
    assert policy["fallback"] == "open"

    # 幂等：二次 init 不重复迁移、不丢数据
    await cfg.init()
    assert cfg.get_binding("demo", "account", "acc1")["enabled"] is False
    assert cfg.get_channel_policy("demo", "10")["primary_account_id"] == "acc2"
    await db.close()


async def test_migration_keeps_groups_without_service_account_unfiltered(tmp_path, settings):
    """旧行为：开关开、但群没指定服务号 = 不过滤；迁移不应凭空造一条策略。"""
    db = Database(settings.db_path)
    await db.connect()
    await db.execute(
        "INSERT OR REPLACE INTO webui_config (key, value_json) VALUES (?,?)",
        ("single_service", json.dumps({"demo": True})),
    )
    await db.execute(
        "INSERT OR REPLACE INTO webui_config (key, value_json) VALUES (?,?)",
        ("multi_group", json.dumps({"groups": {"11": {}}})),
    )
    cfg = ConfigService(db, tmp_path / "root")
    await cfg.init()

    assert cfg.get_channel_policy("demo", "11") is None
    await db.close()


async def test_migration_is_idempotent_and_keeps_existing_bindings(tmp_path, settings):
    db = Database(settings.db_path)
    await db.connect()
    await _seed_legacy(db)
    await db.execute(
        "INSERT OR REPLACE INTO bindings "
        "(capability_id, scope_type, scope_id, enabled, config_json, permission_json, priority, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("demo", "account", "acc1", 1, json.dumps({"manual": True}), "{}", 100, 1),
    )
    cfg = ConfigService(db, tmp_path / "root")
    await cfg.init()

    # 已存在的 binding 不被旧表覆盖（INSERT OR IGNORE）
    assert cfg.get_binding("demo", "account", "acc1")["config"] == {"manual": True}
    # 迁移标记写入后，二次 init 不再改动
    await cfg.init()
    assert cfg.get_binding("demo", "account", "acc1")["config"] == {"manual": True}
    await db.close()
