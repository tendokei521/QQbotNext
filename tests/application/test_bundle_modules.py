"""套装迁移：模块代码随包 + 角色卡字段 + 模型池全量（导出侧）。

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

from app.application.bundle.errors import BundleError
from app.application.bundle.exporter import export_bundle
from app.application.bundle.module_packages import (
    pack_module_dir,
    read_module_identity,
)


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
