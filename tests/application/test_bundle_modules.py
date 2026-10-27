"""预设迁移：模块代码随包 + 角色卡字段 + 模型池全量（导出侧）。

对应设计稿 §5.3「模块：引用 + 可选 zip」与 v1 的三处遗漏：
- 人格只带 system_prompt（tools/skills/begin_dialogs 丢了）；
- 模型池只带第一个；
- 第三方模块只声明依赖、不带代码，换机后模块不会自己出现。
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.application.bundle.applier import BundleApplier
from app.application.bundle.errors import BundleError
from app.application.bundle.exporter import export_bundle
from app.application.bundle.manifest import normalize_manifest
from app.application.bundle.module_packages import (
    pack_module_dir,
    read_module_identity,
)
from app.application.bundle.validator import validate_bytes


def _bundle_with_package(tmp_path, *, declared_name="demo_mod", required=True):
    package = pack_module_dir(_write_module(tmp_path))
    return export_bundle(
        bundle_id="acc1-pack",
        name="主号配置",
        module_configs={"demo_mod": {"enabled": True, "config": {"switch": True}}},
        module_packages={"demo_mod": package},
        module_meta={"demo_mod": {"version": "1.2.0", "builtin": False}},
    ), package


def test_validate_accepts_bundled_module_as_installable(tmp_path):
    data, _package = _bundle_with_package(tmp_path)

    report = validate_bytes(data, installed_modules={})

    assert report["ok"] is True, report["issues"]
    assert report["summary"]["modules_bundled"] == 1
    assert report["summary"]["modules_to_install"] == 1
    assert not any(issue["code"] == "BUNDLE_MODULE_MISSING" for issue in report["issues"])


def test_validate_reports_package_name_mismatch(tmp_path):
    data, package = _bundle_with_package(tmp_path)
    # 把包内容换成另一个模块（保留原清单路径），清单声明与包内 name 不一致
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = _write_module(other_root, name="other_mod")
    replacement = pack_module_dir(other)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files["modules/demo_mod-1.2.0.zip"] = replacement
    files.pop("checksums.sha256", None)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)

    report = validate_bytes(buffer.getvalue(), installed_modules={})
    assert report["ok"] is False
    assert any(
        issue["code"] == "BUNDLE_MODULE_PACKAGE_INVALID" for issue in report["issues"]
    ), report["issues"]


def test_validate_reports_missing_package_file(tmp_path):
    data, _package = _bundle_with_package(tmp_path)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files.pop("modules/demo_mod-1.2.0.zip")
    files.pop("checksums.sha256", None)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)

    report = validate_bytes(buffer.getvalue(), installed_modules={})
    assert any(issue["code"] == "BUNDLE_REF_MISSING" for issue in report["issues"])


def test_validate_marks_outdated_module_as_upgrade(tmp_path):
    data, _package = _bundle_with_package(tmp_path)

    report = validate_bytes(data, installed_modules={"demo_mod": "0.9.0"})

    assert report["ok"] is True, report["issues"]
    assert any(issue["code"] == "BUNDLE_MODULE_UPGRADE" for issue in report["issues"])
    assert report["summary"]["modules_to_install"] == 1


def _write_module(root, name="demo_mod", version="1.2.0", files=None):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "module.json").write_text(
        json.dumps({"name": name, "display_name": "演示模块", "version": version}),
        encoding="utf-8",
    )
    (directory / "main.py").write_text("class Module: pass\n", encoding="utf-8")
    for relative, text in (files or {}).items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return directory


def _open(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(data))


def test_pack_module_dir_is_deterministic_and_installable_shape(tmp_path):
    directory = _write_module(tmp_path, files={"__pycache__/x.pyc": "noise", "lib/helper.py": "x = 1\n"})
    first = pack_module_dir(directory)
    second = pack_module_dir(directory)

    assert first == second, "同目录两次打包必须字节一致（便于校验和/去重）"
    with _open(first) as archive:
        names = set(archive.namelist())
    assert {"module.json", "main.py", "lib/helper.py"} <= names
    assert not any("__pycache__" in name for name in names), "噪声目录不能进包"
    identity = read_module_identity(first)
    assert identity["name"] == "demo_mod"
    assert identity["version"] == "1.2.0"


def test_pack_module_dir_rejects_non_module_directory(tmp_path):
    (tmp_path / "not_a_module").mkdir()
    with pytest.raises(BundleError) as exc:
        pack_module_dir(tmp_path / "not_a_module")
    assert exc.value.code == "BUNDLE_MODULE_PACKAGE_INVALID"


def test_third_party_module_code_travels_with_bundle(tmp_path):
    directory = _write_module(tmp_path)
    package = pack_module_dir(directory)

    data = export_bundle(
        bundle_id="acc1-pack",
        name="主号配置",
        agent_config={"group_enable": True},
        module_configs={"demo_mod": {"enabled": True, "config": {"switch": True}}},
        module_packages={"demo_mod": package},
        module_meta={"demo_mod": {"version": "1.2.0", "builtin": False}},
    )

    with _open(data) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("bundle.json"))
        inner = archive.read("modules/demo_mod-1.2.0.zip")
    assert "modules/demo_mod-1.2.0.zip" in names
    assert read_module_identity(inner)["name"] == "demo_mod"

    requires = {item["name"]: item for item in manifest["requires"]["modules"]}
    assert requires["demo_mod"]["required"] is True
    assert requires["demo_mod"]["source"] == "modules/demo_mod-1.2.0.zip"
    assert requires["demo_mod"]["version"] == ">=1.2.0"
    assert manifest["components"]["modules"][0]["version"] == "1.2.0"


def test_builtin_module_is_declared_but_not_packed(tmp_path):
    data = export_bundle(
        bundle_id="acc1-pack",
        name="主号配置",
        module_configs={"llm_memory": {"enabled": False, "config": {"memory_enable": True}}},
        module_meta={"llm_memory": {"version": "1.0.0", "builtin": True}},
    )
    with _open(data) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("bundle.json"))
    assert not any(name.startswith("modules/") for name in names), "内置模块不重复打包"
    requires = manifest["requires"]["modules"][0]
    assert requires["required"] is False, "内置模块缺失只告警，不阻断导入"
    assert requires["source"] == ""


def test_persona_card_fields_and_full_model_pool_are_exported():
    data = export_bundle(
        bundle_id="acc1-pack",
        name="主号配置",
        persona={
            "name": "群聊搭子",
            "text": "你是一个普通群友。",
            "description": "简短自然",
            "begin_dialogs": [{"role": "user", "content": "在吗"}],
            "tools": ["web_search"],
            "skills": None,
        },
        provider={
            "provider": "openai",
            "api_base": "https://api.deepseek.com/v1",
            "api_key": "sk-provider-secret-123",
            "model": "deepseek-chat",
            "preset_name": "DeepSeek",
            "config": {"api_base": "https://api.deepseek.com/v1", "retry": 2},
            "models": [
                {"model": "deepseek-chat", "provider_type": "chat",
                 "config": {"temperature": 0.7}},
                {"model": "deepseek-reasoner", "provider_type": "chat", "config": {}},
            ],
        },
    )
    assert b"sk-provider-secret-123" not in data
    with _open(data) as archive:
        manifest = json.loads(archive.read("bundle.json"))

    persona = manifest["components"]["personas"][0]
    assert persona["begin_dialogs"] == [{"role": "user", "content": "在吗"}]
    assert persona["tools"] == ["web_search"]
    assert persona["skills"] is None
    assert persona["description"] == "简短自然"

    provider = manifest["components"]["providers"][0]
    assert [item["model"] for item in provider["models"]] == [
        "deepseek-chat", "deepseek-reasoner",
    ]
    assert provider["models"][0]["config"]["temperature"] == 0.7
    assert provider["config"]["retry"] == 2
    assert "api_key" not in provider["config"]
    keys = {item["key"] for item in manifest["secrets"]}
    assert "providers[0].api_key" in keys


def test_export_is_still_deterministic_with_module_package(tmp_path):
    package = pack_module_dir(_write_module(tmp_path))
    kwargs = dict(
        bundle_id="acc1-pack",
        name="主号配置",
        module_configs={"demo_mod": {"enabled": True, "config": {}}},
        module_packages={"demo_mod": package},
        module_meta={"demo_mod": {"version": "1.2.0", "builtin": False}},
    )
    assert export_bundle(**kwargs) == export_bundle(**kwargs)


# ==================== 应用侧（模块安装 + 密钥写回 + 角色卡 + 模型池） ====================


class _FakeInstaller:
    """记录调用即可：真实安装器会加载模块代码，单测不碰执行面。"""

    def __init__(self):
        self.installed: dict[str, str] = {}
        self.uninstalled: list[str] = []

    async def install(self, name: str, data: bytes) -> dict:
        identity = read_module_identity(data)
        self.installed[name] = identity["version"]
        return {"name": name, "version": identity["version"]}

    async def uninstall(self, name: str) -> None:
        self.uninstalled.append(name)
        self.installed.pop(name, None)


async def _seed(container, config_service, account_id="acc1"):
    await config_service.save_bots([
        {"id": account_id, "name": "主号", "ws_url": "ws://127.0.0.1:3001"},
    ])


def _manifest_with_module(**overrides) -> dict:
    raw = {
        "schema": "qqbotnext.bundle/1",
        "id": "demo-pack",
        "name": "演示预设",
        "version": "1.2.0",
        "framework": {"api_version": 1},
        "requires": {"modules": [{
            "name": "demo_mod", "version": ">=1.2.0", "required": True,
            "source": "modules/demo_mod-1.2.0.zip",
        }]},
        "components": {
            "personas": [{
                "source": "persona/default.md", "name": "群聊搭子",
                "description": "简短自然",
                "begin_dialogs": [{"role": "user", "content": "在吗"}],
                "tools": ["web_search"],
                "skills": ["demo_skill"],
            }],
            "providers": [{
                "provider": "openai", "api_base": "https://api.deepseek.com/v1",
                "model": "deepseek-chat", "preset_name": "DeepSeek",
                "config": {"retry": 2},
                "models": [
                    {"model": "deepseek-chat", "config": {"temperature": 0.7}},
                    {"model": "deepseek-reasoner", "config": {}},
                ],
            }],
            "agent": [{"source": "configs/agent.json"}],
            "modules": [{
                "name": "demo_mod", "enabled": True,
                "source": "configs/module-demo_mod.json",
            }],
        },
        "secrets": [
            {"key": "providers[0].api_key", "label": "Provider Key", "required": True},
            {"key": "providers[0].timeout", "label": "连接超时", "required": False},
            {"key": "agent.api_key", "label": "Agent Key", "required": True},
            {"key": "module:demo_mod.api_key", "label": "模块 Key", "required": True},
        ],
    }
    raw.update(overrides)
    return raw


SECRETS = {
    "providers[0].api_key": "sk-provider",
    "providers[0].timeout": "30",
    "agent.api_key": "sk-agent",
    "module:demo_mod.api_key": "sk-module",
}


def _files(tmp_path) -> dict:
    package = pack_module_dir(_write_module(tmp_path))
    return {
        "persona/default.md": "你是一个普通群友。".encode(),
        "configs/agent.json": json.dumps({"group_enable": True}).encode(),
        "configs/module-demo_mod.json": json.dumps({"switch_on": True}).encode(),
        "modules/demo_mod-1.2.0.zip": package,
    }


async def test_apply_installs_bundled_module_and_writes_module_secret(
    container, config_service, db, tmp_path
):
    await _seed(container, config_service)
    installer = _FakeInstaller()
    applier = BundleApplier(config_service, installed_modules={}, module_installer=installer)

    result = await applier.apply(
        "demo-pack", "acc1", normalize_manifest(_manifest_with_module()), _files(tmp_path),
        secrets=dict(SECRETS),
    )

    assert result["status"] == "active"
    assert result["modules_installed"] == ["demo_mod"], "随包模块应被安装"
    assert installer.installed["demo_mod"] == "1.2.0"
    assert result["plan"]["modules_to_install"] == ["demo_mod"]

    module_config = config_service.get_module_config("demo_mod", "acc1")
    assert module_config["switch_on"] is True
    assert module_config["api_key"] == "sk-module", "模块密钥应按声明键回写（此前被静默丢弃）"
    agent = config_service.get_module_config("agent", "acc1")
    assert agent["api_key"] == "sk-agent", "Agent 密钥应按声明键回写"

    row = await db.fetchone("SELECT snapshot_json FROM bundle_activations WHERE id = ?", (result["activation_id"],))
    snapshot = json.loads(row["snapshot_json"])
    assert snapshot["modules_installed"] == ["demo_mod"]


async def test_rollback_uninstalls_module_installed_by_bundle(container, config_service, tmp_path):
    await _seed(container, config_service)
    installer = _FakeInstaller()
    applier = BundleApplier(config_service, installed_modules={}, module_installer=installer)
    result = await applier.apply(
        "demo-pack", "acc1", normalize_manifest(_manifest_with_module()), _files(tmp_path),
        secrets=dict(SECRETS),
    )

    rolled = await applier.rollback(result["activation_id"])

    assert rolled["modules_uninstalled"] == ["demo_mod"]
    assert installer.uninstalled == ["demo_mod"]
    assert config_service.get_module_config("demo_mod", "acc1") == {}, "回滚后模块配置应恢复"


async def test_apply_creates_all_models_and_persona_card_fields(
    container, config_service, tmp_path
):
    await _seed(container, config_service)
    applier = BundleApplier(config_service, module_installer=_FakeInstaller())

    result = await applier.apply(
        "demo-pack", "acc1", normalize_manifest(_manifest_with_module()), _files(tmp_path),
        secrets=dict(SECRETS),
    )

    agent = config_service.get_module_config("agent", "acc1")
    assert len(agent["provider_model_pool"]) == 2, "模型池应全量迁移"
    models = [config_service.get_provider_model(item) for item in agent["provider_model_pool"]]
    assert {model["model"] for model in models} == {"deepseek-chat", "deepseek-reasoner"}
    preset = config_service.get_provider_preset(models[0]["preset_id"])
    assert preset["config"]["retry"] == 2
    assert preset["config"]["timeout"] == "30", "provider 级密钥声明应写进连接预设"

    persona = config_service.get_role_preset(agent["persona_preset_id"])
    assert persona["tools"] == ["web_search"]
    assert persona["skills"] == ["demo_skill"]
    assert persona["begin_dialogs"] == [{"role": "user", "content": "在吗"}]
    assert persona["description"] == "简短自然"
    assert result["modules_upgraded"] == []


async def test_apply_rejects_missing_required_secret(container, config_service, tmp_path):
    await _seed(container, config_service)
    before = config_service.get_module_config("agent", "acc1")
    applier = BundleApplier(config_service, module_installer=_FakeInstaller())

    with pytest.raises(BundleError) as exc:
        await applier.apply(
            "demo-pack", "acc1", normalize_manifest(_manifest_with_module()), _files(tmp_path),
            secrets={"providers[0].api_key": "sk-provider"},
        )

    assert exc.value.code == "BUNDLE_SECRET_MISSING"
    assert config_service.get_module_config("agent", "acc1") == before, "缺密钥时不应写入半套配置"


async def test_apply_upgrades_installed_module_without_marking_uninstall(
    container, config_service, tmp_path
):
    await _seed(container, config_service)
    installer = _FakeInstaller()
    installer.installed["demo_mod"] = "0.9.0"
    applier = BundleApplier(
        config_service,
        installed_modules={"demo_mod": "0.9.0"},
        module_installer=installer,
    )
    result = await applier.apply(
        "demo-pack", "acc1", normalize_manifest(_manifest_with_module()), _files(tmp_path),
        secrets=dict(SECRETS),
    )

    assert result["modules_installed"] == []
    assert result["modules_upgraded"] == ["demo_mod"]
    await applier.rollback(result["activation_id"])
    assert installer.uninstalled == [], "升级的模块属于用户原有安装，回滚不能删"
