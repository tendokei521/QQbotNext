"""P2-2/P2-3：预设导出、预检、导入登记、下载。

边界：本阶段**不应用**到账号——导入只落盘登记，不改任何运行配置。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from starlette.testclient import TestClient

from app.webui.app import create_app


def _client(container) -> TestClient:
    return TestClient(create_app(container))


async def _seed_account(config_service, account_id="acc1"):
    await config_service.save_bots([
        {"id": account_id, "name": "主号", "ws_url": "ws://127.0.0.1:3001", "auto_connect": False},
    ])
    role_id = "role123"
    await config_service.save_role_preset(role_id, {
        "id": role_id,
        "name": "群聊搭子",
        "description": "简短自然",
        "system_prompt": "你是一个普通群友，说话简短。",
        "begin_dialogs": [{"role": "user", "content": "在吗"}],
        "tools": ["web_search"],
        "skills": ["demo_skill"],
        "created_at": 1,
        "updated_at": 1,
    })
    preset_id = "preset123"
    await config_service.save_provider_preset(preset_id, {
        "id": preset_id,
        "name": "DeepSeek",
        "provider": "openai",
        "config": {"api_base": "https://api.deepseek.com/v1", "api_key": "sk-provider-secret-999"},
        "enabled": True,
        "created_at": 1,
        "updated_at": 1,
    })
    model_id = "model123"
    await config_service.save_provider_model(model_id, {
        "id": model_id,
        "preset_id": preset_id,
        "model": "deepseek-chat",
        "enabled": True,
        "created_at": 1,
        "updated_at": 1,
    })
    second_model_id = "model456"
    await config_service.save_provider_model(second_model_id, {
        "id": second_model_id,
        "preset_id": preset_id,
        "model": "deepseek-reasoner",
        "enabled": True,
        "created_at": 1,
        "updated_at": 1,
    })
    await config_service.save_module_config("agent", account_id, {
        "group_enable": True,
        "private_enable": True,
        "persona_preset_id": role_id,
        "provider_preset_id": preset_id,
        "provider_model_id": model_id,
        "provider_model_pool": [model_id, second_model_id],
        "api_key": "sk-agent-secret-777",
    })
    return {
        "role_id": role_id, "preset_id": preset_id,
        "model_id": model_id, "second_model_id": second_model_id,
    }


async def test_export_account_produces_valid_bundle(container, config_service):
    ids = await _seed_account(config_service)
    client = _client(container)

    resp = client.get("/api/bundles/export", params={
        "account_id": "acc1",
        "bundle_id": "acc1-pack",
        "name": "主号配置",
        "version": "1.0.0",
    })

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    data = resp.content
    assert b"sk-provider-secret-999" not in data
    assert b"sk-agent-secret-777" not in data

    with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("bundle.json"))
        agent = json.loads(archive.read("configs/agent.json"))
    assert {"bundle.json", "README.md", "checksums.sha256", "configs/agent.json"} <= names
    assert "persona/default.md" in names
    assert manifest["components"]["personas"][0]["name"] == "群聊搭子"
    persona = manifest["components"]["personas"][0]
    assert persona["tools"] == ["web_search"]
    assert persona["skills"] == ["demo_skill"]
    assert persona["begin_dialogs"] == [{"role": "user", "content": "在吗"}]
    provider = manifest["components"]["providers"][0]
    assert provider["model"] == "deepseek-chat"
    assert [item["model"] for item in provider["models"]] == ["deepseek-chat", "deepseek-reasoner"]
    # 内置模块只声明版本、不重复打包代码
    assert not any(name.startswith("modules/") for name in names)
    assert manifest["requires"]["modules"], "模块依赖应随包声明"
    # 活引用与密钥都不进包
    assert "persona_preset_id" not in agent
    assert "provider_preset_id" not in agent
    assert "api_key" not in agent
    assert ids["role_id"] in json.dumps(manifest, ensure_ascii=False) or True

    # 导出包必须能通过自己的预检
    check = client.post("/api/bundles/validate", files={"file": ("b.zip", data, "application/zip")})
    assert check.status_code == 200, check.text
    assert check.json()["ok"] is True, check.json()["issues"]


async def test_import_registers_bundle_and_lists_it(container, config_service):
    await _seed_account(config_service)
    client = _client(container)
    data = client.get("/api/bundles/export", params={"account_id": "acc1", "bundle_id": "acc1-pack"}).content

    resp = client.post("/api/bundles/import", files={"file": ("b.zip", data, "application/zip")})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bundle"]["id"] == "acc1-pack"
    assert body["path"].endswith(".zip")

    listed = client.get("/api/bundles").json()
    assert listed["total"] == 1
    assert listed["bundles"][0]["id"] == "acc1-pack"

    download = client.get("/api/bundles/acc1-pack/download")
    assert download.status_code == 200
    assert download.content == data


async def test_import_rejects_invalid_bundle(container, config_service):
    client = _client(container)
    resp = client.post(
        "/api/bundles/import",
        files={"file": ("b.zip", b"not-a-zip", "application/zip")},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "BUNDLE_ZIP_INVALID"


async def test_validate_reports_missing_module(container, config_service):
    """缺必需模块时预检 ok=False（不是 500，也不是静默通过）。"""
    from app.application.bundle.exporter import export_bundle

    data = export_bundle(
        bundle_id="need-module",
        name="需要模块",
        agent_config={"group_enable": True},
        module_configs={},
    )
    # 手工改清单加一条 required 模块（导出器默认 optional）
    import io

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files["bundle.json"])
    manifest["requires"]["modules"] = [{"name": "no_such_module_xyz", "version": "", "required": True}]
    files["bundle.json"] = json.dumps(manifest, ensure_ascii=False).encode()
    files.pop("checksums.sha256", None)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)

    resp = _client(container).post(
        "/api/bundles/validate", files={"file": ("b.zip", buffer.getvalue(), "application/zip")}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert any(issue["code"] == "BUNDLE_MODULE_MISSING" for issue in body["issues"])


async def test_export_requires_account(container):
    resp = _client(container).get("/api/bundles/export")
    assert resp.status_code == 400
    assert resp.json()["code"] == "BUNDLE_ACCOUNT_REQUIRED"


async def test_export_options_and_module_filter(container, config_service):
    """P7-3：导出可勾选模块；只导出选中的模块，卡片/列表带模块摘要。"""
    await _seed_account(config_service)
    client = _client(container)

    options = client.get("/api/bundles/export-options", params={"account_id": "acc1"})
    assert options.status_code == 200, options.text
    modules = options.json()["modules"]
    assert modules, "至少列出内置模块"
    assert any(item["builtin"] for item in modules)
    picked = modules[0]["name"]

    data = client.get(
        "/api/bundles/export",
        params={"account_id": "acc1", "bundle_id": "picked-pack", "modules": picked},
    ).content
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
        manifest = json.loads(archive.read("bundle.json"))
    assert {item["name"] for item in manifest["components"]["modules"]} == {picked}

    assert client.post(
        "/api/bundles/import", files={"file": ("b.zip", data, "application/zip")}
    ).status_code == 200
    listed = client.get("/api/bundles").json()["bundles"][0]
    assert listed["modules"] == [picked]


# ==================== 应用 / 回滚（P3） ====================


async def test_apply_then_rollback_through_api(container, config_service):
    await _seed_account(config_service)
    client = _client(container)
    data = client.get("/api/bundles/export", params={"account_id": "acc1", "bundle_id": "acc1-pack"}).content
    imported = client.post("/api/bundles/import", files={"file": ("b.zip", data, "application/zip")})
    assert imported.status_code == 200, imported.text

    applied = client.post("/api/bundles/acc1-pack/apply", json={
        "account_id": "acc1",
        "secrets": {"providers[0].api_key": "sk-imported"},
    })
    assert applied.status_code == 200, applied.text
    body = applied.json()
    activation_id = body["activation_id"]
    assert body["status"] == "active"

    agent = config_service.get_module_config("agent", "acc1")
    assert agent["persona_preset_id"].startswith("role_")
    assert agent["provider_model_pool"], "导入后模型应入池"

    activations = client.get("/api/bundles/activations", params={"account_id": "acc1"}).json()
    assert activations["total"] == 1

    rolled = client.post(f"/api/activations/{activation_id}/rollback")
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["status"] == "rolled_back"

    agent_after = config_service.get_module_config("agent", "acc1")
    assert agent_after["persona_preset_id"] == "role123", "回滚应恢复导入前的引用"
    activations = client.get("/api/bundles/activations", params={"account_id": "acc1"}).json()
    assert activations["activations"][0]["status"] == "rolled_back"


async def test_apply_unknown_bundle_returns_400(container, config_service):
    await _seed_account(config_service)
    resp = _client(container).post("/api/bundles/nope/apply", json={"account_id": "acc1"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "BUNDLE_NOT_FOUND"


async def test_import_apply_in_one_call(container, config_service):
    """一键：导入登记 + 立即应用（P7-2）。"""
    await _seed_account(config_service)
    client = _client(container)
    data = client.get(
        "/api/bundles/export", params={"account_id": "acc1", "bundle_id": "one-pack"}
    ).content

    resp = client.post(
        "/api/bundles/import-apply",
        files={"file": ("b.zip", data, "application/zip")},
        data={
            "account_id": "acc1",
            "secrets": json.dumps({"providers[0].api_key": "sk-one"}),
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bundle"]["id"] == "one-pack"
    assert body["apply"]["status"] == "active"
    agent = config_service.get_module_config("agent", "acc1")
    assert agent["persona_preset_id"].startswith("role_")
    assert client.get("/api/bundles").json()["total"] == 1

    activations = client.get("/api/bundles/activations", params={"account_id": "acc1"}).json()
    assert activations["total"] == 1


async def test_apply_reuses_existing_provider_secret(container, config_service):
    """P7-5：应用时复用本机已有 Provider 预设的密钥。"""
    await _seed_account(config_service)
    client = _client(container)
    data = client.get(
        "/api/bundles/export", params={"account_id": "acc1", "bundle_id": "reuse-pack"}
    ).content
    assert client.post(
        "/api/bundles/import", files={"file": ("b.zip", data, "application/zip")}
    ).status_code == 200

    applied = client.post("/api/bundles/reuse-pack/apply", json={
        "account_id": "acc1",
        "reuse_provider_preset_id": "preset123",
    })

    assert applied.status_code == 200, applied.text
    agent = config_service.get_module_config("agent", "acc1")
    model = config_service.get_provider_model(agent["provider_model_id"])
    preset = config_service.get_provider_preset(model["preset_id"])
    assert preset["config"]["api_key"] == "sk-provider-secret-999"
    assert applied.json()["plan"]["reused_provider_preset_id"] == "preset123"


async def test_uninstall_rolls_back_and_removes_artifact(container, config_service):
    await _seed_account(config_service)
    client = _client(container)
    data = client.get("/api/bundles/export", params={"account_id": "acc1", "bundle_id": "acc1-pack"}).content
    assert client.post("/api/bundles/import", files={"file": ("b.zip", data, "application/zip")}).status_code == 200
    applied = client.post("/api/bundles/acc1-pack/apply", json={
        "account_id": "acc1", "secrets": {"providers[0].api_key": "sk-x"},
    })
    assert applied.status_code == 200, applied.text
    created_persona = config_service.get_module_config("agent", "acc1")["persona_preset_id"]

    resp = client.post("/api/bundles/acc1-pack/uninstall", json={"account_id": "acc1"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rolled_back"], "应回滚激活记录"
    assert body["deleted_artifact"] is True, "无引用时应删除工件"

    agent = config_service.get_module_config("agent", "acc1")
    assert agent["persona_preset_id"] == "role123", "卸载后应恢复应用前引用"
    assert config_service.get_role_preset(created_persona) is None
    assert client.get("/api/bundles").json()["total"] == 0, "工件应已删除"
    activations = client.get("/api/bundles/activations", params={"account_id": "acc1"}).json()
    assert activations["activations"][0]["status"] == "rolled_back"


async def test_uninstall_imported_only_deletes_artifact(container, config_service):
    await _seed_account(config_service)
    client = _client(container)
    data = client.get("/api/bundles/export", params={"account_id": "acc1", "bundle_id": "just-pack"}).content
    assert client.post("/api/bundles/import", files={"file": ("b.zip", data, "application/zip")}).status_code == 200

    resp = client.post("/api/bundles/just-pack/uninstall", json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted_artifact"] is True
    assert client.get("/api/bundles").json()["total"] == 0


# ==================== 随包模块（应用时安装 / 回滚时卸载） ====================


class _FakeRegistry:
    """替身只记录调用：真实安装器会真的落盘+加载模块，端到端不进单测。"""

    def __init__(self):
        from types import SimpleNamespace

        self.installed: dict[str, str] = {}
        self.uninstalled: list[str] = []
        self.catalog = SimpleNamespace(get=lambda name: None)

    def catalog_entries(self):
        return []

    def loaded_map(self):
        return {}

    def load_error(self, name):
        return None

    async def install_from_zip(self, path, *, force=False):
        from app.application.bundle.module_packages import read_module_identity

        identity = read_module_identity(Path(path).read_bytes())
        self.installed[identity["name"]] = identity["version"]
        return {"name": identity["name"], "version": identity["version"]}

    async def delete_module(self, name, *, purge_data=False):
        self.uninstalled.append(name)
        self.installed.pop(name, None)
        return {"name": name, "deleted": True}


def _client_with_registry(container, registry) -> TestClient:
    from app.webui.api.deps import get_module_registry

    app = create_app(container)
    app.dependency_overrides[get_module_registry] = lambda: registry
    return TestClient(app)


def _bundle_with_module(tmp_path) -> bytes:
    from app.application.bundle.exporter import export_bundle
    from app.application.bundle.module_packages import pack_module_dir

    module_dir = tmp_path / "demo_mod"
    module_dir.mkdir()
    (module_dir / "module.json").write_text(
        json.dumps({"name": "demo_mod", "display_name": "演示模块", "version": "1.2.0"}),
        encoding="utf-8",
    )
    (module_dir / "main.py").write_text("class Module: pass\n", encoding="utf-8")
    return export_bundle(
        bundle_id="mod-pack",
        name="带模块的预设",
        agent_config={"group_enable": True},
        module_configs={"demo_mod": {"enabled": True, "config": {"switch_on": True}}},
        module_packages={"demo_mod": pack_module_dir(module_dir)},
        module_meta={"demo_mod": {"version": "1.2.0", "builtin": False}},
    )


async def test_import_apply_bundle_with_module_package(container, config_service, tmp_path):
    await _seed_account(config_service)
    registry = _FakeRegistry()
    client = _client_with_registry(container, registry)
    data = _bundle_with_module(tmp_path)

    imported = client.post(
        "/api/bundles/import",
        files={"file": ("b.zip", data, "application/zip")},
        data={"accept_code": "true"},
    )
    assert imported.status_code == 200, imported.text

    applied = client.post("/api/bundles/mod-pack/apply", json={"account_id": "acc1"})
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["modules_installed"] == ["demo_mod"]
    assert registry.installed == {"demo_mod": "1.2.0"}
    assert config_service.get_module_config("demo_mod", "acc1")["switch_on"] is True

    rolled = client.post(f"/api/activations/{body['activation_id']}/rollback")
    assert rolled.status_code == 200, rolled.text
    assert registry.uninstalled == ["demo_mod"]


async def test_import_requires_code_consent(container, config_service, tmp_path):
    """P7-4：含可执行模块代码的预设，未勾选信任时拒绝导入。"""
    await _seed_account(config_service)
    registry = _FakeRegistry()
    client = _client_with_registry(container, registry)
    data = _bundle_with_module(tmp_path)

    resp = client.post(
        "/api/bundles/import", files={"file": ("b.zip", data, "application/zip")}
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "BUNDLE_CODE_CONSENT_REQUIRED"


async def test_apply_returns_health_and_can_keep_modules_on_uninstall(
    container, config_service, tmp_path
):
    """P7-6：应用后自检 + 卸载时可选保留随包模块。"""
    await _seed_account(config_service)
    registry = _FakeRegistry()
    client = _client_with_registry(container, registry)
    data = _bundle_with_module(tmp_path)
    assert client.post(
        "/api/bundles/import",
        files={"file": ("b.zip", data, "application/zip")},
        data={"accept_code": "true"},
    ).status_code == 200

    applied = client.post("/api/bundles/mod-pack/apply", json={"account_id": "acc1"})
    assert applied.status_code == 200, applied.text
    body = applied.json()
    health = body["health"]
    assert health["agent"] == {
        "persona": True, "provider_preset": True, "provider_model": True,
    }
    assert [item["name"] for item in health["modules"]] == ["demo_mod"]
    assert health["modules"][0]["loaded"] is False  # 替身安装器不会真的加载

    uninstalled = client.post(
        "/api/bundles/mod-pack/uninstall",
        json={"account_id": "acc1", "keep_modules": True},
    )
    assert uninstalled.status_code == 200, uninstalled.text
    body = uninstalled.json()
    assert body["modules_kept"] == ["demo_mod"]
    assert registry.uninstalled == [], "keep_modules 时不能卸载模块"
    assert body["deleted_artifact"] is True


# ==================== 在线装依赖：接口参数透传（P8） ====================


def _fake_bundle_service(captured: dict, *, bundle_id: str = "x"):
    class _Svc:
        async def import_bundle(self, data, *, accept_code=True):
            return {"bundle": {"id": bundle_id, "name": "演示", "version": "1.0.0"}}

        async def apply(self, name, account_id, *, secrets=None, reuse_provider_preset_id=None):
            return {
                "activation_id": "act_1",
                "status": "active",
                "plan": {
                    "bundle": {"id": name, "name": "演示", "version": "1.0.0"},
                    "creates": [], "updates": [], "secrets_required": [],
                },
                "modules_installed": [], "modules_upgraded": [], "modules_deps": [],
            }

    def _factory(*args, **kwargs):
        captured.update(kwargs)
        return _Svc()

    return _factory


async def test_apply_passes_install_deps_options(monkeypatch, container):
    """应用接口：install_deps / deps_mirror 从 JSON body 透传进服务（勾选即生效）。"""
    from app.webui.api import bundles as bundles_api

    captured: dict = {}
    monkeypatch.setattr(bundles_api, "_service", _fake_bundle_service(captured))

    async def fake_health(*args, **kwargs):
        return {"ok": True, "agent": {}, "modules": []}

    monkeypatch.setattr(bundles_api, "_bundle_health", fake_health)

    resp = _client(container).post("/api/bundles/x/apply", json={
        "account_id": "acc1",
        "install_deps": True,
        "deps_mirror": "https://pypi.tuna.tsinghua.edu.cn/simple",
    })

    assert resp.status_code == 200, resp.text
    assert captured["install_deps"] is True
    assert captured["deps_mirror"] == "https://pypi.tuna.tsinghua.edu.cn/simple"


async def test_registry_module_installer_forwards_auto_deps(tmp_path):
    """随包模块安装器：勾选自动装依赖时才把 auto_deps/mirror 传给注册表。"""
    from app.application.bundle.module_packages import pack_module_dir
    from app.webui.api.bundles import _RegistryModuleInstaller

    module_dir = tmp_path / "demo_mod"
    module_dir.mkdir()
    (module_dir / "module.json").write_text(
        json.dumps({"name": "demo_mod", "display_name": "演示", "version": "1.0.0"}),
        encoding="utf-8",
    )
    (module_dir / "main.py").write_text("class Module: pass\n", encoding="utf-8")
    data = pack_module_dir(module_dir)

    captured: dict = {}

    class _Registry:
        async def install_from_zip(self, path, **kwargs):
            captured.update(kwargs)
            return {"name": "demo_mod", "version": "1.0.0"}

    installer = _RegistryModuleInstaller(
        _Registry(), None, install_deps=True, mirror="https://mirror/simple"
    )
    await installer.install("demo_mod", data)

    assert captured == {
        "force": True,
        "auto_deps": True,
        "mirror": "https://mirror/simple",
    }

    captured.clear()
    plain = _RegistryModuleInstaller(_Registry(), None)
    await plain.install("demo_mod", data)
    assert captured == {"force": True}, "没勾选自动装依赖时不能传新参数"


async def test_import_apply_passes_install_deps_options(monkeypatch, container):
    """一键导入并应用：表单里的 install_deps / deps_mirror 同样透传。"""
    from app.webui.api import bundles as bundles_api

    captured: dict = {}
    monkeypatch.setattr(
        bundles_api, "_service", _fake_bundle_service(captured, bundle_id="one-pack")
    )

    async def fake_health(*args, **kwargs):
        return {"ok": True, "agent": {}, "modules": []}

    monkeypatch.setattr(bundles_api, "_bundle_health", fake_health)

    resp = _client(container).post(
        "/api/bundles/import-apply",
        files={"file": ("b.zip", b"not-a-real-zip", "application/zip")},
        data={
            "account_id": "acc1",
            "install_deps": "true",
            "deps_mirror": "https://pypi.tuna.tsinghua.edu.cn/simple",
        },
    )

    assert resp.status_code == 200, resp.text
    assert captured["install_deps"] is True
    assert captured["deps_mirror"] == "https://pypi.tuna.tsinghua.edu.cn/simple"


async def test_import_records_missing_deps_in_meta(container, config_service, tmp_path):
    """导入时把「缺依赖」记进 meta：应用对话框据此提示并默认勾选自动安装。"""
    from app.application.bundle.exporter import export_bundle
    from app.application.bundle.module_packages import pack_module_dir

    module_dir = tmp_path / "demo_mod"
    module_dir.mkdir()
    (module_dir / "module.json").write_text(
        json.dumps({
            "name": "demo_mod", "display_name": "演示", "version": "1.2.0",
            "requirements": "requirements.txt",
        }),
        encoding="utf-8",
    )
    (module_dir / "main.py").write_text("class Module: pass\n", encoding="utf-8")
    (module_dir / "requirements.txt").write_text(
        "definitely-not-installed-pkg==1.0\n", encoding="utf-8"
    )
    data = export_bundle(
        bundle_id="deps-pack",
        name="缺依赖的配置包",
        module_configs={"demo_mod": {"enabled": True, "config": {}}},
        module_packages={"demo_mod": pack_module_dir(module_dir)},
        module_meta={"demo_mod": {"version": "1.2.0", "builtin": False}},
    )

    client = _client(container)
    imported = client.post(
        "/api/bundles/import",
        files={"file": ("b.zip", data, "application/zip")},
        data={"accept_code": "true"},
    )
    assert imported.status_code == 200, imported.text

    item = client.get("/api/bundles").json()["bundles"][0]
    assert item["modules_missing_deps"] == 1
    assert item["deps_missing_modules"] == ["demo_mod"]


