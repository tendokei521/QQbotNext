"""配置中心测试：legacy JSON 迁移 + 读写 + 账号身份（account_id）+ ModuleConfig 深合并。

账号身份自 v4 起是「用户设定的 account_id」：
- `id` 唯一、不可变，是配置/运行时的 key；
- `name` 只用于展示与日志；
- 数组下标（旧 bot_index）已移除。
"""

from app.infrastructure.config.config_service import ConfigService
from app.infrastructure.persistence.database import Database
from app.plugin.base import ModuleConfig


class _Svc:
    def __init__(self):
        self.data = {}

    def get_module_config(self, module, bot_id):
        return self.data.get(bot_id, {})

    def set_module_config(self, module, bot_id, data, persist=True):
        self.data[bot_id] = dict(data)


def test_module_config_deep_merge():
    defaults = {
        "nested": {"a": 1, "b": {"x": 1}},
        "items": [],
        "plain": "d",
    }
    svc = _Svc()
    cfg = ModuleConfig("m", 1, defaults, svc)

    # 空存储 → 全默认
    assert cfg.raw_config == defaults

    # 部分存储 + 深合并
    svc.data[1] = {"nested": {"b": {"x": 9}, "c": 2}, "plain": "p", "extra": "keep"}
    raw = cfg.raw_config
    assert raw["nested"]["a"] == 1          # 补缺
    assert raw["nested"]["b"]["x"] == 9     # 覆盖
    assert raw["nested"]["c"] == 2          # 默认外新增保留
    assert raw["plain"] == "p"
    assert raw["extra"] == "keep"           # 多余键不删


def test_module_config_none_stored_falls_back_to_default():
    defaults = {"flag": True}
    svc = _Svc()
    svc.data[1] = {"flag": None}
    cfg = ModuleConfig("m", 1, defaults, svc)
    assert cfg.get("flag") is True
    assert cfg.raw_config["flag"] is True


def test_module_config_cast_on_set():
    svc = _Svc()
    svc.data[1] = {"count": 5, "name": "x", "enabled": False, "lst": ["a"]}
    cfg = ModuleConfig("m", 1, {}, svc)
    cfg.set("count", "10")                 # 旧值 int → 转 int
    cfg.set("name", 123)                   # 旧值 str → 转 str
    cfg.set("enabled", "true")             # 旧值 bool → 字符串特判 True
    cfg.set("lst", ["a", "b"])             # 容器原样
    assert svc.data[1]["count"] == 10
    assert svc.data[1]["name"] == "123"
    assert svc.data[1]["enabled"] is True
    assert svc.data[1]["lst"] == ["a", "b"]


def test_module_config_dot_access():
    svc = _Svc()
    svc.data[1] = {"api_key": "sk-xxx"}
    cfg = ModuleConfig("m", 1, {}, svc)
    assert cfg.api_key == "sk-xxx"
    assert cfg.missing is None


async def test_migrate_from_legacy(project_root, settings):
    db = Database(settings.db_path)
    await db.connect()
    cfg = ConfigService(db, project_root)
    await cfg.init()

    # bots 迁移：旧 JSON 没有连接方向 → 默认正向；id 由迁移自动分配，name 给默认名
    bots = cfg.get_bots()
    assert bots == [{
        "id": "bot1",
        "name": "账号 1",
        "ws_url": "ws://127.0.0.1:1",
        "owner_id": 10001,
        "auto_connect": False,
        "conn_mode": "forward",
    }]

    # webui 配置迁移
    webui = cfg.get_webui_config()
    assert webui["logs"]["max_lines"] == 10

    # 模块配置迁移（legacy JSON 的 key 是 QQ 号，迁移后原样保留为 key）
    assert cfg.get_module_config("demo", 12345) == {"key1": "v1", "num": 42}

    # 模块权限迁移
    auth = cfg.get_module_authority("demo", 12345)
    assert auth["enabled"] is True
    assert auth["group_mode"] == "whitelist"
    assert auth["group_list"] == ["1"]

    # 幂等：再次 init 不重复（数据已存在则跳过迁移）
    await cfg.init()
    assert cfg.get_module_config("demo", 12345) == {"key1": "v1", "num": 42}

    await db.close()


