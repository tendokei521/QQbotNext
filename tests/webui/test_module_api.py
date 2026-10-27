"""模块管理 API（C5）测试：预检 / 安装 / 删除 / 软卸载 / 恢复 + WS 契约。

覆盖：
- validate-zip 200（合法包返回名称/版本/目标目录/冲突）与 400（非 zip）；
- install-zip 200，安装后编目/列表可见（source=installed、builtin=False）；
- DELETE：内置 403、不存在 404、第三方 200；
- uninstall：不存在 404、存在 200 且进入 uninstalled 状态；
- WS 广播同时带 bot_id 与 account_id（🔗1 跨账号串台修复的契约面）。
"""

from __future__ import annotations

import io
import json
import sys
import zipfile

import pytest
from starlette.testclient import TestClient

from app.bootstrap import build_container
from app.infrastructure.config.config_service import ConfigService
from app.infrastructure.persistence.database import Database
from app.plugin.registry import ModuleRegistry
from app.webui.app import create_app

MODULE_SRC = '''
from app.plugin import BaseModule


class Module(BaseModule):
    name = "演示模块"
    sign = "Demo"
    description = "测试用模块"
    permission = "member"
    subscribe = ("message_group",)
    default_config = {"greet": "hi"}
    config_schema = {}


    async def handle(self, event):
        pass
'''


def _module_json(name: str, version: str = "1.2.3", display_name: str = "演示模块") -> str:
    return json.dumps(
        {
            "name": name,
            "display_name": display_name,
            "version": version,
            "api_version": 1,
            "entry": "main.py",
        },
        ensure_ascii=False,
    )


def _make_zip(*, name: str = "demo", version: str = "1.2.3", wrapper: bool = True) -> bytes:
    """构造一个插件 zip；wrapper=True 时带一层顶层目录（GitHub zip 布局）。"""
    prefix = f"{name}-main/" if wrapper else ""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{prefix}module.json", _module_json(name, version))
        zf.writestr(f"{prefix}main.py", MODULE_SRC)
    return buf.getvalue()


def _zip_file(data: bytes, filename: str = "demo.zip") -> dict:
    return {"file": (filename, data, "application/zip")}


async def _make_app(settings, tmp_path):
    """临时内置模块目录 + 容器 + WebUI app（参考 tests/plugin/test_plugin_page.py）。"""
    builtin = tmp_path / "plugins"
    builtin.mkdir(parents=True, exist_ok=True)
    (builtin / "__init__.py").write_text("", encoding="utf-8")
    demo = builtin / "builtindemo"
    demo.mkdir()
    (demo / "module.json").write_text(_module_json("builtindemo"), encoding="utf-8")
    (demo / "main.py").write_text(MODULE_SRC, encoding="utf-8")
    # 装坏的模块：导入即抛错 → 编目 status=error，但不应影响其它模块（C3 验收）
    broken = builtin / "brokenmod"
    broken.mkdir()
    (broken / "module.json").write_text(_module_json("brokenmod"), encoding="utf-8")
    (broken / "main.py").write_text("raise RuntimeError('boom-load')\n", encoding="utf-8")
    # 缺清单的内置模块：WARN + 类属性兜底，编目 status=manifest_missing（C4）
    nodoc = builtin / "nodocmod"
    nodoc.mkdir()
    (nodoc / "main.py").write_text(MODULE_SRC, encoding="utf-8")
    settings.plugins_dir = builtin

    c = build_container(settings)
    db = c.get(Database)
    await db.connect()
    await c.get(ConfigService).init()
    registry = c.get(ModuleRegistry)
    await registry.load_all(bot_id="acc1")
    app = create_app(c)
    return c, db, TestClient(app)


@pytest.fixture
async def webui_env(settings, tmp_path):
    c, db, client = await _make_app(settings, tmp_path)
    try:
        yield client, c
    finally:
        await db.close()
        for key in [k for k in sys.modules if k.startswith("qqbot_plugin_")]:
            del sys.modules[key]


# ==================== 编目合并（装坏/缺清单也要可见） ====================


async def test_load_error_module_visible(webui_env):
    client, _ = webui_env
    modules = client.get("/api/modules", params={"account_id": "acc1"}).json()
    assert "brokenmod" in modules
    broken = modules["brokenmod"]
    assert broken["status"] == "error"
    assert broken["error"]  # 用户可读的加载失败原因
    assert broken["builtin"] is True
    assert broken["source"] == "builtin"
    # 坏模块不影响其它模块
    assert modules["builtindemo"]["status"] == "ok"


