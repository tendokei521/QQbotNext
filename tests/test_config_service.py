"""配置中心测试：legacy JSON 迁移 + 读写 + ModuleConfig 深合并。"""

from app.infrastructure.config.config_service import ConfigService, DEFAULT_WEBUI_CONFIG
from app.infrastructure.persistence.database import Database
from app.modules.base import ModuleConfig


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

    # bots 迁移
    bots = cfg.get_bots()
    assert bots == [{"ws_url": "ws://127.0.0.1:1", "owner_id": 10001, "auto_connect": False}]

    # webui 配置迁移
    webui = cfg.get_webui_config()
    assert webui["logs"]["max_lines"] == 10

    # 模块配置迁移
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
    await config_service.save_bots([
        {"ws_url": "ws://a", "owner_id": 1, "auto_connect": True},
        {"ws_url": "ws://b", "owner_id": None, "auto_connect": False},
    ])
    assert len(config_service.get_bots()) == 2
    assert config_service.get_bots()[0]["auto_connect"] is True


async def test_module_config_read_write(config_service):
    config_service.set_module_config("demo", None, {"a": 1, "b": "x"})
    assert config_service.get_module_config("demo", None) == {"a": 1, "b": "x"}


async def test_default_webui_config_shape(config_service):
    cfg = config_service.get_webui_config()
    assert "logs" in cfg
    assert "single_service" in cfg
    assert "multi_group" in cfg
