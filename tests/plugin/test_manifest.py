"""module.json v2 清单测试：字段校验 / 编码 / API 版本 / 加载入口。"""

from __future__ import annotations

import json

import pytest

from app.plugin.manifest import (
    Manifest,
    ManifestError,
    load_manifest,
    validate_manifest,
)


def _data(**overrides) -> dict:
    data = {
        "name": "demo",
        "display_name": "演示模块",
        "version": "1.2.3",
        "description": "测试用",
        "author": "tester",
        "entry": "main.py",
        "api_version": 1,
        "requires": ["llm"],
        "requirements": "requirements.txt",
    }
    data.update(overrides)
    return data


def _write(module_dir, data_or_text, *, encoding="utf-8"):
    module_dir.mkdir(parents=True, exist_ok=True)
    text = data_or_text if isinstance(data_or_text, str) else json.dumps(data_or_text, ensure_ascii=False)
    (module_dir / "module.json").write_text(text, encoding=encoding)
    return module_dir


# ==================== load_manifest ====================


def test_load_manifest_full_fields(tmp_path):
    module_dir = _write(tmp_path / "demo", _data())

    manifest = load_manifest(module_dir)

    assert isinstance(manifest, Manifest)
    assert manifest.name == "demo"
    assert manifest.display_name == "演示模块"
    assert manifest.version == "1.2.3"
    assert manifest.author == "tester"
    assert manifest.entry == "main.py"
    assert manifest.api_version == 1
    assert manifest.requires == ("llm",)
    assert manifest.requirements == "requirements.txt"
    assert manifest.raw["name"] == "demo"


def test_load_manifest_defaults(tmp_path):
    module_dir = _write(
        tmp_path / "demo",
        {"name": "demo", "display_name": "演示", "version": "0.1.0"},
    )

    manifest = load_manifest(module_dir)

    assert manifest.api_version == 1
    assert manifest.entry == "main.py"
    assert manifest.requires == ()
    assert manifest.requirements is None
    assert manifest.description == ""
    assert manifest.author == ""


def test_load_manifest_missing_file(tmp_path):
    module_dir = tmp_path / "demo"
    module_dir.mkdir()

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(module_dir)

    assert "module.json" in str(exc_info.value)
    assert exc_info.value.hint


def test_load_manifest_non_utf8(tmp_path):
    module_dir = tmp_path / "demo"
    module_dir.mkdir()
    (module_dir / "module.json").write_bytes(
        json.dumps(_data(), ensure_ascii=False).encode("gbk")
    )

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(module_dir)

    assert "UTF-8" in str(exc_info.value)
    assert "UTF-8" in exc_info.value.hint


def test_load_manifest_bad_json(tmp_path):
    module_dir = _write(tmp_path / "demo", '{"name": "demo",}')

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(module_dir)

    assert "JSON" in str(exc_info.value)


def test_load_manifest_accepts_bom(tmp_path):
    module_dir = tmp_path / "demo"
    module_dir.mkdir()
    content = "\ufeff" + json.dumps(_data(), ensure_ascii=False)
    (module_dir / "module.json").write_text(content, encoding="utf-8")

    assert load_manifest(module_dir).name == "demo"


# ==================== validate_manifest ====================


def test_validate_manifest_valid():
    assert validate_manifest(_data(), source="builtin") == []
    assert validate_manifest(_data(), source="installed") == []


@pytest.mark.parametrize("field", ["name", "display_name", "version"])
def test_validate_manifest_required_fields(field):
    data = _data()
    data.pop(field)

    problems = validate_manifest(data, source="installed")

    assert any(field in problem for problem in problems)


@pytest.mark.parametrize("bad_name", ["my.plugin", "a:b", "中文模块", "with space", "9lives", ""])
def test_validate_manifest_rejects_illegal_names(bad_name):
    problems = validate_manifest(_data(name=bad_name), source="installed")

    assert problems


def test_validate_manifest_accepts_snake_case():
    assert validate_manifest(_data(name="_private2"), source="installed") == []


@pytest.mark.parametrize("api_version", [2, 0, "1", True, None])
def test_validate_manifest_rejects_bad_api_version(api_version):
    problems = validate_manifest(_data(api_version=api_version), source="installed")

    assert any("api_version" in problem for problem in problems)


def test_validate_manifest_optional_field_types():
    problems = validate_manifest(
        _data(requires="llm", requirements=123, entry=5), source="installed"
    )

    assert any("requires" in problem for problem in problems)
    assert any("requirements" in problem for problem in problems)
    assert any("entry" in problem for problem in problems)


def test_validate_manifest_not_a_dict():
    problems = validate_manifest(["not", "a", "dict"], source="installed")

    assert problems


def test_validate_manifest_unknown_source():
    with pytest.raises(ValueError):
        validate_manifest(_data(), source="whatever")
