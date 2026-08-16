"""插件自定义配置页测试：检测 / page API / config GET / 无页模块不受影响。"""

import sys

import pytest

from app.modules.registry import ModuleRegistry

MODULE_SRC = '''
from app.modules import BaseModule

class Module(BaseModule):
    name = "页面测试"
    sign = "PageTest"
    description = ""
    permission = "member"
    subscribe = ("message_group",)
    default_config = {"greet": "hi"}
    config_schema = {}

    async def handle(self, event):
        pass
'''

PAGE_HTML = "<html><head><title>P</title></head><body>hello</body></html>"


@pytest.fixture
async def page_env(settings, tmp_path):
    mod_dir = tmp_path / "module" / "modules" / "pagemod"
    (mod_dir / "pages").mkdir(parents=True)
    (mod_dir / "__init__.py").write_text("", encoding="utf-8")
    (mod_dir / "module.py").write_text(MODULE_SRC, encoding="utf-8")
    (mod_dir / "pages" / "index.html").write_text(PAGE_HTML, encoding="utf-8")
    (tmp_path / "module" / "modules" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "module" / "__init__.py").write_text("", encoding="utf-8")

    from app.bootstrap import build_container
    from app.infrastructure.config.config_service import ConfigService
    from app.infrastructure.persistence.database import Database
    from app.webui.app import create_app

    for key in [k for k in sys.modules if k == "module" or k.startswith("module.")]:
        del sys.modules[key]
    sys.path.insert(0, str(tmp_path))
    try:
        c = build_container(settings)
        db = c.get(Database)
        await db.connect()
        await c.get(ConfigService).init()
        await c.get(ModuleRegistry).load_all(bot_id=123)
        app = create_app(c)
        from starlette.testclient import TestClient

        yield TestClient(app), c
        await db.close()
    finally:
        sys.path.remove(str(tmp_path))
        for key in [k for k in sys.modules if k == "module" or k.startswith("module.")]:
            del sys.modules[key]


async def test_has_page_detection(page_env):
    client, c = page_env
    reg = c.get(ModuleRegistry)
    assert reg.module_has_page("pagemod") is True
    assert reg.module_has_page("nonexistent") is False
    # 无 pages 的模块不受影响
    assert reg.module_has_page("__init__") is False


async def test_page_endpoint_returns_html(page_env):
    client, c = page_env
    resp = client.get("/api/module/pagemod/page")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert "hello" in body
    assert 'PLUGIN_MODULE' in body and 'pagemod' in body  # 注入模块名
    assert "PLUGIN_BOT_ID = null" in body  # 未带 bot_id → null

    # 带 bot_id → 注入账号（int，无引号）
    resp2 = client.get("/api/module/pagemod/page?bot_id=123")
    assert "PLUGIN_BOT_ID = 123;" in resp2.text


async def test_page_endpoint_404_without_page(page_env):
    client, c = page_env
    resp = client.get("/api/module/pagemod_other/page")  # 无此模块
    assert resp.status_code == 404


async def test_config_get_endpoint(page_env):
    client, c = page_env
    resp = client.get("/api/module/pagemod/config?bot_id=123")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["module"] == "pagemod"
    assert data["config"]["greet"] == "hi"
