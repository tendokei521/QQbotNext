"""插件自定义配置页测试：检测 / page API / config GET / 无页模块不受影响。"""

import sys

import pytest

from app.plugin.registry import ModuleRegistry

MODULE_SRC = '''
from app.plugin import BaseModule

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
    mod_dir = tmp_path / "plugins" / "pagemod"
    (mod_dir / "pages").mkdir(parents=True)
    (mod_dir / "__init__.py").write_text("", encoding="utf-8")
    (mod_dir / "main.py").write_text(MODULE_SRC, encoding="utf-8")
    (mod_dir / "pages" / "index.html").write_text(PAGE_HTML, encoding="utf-8")
    (tmp_path / "plugins" / "__init__.py").write_text("", encoding="utf-8")

    from app.bootstrap import build_container
    from app.infrastructure.config.config_service import ConfigService
    from app.infrastructure.persistence.database import Database
    from app.webui.app import create_app

    settings.plugins_dir = tmp_path / "plugins"
    try:
        c = build_container(settings)
        db = c.get(Database)
        await db.connect()
        await c.get(ConfigService).init()
        await c.get(ModuleRegistry).load_all(bot_id="acc1")
        app = create_app(c)
        from starlette.testclient import TestClient

        yield TestClient(app), c
        await db.close()
    finally:
        pass
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
    # 未带账号 → null。PLUGIN_ACCOUNT_ID 是正式名（账号 id），
    # PLUGIN_BOT_ID 保留给既有插件页，两者同一个值。
    assert "PLUGIN_ACCOUNT_ID = null" in body
    assert "PLUGIN_BOT_ID = null" in body

    # 带 account_id → 注入账号 id（字符串，带引号）
    resp2 = client.get("/api/module/pagemod/page?account_id=acc1")
    assert 'PLUGIN_ACCOUNT_ID = "acc1";' in resp2.text
    assert 'PLUGIN_BOT_ID = "acc1";' in resp2.text

    # 老插件页仍用 ?bot_id= → 兼容别名，语义同样是账号 id
    resp3 = client.get("/api/module/pagemod/page?bot_id=acc9")
    assert 'PLUGIN_ACCOUNT_ID = "acc9";' in resp3.text


async def test_page_endpoint_404_without_page(page_env):
    client, c = page_env
    resp = client.get("/api/module/pagemod_other/page")  # 无此模块
    assert resp.status_code == 404


async def test_config_get_endpoint(page_env):
    client, c = page_env
    resp = client.get("/api/module/pagemod/config?account_id=acc1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["module"] == "pagemod"
    assert data["config"]["greet"] == "hi"


async def test_installed_module_page_is_served(page_env, tmp_path):
    """修 P0-2：zip 安装的外部模块自定义页也必须可达（旧实现只查内置根）。"""
    import json
    import zipfile

    client, c = page_env
    from app.plugin.registry import ModuleRegistry

    reg = c.get(ModuleRegistry)
    src = tmp_path / "zip_page_src"
    (src / "pages").mkdir(parents=True)
    (src / "main.py").write_text(MODULE_SRC, encoding="utf-8")
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "module.json").write_text(
        json.dumps(
            {"name": "zipmod", "display_name": "外部页模块", "version": "1.0.0", "api_version": 1},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (src / "pages" / "index.html").write_text(PAGE_HTML, encoding="utf-8")

    zip_path = tmp_path / "zipmod.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file in src.rglob("*"):
            if file.is_file():
                archive.write(file, f"zipmod/{file.relative_to(src)}")

    await reg.install_from_zip(zip_path)

    assert reg.module_has_page("zipmod") is True
    resp = client.get("/api/module/zipmod/page?account_id=acc1")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "hello" in resp.text
    assert 'PLUGIN_MODULE = "zipmod"' in resp.text
    assert 'PLUGIN_ACCOUNT_ID = "acc1";' in resp.text