async def test_save_and_load_bots(config_service):
    """保存按 id upsert；顺序由 payload 决定（id 不可变，不能靠下标表达顺序）。"""
    await config_service.save_bots([
        {"id": "main", "name": "主号", "ws_url": "ws://a", "owner_id": 1, "auto_connect": True},
        {"id": "backup", "name": "备用", "ws_url": "ws://b", "owner_id": None, "auto_connect": False},
    ])
    bots = config_service.get_bots()
    assert [b["id"] for b in bots] == ["main", "backup"]
    assert [b["name"] for b in bots] == ["主号", "备用"]
    assert bots[0]["auto_connect"] is True


async def test_add_bot_assigns_id_and_name(config_service):
    """不传 id 时自动分配 botN；name 缺省给「账号 N」。"""
    first = await config_service.add_bot({"ws_url": "ws://a"})
    second = await config_service.add_bot({"ws_url": "ws://b"})
    assert first == "bot1"
    assert second == "bot2"
    assert config_service.get_account_name(first) == "账号 1"


async def test_add_bot_rejects_invalid_and_duplicate_id(config_service):
    """「没填」自动分配，「填错」必须报错——否则用户手滑会被静默换成另一个 id。"""
    import pytest

    with pytest.raises(ValueError):
        await config_service.add_bot({"id": "bad id!", "ws_url": "ws://a"})
    assert config_service.get_bots() == []

    await config_service.add_bot({"id": "main", "name": "主号", "ws_url": "ws://a"})
    with pytest.raises(ValueError):
        await config_service.add_bot({"id": "main", "ws_url": "ws://b"})


async def test_rename_keeps_id(config_service):
    """改名只动 name，id（外部引用）保持不变。"""
    await config_service.save_bots([{"id": "main", "name": "旧名", "ws_url": "ws://a"}])
    await config_service.save_bots([{"id": "main", "name": "新名", "ws_url": "ws://a"}])
    bots = config_service.get_bots()
    assert bots[0]["id"] == "main"
    assert bots[0]["name"] == "新名"