async def test_manifest_missing_module_visible(webui_env):
    client, _ = webui_env
    modules = client.get("/api/modules", params={"account_id": "acc1"}).json()
    assert "nodocmod" in modules
    nodoc = modules["nodocmod"]
    assert nodoc["manifest_missing"] is True
    assert nodoc["builtin"] is True
    assert nodoc["status"] == "ok"


async def test_modules_list_shows_preset_origin(webui_env):
    """预设装的模块要能在模块页显示「来自预设 xxx」（来源台账）。"""
    client, container = webui_env
    from app.application.bundle.origins import record_origin

    db = container.get(Database)
    await record_origin(
        db, "builtindemo", bundle_id="demo-pack", bundle_name="群聊搭子",
        bundle_version="1.2.0", account_id="acc1", action="installed",
    )

    modules = client.get("/api/modules", params={"account_id": "acc1"}).json()

    origin = modules["builtindemo"]["origin"]
    assert origin["bundle_id"] == "demo-pack"
    assert origin["bundle_name"] == "群聊搭子"
    assert origin["bundle_version"] == "1.2.0"
    assert origin["action"] == "installed"
    # 没有来源的模块不应出现空 origin 字段
    assert "origin" not in modules["brokenmod"]


# ==================== validate-zip ====================


async def test_validate_zip_ok(webui_env):
    client, _ = webui_env
    resp = client.post("/api/modules/validate-zip", files=_zip_file(_make_zip()))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    plugin = body["plugin"]
    assert plugin["name"] == "demo"
    assert plugin["version"] == "1.2.3"
    assert plugin["display_name"] == "演示模块"
    assert plugin["source"] == "installed"
    assert plugin["target"].endswith("demo")
    assert plugin["conflict"] is False


async def test_validate_zip_rejects_non_zip(webui_env):
    client, _ = webui_env
    resp = client.post("/api/modules/validate-zip", files=_zip_file(b"not a zip", "bad.zip"))
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["status"] == "error"
    assert body.get("hint")


async def test_validate_zip_reports_builtin_conflict(webui_env):
    client, _ = webui_env
    resp = client.post("/api/modules/validate-zip", files=_zip_file(_make_zip(name="builtindemo")))
    assert resp.status_code == 200, resp.text
    plugin = resp.json()["plugin"]
    assert plugin["conflict"] is True
    assert plugin["builtin_conflict"] is True
    assert plugin["conflict_reason"]


async def test_install_zip_rejects_builtin_name(webui_env):
    client, _ = webui_env
    resp = client.post("/api/modules/install-zip", files=_zip_file(_make_zip(name="builtindemo")))
    assert resp.status_code == 400, resp.text
    body = resp.json()
    combined = body.get("message", "") + body.get("hint", "")
    assert "内置" in combined


# ==================== install-zip ====================


async def test_install_zip_appears_in_catalog(webui_env):
    client, _ = webui_env
    resp = client.post("/api/modules/install-zip", files=_zip_file(_make_zip()))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["plugin"]["module_name"] == "demo"

    listing = client.get("/api/modules", params={"account_id": "acc1"})
    assert listing.status_code == 200
    modules = listing.json()
    assert "demo" in modules
    demo = modules["demo"]
    assert demo["source"] == "installed"
    assert demo["builtin"] is False
    assert demo["version"] == "1.2.3"
    assert demo["status"] == "ok"
    # 编目新增字段齐备（前端卡片/菜单依赖）
    assert demo["path"]
    assert demo["manifest_missing"] is False
    # 内置模块也在（source 归一为 builtin）
    assert modules["builtindemo"]["source"] == "builtin"
    assert modules["builtindemo"]["builtin"] is True


async def test_install_zip_requires_force_on_conflict(webui_env):
    client, _ = webui_env
    assert client.post("/api/modules/install-zip", files=_zip_file(_make_zip())).status_code == 200
    # 同名再次安装：无 force → 400 + hint
    resp = client.post("/api/modules/install-zip", files=_zip_file(_make_zip()))
    assert resp.status_code == 400, resp.text
    assert resp.json().get("hint")
    # force=True → 覆盖安装成功
    resp2 = client.post(
        "/api/modules/install-zip",
        files=_zip_file(_make_zip(version="1.3.0")),
        data={"force": "true"},
    )
    assert resp2.status_code == 200, resp2.text


# ==================== update（覆盖安装） ====================


