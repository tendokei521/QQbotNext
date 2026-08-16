"""Provider 数据源与 list/dynamic API 测试。"""

import sys

import pytest

from app.modules import resolve_enabled_ids
from app.services.provider_service import ProviderRegistry

MODULE_SRC = '''
from app.modules import BaseModule

class Module(BaseModule):
    name = "Provider测试"
    sign = "ProviderTest"
    description = ""
    permission = "member"
    subscribe = ("message_group",)
    default_config = {}

    config_schema = {
        "group_list": {
            "type": "list", "label": "目标群", "endpoint": "groups",
            "id_field": "group_id", "name_field": "group_name",
            "meta_fields": ["member_count"], "sortable": True,
            "checkboxes": True, "mode_select": True, "default": {},
        },
        "provider_cfg": {
            "type": "dynamic", "label": "提供商", "endpoint": "providers", "default": {},
        },
    }

    LIST_PROVIDERS = {"groups": "list_groups"}
    DYNAMIC_PROVIDERS = {"providers": "dynamic_providers"}

    async def list_groups(self, field, bot):
        return {"items": [
            {"group_id": "g1", "group_name": "群一", "member_count": 10},
            {"group_id": "g2", "group_name": "群二", "member_count": 20},
        ]}

    async def dynamic_providers(self, field, bot, value=None):
        if value is None:
            return {"options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]}
        return {"fields": [{"key": "k1", "type": "string", "label": "字段1"}]}

    async def handle(self, event):
        pass
'''


class FakeBot:
    bot_id = 123
    index = 0
    owner_id = None

    async def get_group_list(self):
        return {"status": "ok", "data": [
            {"group_id": "g1", "group_name": "群一", "member_count": 10},
            {"group_id": "g2", "group_name": "群二", "member_count": 20},
        ]}

    async def get_friend_list(self):
        return {"status": "ok", "data": [{"user_id": "u1", "nickname": "张三"}]}


class _Ctx:
    module_name = "testmod"


def test_resolve_enabled_ids_modes():
    cfg = {"g1": {"enabled": True, "index": 1}, "g2": {"enabled": False, "index": 0}}
    assert resolve_enabled_ids(cfg, "all") == ["g1", "g2"]
    assert resolve_enabled_ids(cfg, "none") == []
    assert resolve_enabled_ids(cfg, "partial") == ["g1"]


def test_resolve_enabled_ids_legacy_list():
    assert resolve_enabled_ids(["g1", "g2"], "all") == ["g1", "g2"]
    assert resolve_enabled_ids([], "all") == []
    assert resolve_enabled_ids({}, "all") == []


@pytest.fixture
def registry():
    return ProviderRegistry(log=None)


async def test_register_and_call_module_provider(registry):
    registry.register_list("testmod", "groups", lambda m, f, b: {"items": [{"id": "x"}]})
    result = await registry.call("testmod", "groups", "list", _Ctx(), None, {})
    assert result == {"items": [{"id": "x"}]}


async def test_builtin_groups_provider(registry):
    result = await registry.call("nomodule", "groups", "list", _Ctx(), FakeBot(), {})
    items = result["items"]
    assert items[0]["group_id"] == "g1"
    assert items[0]["group_name"] == "群一"
    assert items[0]["member_count"] == 10


async def test_builtin_friends_provider(registry):
    result = await registry.call("nomodule", "friends", "list", _Ctx(), FakeBot(), {})
    assert result["items"][0] == {"user_id": "u1", "nickname": "张三"}


async def test_builtin_provider_no_bot_returns_empty(registry):
    result = await registry.call("nomodule", "groups", "list", _Ctx(), None, {})
    assert result == {"items": []}


# ── API 集成 ──────────────────────────────────────────────


@pytest.fixture
async def api_env(settings, tmp_path):
    mod_dir = tmp_path / "module" / "modules" / "testmod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "__init__.py").write_text("", encoding="utf-8")
    (mod_dir / "module.py").write_text(MODULE_SRC, encoding="utf-8")
    (tmp_path / "module" / "modules" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "module" / "__init__.py").write_text("", encoding="utf-8")

    from app.bootstrap import build_container
    from app.infrastructure.config.config_service import ConfigService
    from app.infrastructure.onebot.gateway import OneBotGateway
    from app.infrastructure.persistence.database import Database
    from app.modules.registry import ModuleRegistry
    from app.webui.app import create_app

    for key in [k for k in sys.modules if k == "module" or k.startswith("module.")]:
        del sys.modules[key]
    sys.path.insert(0, str(tmp_path))
    try:
        c = build_container(settings)
        db = c.get(Database)
        await db.connect()
        await c.get(ConfigService).init()
        c.get(OneBotGateway).connections = {0: FakeBot()}
        await c.get(ModuleRegistry).load_all(bot_id=123)
        app = create_app(c)
        from starlette.testclient import TestClient

        client = TestClient(app)
        yield client, c
        await db.close()
    finally:
        sys.path.remove(str(tmp_path))
        # 清理临时 module 包，避免遮蔽项目 module
        for key in [k for k in sys.modules if k == "module" or k.startswith("module.")]:
            del sys.modules[key]


async def test_list_api_returns_items_with_saved_merge(api_env):
    client, c = api_env
    from app.modules.registry import ModuleRegistry

    # 先写入一条已存配置：g2 enabled=False
    module = c.get(ModuleRegistry).get("testmod", 123)
    module.config.set("group_list", {"g2": {"enabled": False, "index": 0}}, auto_save=True)

    resp = client.get("/api/module/testmod/list/groups?bot_id=123")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    by_id = {it["id"]: it for it in data["items"]}
    assert by_id["g1"]["name"] == "群一"
    assert by_id["g1"]["enabled"] is True
    assert by_id["g2"]["enabled"] is False  # 已存配置合并生效
    assert data["mode"] == "all"


async def test_dynamic_api_options_and_fields(api_env):
    client, c = api_env
    resp = client.get("/api/module/testmod/dynamic/providers?bot_id=123")
    assert resp.status_code == 200
    opts = resp.json()
    assert opts["ok"] is True
    assert [o["value"] for o in opts["options"]] == ["a", "b"]

    resp2 = client.get("/api/module/testmod/dynamic/providers/a?bot_id=123")
    fields = resp2.json()
    assert fields["ok"] is True
    assert fields["fields"][0]["key"] == "k1"