async def test_bot_accounts_table_created(tmp_path):
    """新库与老库都应存在 bot_accounts 表（老库靠 CREATE TABLE IF NOT EXISTS 补建）。"""
    path = tmp_path / "fresh.db"
    db = Database(path)
    await db.connect()
    try:
        assert "bot_accounts" in {r["name"] for r in await db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        cols = await db.table_columns("bot_accounts")
        assert cols >= {"account_id", "user_id", "nickname", "last_login_at"}
    finally:
        await db.close()


async def test_ensure_columns_is_idempotent(tmp_path):
    """ensure_columns 老库迁移：缺列则补，重复调用不报错。"""
    path = tmp_path / "legacy.db"
    db = Database(path)
    await db.connect()
    try:
        await db.execute("CREATE TABLE legacy_tbl (id INTEGER PRIMARY KEY)")
        await db.ensure_columns("legacy_tbl", {"extra": "TEXT"})
        assert "extra" in await db.table_columns("legacy_tbl")
        await db.ensure_columns("legacy_tbl", {"extra": "TEXT"})  # 幂等
        assert "extra" in await db.table_columns("legacy_tbl")
    finally:
        await db.close()


async def test_save_and_load_bot_accounts(config_service, settings, project_root):
    """上次登录 QQ：首次写入返回 True 并落盘；重启后仍可读回。"""
    assert await config_service.save_bot_account("main", 12345, "小明") is True
    info = config_service.get_bot_account("main")
    assert info and info["user_id"] == "12345" and info["nickname"] == "小明"
    assert info["last_login_at"] > 0

    # 重启：新 ConfigService 从 SQLite 重新加载
    db2 = Database(settings.db_path)
    await db2.connect()
    try:
        cfg2 = ConfigService(db2, project_root)
        await cfg2.init()
        assert cfg2.get_bot_account("main")["nickname"] == "小明"
    finally:
        await db2.close()


async def test_save_bot_account_same_account_is_noop(config_service):
    """账号相同 → 不重复写库、不通知（满足「相同不变」）。"""
    assert await config_service.save_bot_account("main", 1, "A") is True
    assert await config_service.save_bot_account("main", 1, "A") is False
    # 同一账号换了昵称也视为不变（只有 user_id 变化才算换 QQ）
    assert await config_service.save_bot_account("main", 1, "A2") is False
    # 未登录成功的空值不落盘
    assert await config_service.save_bot_account("other", 0, "") is False
    assert config_service.get_bot_account("other") is None


async def test_bot_account_changes_when_switching_qq(config_service):
    assert await config_service.save_bot_account("main", 111, "旧号") is True
    assert await config_service.save_bot_account("main", 222, "新号") is True
    info = config_service.get_bot_account("main")
    assert info["user_id"] == "222" and info["nickname"] == "新号"


async def test_delete_bot_only_drops_its_own_snapshot(config_service):
    """删账号只作废它自己的快照——id 不可变，别的账号不会再被"顶上"。"""
    await config_service.save_bots([
        {"id": "a", "name": "甲", "ws_url": "ws://a"},
        {"id": "b", "name": "乙", "ws_url": "ws://b"},
    ])
    await config_service.save_bot_account("a", 1, "A")
    await config_service.save_bot_account("b", 2, "B")
    assert await config_service.delete_bot("a") is True
    assert config_service.get_bot_account("a") is None
    assert config_service.get_bot_account("b")["user_id"] == "2"
    assert [x["id"] for x in config_service.get_bots()] == ["b"]


async def test_save_bots_deletes_accounts_missing_from_payload(config_service):
    """全量保存时，payload 里没有的 id 视为删除，并连带清理它的快照。"""
    await config_service.save_bots([
        {"id": "a", "name": "甲", "ws_url": "ws://a"},
        {"id": "b", "name": "乙", "ws_url": "ws://b"},
    ])
    await config_service.save_bot_account("b", 2, "B")
    await config_service.save_bots([{"id": "a", "name": "甲", "ws_url": "ws://a"}])
    assert config_service.get_bot_account("b") is None
    assert [x["id"] for x in config_service.get_bots()] == ["a"]


async def test_module_config_read_write(config_service):
    config_service.set_module_config("demo", None, {"a": 1, "b": "x"})
    assert config_service.get_module_config("demo", None) == {"a": 1, "b": "x"}


async def test_default_webui_config_shape(config_service):
    cfg = config_service.get_webui_config()
    assert "logs" in cfg
    assert "single_service" in cfg
    assert "multi_group" in cfg


# ==================== 连接方向（正向 / 反向 WS）====================


async def test_bots_conn_mode_roundtrip(tmp_path):
    """连接方向要落库：反向账号保存后重启仍读回 reverse（否则重启变正向去连）。

    用独立空根目录建库：`project_root` 夹具带 legacy JSON，会在二次启动时再迁移
    一批 bots 覆盖回默认值，掩盖「conn_mode 有没有真的落盘」这个断言。
    """
    empty_root = tmp_path / "empty_root"
    empty_root.mkdir()
    db = Database(tmp_path / "roundtrip.db")
    await db.connect()
    try:
        cfg = ConfigService(db, empty_root)
        await cfg.init()
        await cfg.save_bots([
            {"id": "rev", "name": "反向号", "ws_url": "ws://127.0.0.1:3001/onebot/v11/ws",
             "conn_mode": "reverse", "auto_connect": True},
            {"id": "fwd", "name": "正向号", "ws_url": "ws://127.0.0.1:3002/",
             "conn_mode": "forward", "auto_connect": False},
        ])
        assert [b["conn_mode"] for b in cfg.get_bots()] == ["reverse", "forward"]
        # 对外接口也要带上（前端据此渲染反向监听字段）
        assert [b["conn_mode"] for b in cfg.get_bots_public()] == ["reverse", "forward"]

        # 重启：同一份 SQLite 重新加载
        cfg2 = ConfigService(db, empty_root)
        await cfg2.init()
        assert [b["conn_mode"] for b in cfg2.get_bots()] == ["reverse", "forward"]
    finally:
        await db.close()


async def test_bots_conn_mode_defaults_to_forward(config_service):
    """未提交连接方向：新账号默认正向，已有账号沿用旧值（不能被静默改成正向）。"""
    await config_service.save_bots([{"id": "a", "ws_url": "ws://a"}])
    assert config_service.get_bots()[0]["conn_mode"] == "forward"

    await config_service.save_bots([{"id": "a", "ws_url": "ws://a", "conn_mode": "reverse"}])
    # 模拟老前端全量保存：payload 里没有 conn_mode
    await config_service.save_bots([{"id": "a", "ws_url": "ws://a"}])
    assert config_service.get_bots()[0]["conn_mode"] == "reverse"


async def test_bots_conn_mode_normalizes_unknown_values(config_service):
    """非法/别名写法统一归一，避免前后端各认一套（server / ws_reverse / 空串等）。"""
    await config_service.save_bots([
        {"id": "a", "ws_url": "ws://a", "conn_mode": "server"},
        {"id": "b", "ws_url": "ws://b", "conn_mode": "ws_reverse"},
        {"id": "c", "ws_url": "ws://c", "conn_mode": ""},
        {"id": "d", "ws_url": "ws://d", "conn_mode": None},
        {"id": "e", "ws_url": "ws://e", "conn_mode": "garbage"},
    ])
    assert [b["conn_mode"] for b in config_service.get_bots()] == [
        "reverse", "reverse", "forward", "forward", "forward",
    ]


async def test_legacy_bots_table_migrates_to_account_id(tmp_path):
    """老库（bots 表以 bot_index 为主键）启动时整体迁移成 account_id + name。

    已登录过的账号 id 取它当时的 QQ（旧模块配置 key 天然对上）；
    从未登录过的取 botN，name 给「账号 N」。
    """
    empty_root = tmp_path / "empty_root"
    empty_root.mkdir()
    db = Database(tmp_path / "old.db")
    await db.connect()
    try:
        await db.execute("DROP TABLE bots")
        await db.execute("DROP TABLE bot_accounts")
        await db.execute(
            "CREATE TABLE bots (bot_index INTEGER PRIMARY KEY, ws_url TEXT NOT NULL DEFAULT '', "
            "owner_id TEXT, auto_connect INTEGER NOT NULL DEFAULT 0)"
        )
        await db.execute(
            "INSERT INTO bots (bot_index, ws_url, auto_connect) VALUES (0, 'ws://old', 1)"
        )
        await db.execute(
            "INSERT INTO bots (bot_index, ws_url, auto_connect) VALUES (1, 'ws://old2', 0)"
        )
        await db.execute(
            "CREATE TABLE bot_accounts (bot_index INTEGER PRIMARY KEY, user_id TEXT, "
            "nickname TEXT NOT NULL DEFAULT '', last_login_at INTEGER NOT NULL DEFAULT 0)"
        )
        await db.execute(
            "INSERT INTO bot_accounts VALUES (0, '10001', '大号', 1700000000)"
        )

        cfg = ConfigService(db, empty_root)
        await cfg.init()

        cols = await db.table_columns("bots")
        assert "account_id" in cols and "name" in cols
        assert "bot_index" not in cols

        bots = cfg.get_bots()
        # 0 号登录过 → id 取 QQ，name 取昵称；1 号没登录过 → botN + 默认名
        assert [b["id"] for b in bots] == ["10001", "bot2"]
        assert [b["name"] for b in bots] == ["大号", "账号 2"]
        assert bots[0]["conn_mode"] == "forward"
        assert cfg.get_bot_account("10001")["user_id"] == "10001"

        # 幂等：再跑一次不产生任何变化
        await cfg.init()
        assert [b["id"] for b in cfg.get_bots()] == ["10001", "bot2"]
    finally:
        await db.close()