async def test_update_zip_overwrites_version(webui_env):
    client, _ = webui_env
    assert client.post("/api/modules/install-zip", files=_zip_file(_make_zip())).status_code == 200
    resp = client.post(
        "/api/modules/demo/update", files=_zip_file(_make_zip(version="2.0.0"))
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["plugin"]["version"] == "2.0.0"
    assert body["plugin"]["overwritten"] is True
    modules = client.get("/api/modules", params={"account_id": "acc1"}).json()
    assert modules["demo"]["version"] == "2.0.0"


# ==================== DELETE ====================


async def test_delete_builtin_forbidden(webui_env):
    client, _ = webui_env
    resp = client.delete("/api/modules/builtindemo")
    assert resp.status_code == 403, resp.text
    assert resp.json()["status"] == "error"


async def test_delete_missing_404(webui_env):
    client, _ = webui_env
    resp = client.delete("/api/modules/no_such_module")
    assert resp.status_code == 404, resp.text


async def test_delete_installed_module_ok(webui_env):
    client, _ = webui_env
    assert client.post("/api/modules/install-zip", files=_zip_file(_make_zip())).status_code == 200
    resp = client.delete(
        "/api/modules/demo", params={"purge_data": "false", "account_id": "acc1"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success"
    # 删除后列表里不再有该模块
    modules = client.get("/api/modules", params={"account_id": "acc1"}).json()
    assert "demo" not in modules


# ==================== uninstall / install（恢复） ====================


async def test_uninstall_missing_404(webui_env):
    client, _ = webui_env
    resp = client.post("/api/modules/no_such_module/uninstall")
    assert resp.status_code == 404, resp.text


async def test_uninstall_then_restore(webui_env):
    client, _ = webui_env
    assert client.post("/api/modules/install-zip", files=_zip_file(_make_zip())).status_code == 200

    resp = client.post("/api/modules/demo/uninstall", params={"account_id": "acc1"})
    assert resp.status_code == 200, resp.text
    modules = client.get("/api/modules", params={"account_id": "acc1"}).json()
    assert modules["demo"]["status"] == "uninstalled"

    restored = client.post("/api/modules/demo/install", params={"account_id": "acc1"})
    assert restored.status_code == 200, restored.text
    modules2 = client.get("/api/modules", params={"account_id": "acc1"}).json()
    assert modules2["demo"]["status"] == "ok"


# ==================== WS 契约：bot_id + account_id ====================


async def test_authority_broadcast_has_account_id(webui_env, monkeypatch):
    client, _ = webui_env
    from app.webui.ws import manager

    captured: list[dict] = []

    async def fake_broadcast(message: str) -> None:
        captured.append(json.loads(message))

    monkeypatch.setattr(manager, "broadcast", fake_broadcast)
    resp = client.post(
        "/api/module/builtindemo/toggle",
        params={"account_id": "acc1"},
        data={"enabled": "false"},
    )
    assert resp.status_code == 200, resp.text
    payload = next(item for item in captured if item.get("type") == "module_authority_updated")
    assert payload["bot_id"] == "acc1"
    assert payload["account_id"] == "acc1"
    assert payload["enabled"] is False


async def test_config_broadcast_has_account_id(webui_env, monkeypatch):
    client, _ = webui_env
    from app.webui.ws import manager

    captured: list[dict] = []

    async def fake_broadcast(message: str) -> None:
        captured.append(json.loads(message))

    monkeypatch.setattr(manager, "broadcast", fake_broadcast)
    resp = client.post(
        "/api/module/builtindemo/config",
        params={"account_id": "acc1"},
        json={"greet": "hello"},
    )
    assert resp.status_code == 200, resp.text
    payload = next(item for item in captured if item.get("type") == "module_config_updated")
    assert payload["bot_id"] == "acc1"
    assert payload["account_id"] == "acc1"
    assert payload["config"]["greet"] == "hello"


# ==================== 广播 modules_reloaded ====================


async def test_install_broadcasts_modules_reloaded(webui_env, monkeypatch):
    client, _ = webui_env
    from app.webui.ws import manager

    captured: list[dict] = []

    async def fake_broadcast(message: str) -> None:
        captured.append(json.loads(message))

    monkeypatch.setattr(manager, "broadcast", fake_broadcast)
    assert client.post("/api/modules/install-zip", files=_zip_file(_make_zip())).status_code == 200
    assert any(item.get("type") == "modules_reloaded" for item in captured)
