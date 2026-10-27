"""P2-1：套装清单与校验器。

预检报告是"信任界面"：结构问题直接拒绝（400），语义问题进 issues（模块缺失/密钥泄漏/未知键）。
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.application.bundle.errors import BundleError
from app.application.bundle.exporter import export_bundle
from app.application.bundle.validator import validate_bytes


def _bundle(**kwargs) -> bytes:
    payload = {
        "bundle_id": "group-chat-companion",
        "name": "群聊搭子",
        "version": "1.2.0",
        "description": "测试套装",
        "agent_config": {"group_enable": True, "persona_preset_id": "abc", "api_key": "sk-secret-key-123456"},
        "persona": {"name": "群聊搭子", "text": "你是一个普通群友。"},
        "provider": {
            "provider": "openai",
            "api_base": "https://api.deepseek.com/v1",
            "api_key": "sk-provider-secret-123",
            "model": "deepseek-chat",
            "preset_name": "DeepSeek",
        },
        "module_configs": {"llm_memory": {"enabled": True, "config": {"memory_enable": True}}},
    }
    payload.update(kwargs)
    return export_bundle(**payload)


def test_valid_bundle_passes():
    report = validate_bytes(
        _bundle(),
        installed_modules={"llm_memory": "1.2.0"},
        agent_schema={"group_enable": {}, "persona_preset_id": {}, "api_key": {}},
    )

    assert report["ok"] is True, report["issues"]
    assert report["bundle"]["id"] == "group-chat-companion"
    assert report["bundle"]["version"] == "1.2.0"
    assert report["summary"]["presets"] == 2
    assert report["summary"]["secrets_required"] >= 1
    secrets = {item["key"] for item in report["secrets"]}
    assert "providers[0].api_key" in secrets
    assert "agent.api_key" in secrets


def test_secret_values_are_not_exported():
    data = _bundle()
    assert b"sk-provider-secret-123" not in data
    assert b"sk-secret-key-123456" not in data


def test_export_is_deterministic():
    assert _bundle() == _bundle(), "同输入导出两次应字节一致（便于比对/去重）"


def test_invalid_zip_is_rejected():
    with pytest.raises(BundleError) as exc:
        validate_bytes(b"not a zip at all")
    assert exc.value.code == "BUNDLE_ZIP_INVALID"


def test_missing_manifest_is_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("README.md", "hi")
    with pytest.raises(BundleError) as exc:
        validate_bytes(buffer.getvalue())
    assert exc.value.code == "BUNDLE_MANIFEST_MISSING"


def test_path_traversal_is_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bundle.json", "{}")
        archive.writestr("../evil.txt", "x")
    with pytest.raises(BundleError) as exc:
        validate_bytes(buffer.getvalue())
    assert exc.value.code == "BUNDLE_ZIP_UNSAFE"


def _manifest_zip(manifest: dict, extra: dict | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bundle.json", json.dumps(manifest, ensure_ascii=False))
        for name, text in (extra or {}).items():
            archive.writestr(name, text)
    return buffer.getvalue()


def _base_manifest() -> dict:
    return {
        "schema": "qqbotnext.bundle/1",
        "id": "demo-pack",
        "name": "演示",
        "version": "1.0.0",
        "framework": {"api_version": 1},
        "requires": {"modules": [{"name": "llm_memory", "version": ">=1.0.0", "required": True}]},
        "components": {"personas": [], "providers": [], "agent": [], "modules": []},
        "secrets": [],
    }


def test_bad_schema_and_ids():
    manifest = _base_manifest()
    manifest["schema"] = "qqbotnext.bundle/999"
    with pytest.raises(BundleError) as exc:
        validate_bytes(_manifest_zip(manifest))
    assert exc.value.code == "BUNDLE_SCHEMA_UNSUPPORTED"

    manifest = _base_manifest()
    manifest["id"] = "Bad ID!"
    with pytest.raises(BundleError) as exc:
        validate_bytes(_manifest_zip(manifest))
    assert exc.value.code == "BUNDLE_ID_INVALID"

    manifest = _base_manifest()
    manifest["version"] = "v1"
    with pytest.raises(BundleError) as exc:
        validate_bytes(_manifest_zip(manifest))
    assert exc.value.code == "BUNDLE_VERSION_INVALID"


def test_missing_module_and_version_are_reported():
    missing = validate_bytes(_manifest_zip(_base_manifest()), installed_modules={})
    assert missing["ok"] is False
    assert any(issue["code"] == "BUNDLE_MODULE_MISSING" for issue in missing["issues"])

    outdated = validate_bytes(_manifest_zip(_base_manifest()), installed_modules={"llm_memory": "0.9.0"})
    assert any(issue["code"] == "BUNDLE_MODULE_VERSION" for issue in outdated["issues"])

    good = validate_bytes(_manifest_zip(_base_manifest()), installed_modules={"llm_memory": "1.2.0"})
    assert good["ok"] is True


def test_optional_module_missing_is_only_warning():
    manifest = _base_manifest()
    manifest["requires"]["modules"] = [{"name": "llm_memory", "version": "", "required": False}]
    report = validate_bytes(_manifest_zip(manifest), installed_modules={})
    assert report["ok"] is True
    assert any(issue["level"] == "warn" for issue in report["issues"])


def test_incompatible_api_version_is_rejected():
    manifest = _base_manifest()
    manifest["requires"]["modules"] = []
    manifest["framework"]["api_version"] = 99
    report = validate_bytes(_manifest_zip(manifest), framework_api_version=1, installed_modules={})
    assert report["ok"] is False
    assert any(issue["code"] == "BUNDLE_INCOMPATIBLE" for issue in report["issues"])


def test_missing_reference_file_is_reported():
    manifest = _base_manifest()
    manifest["requires"]["modules"] = []
    manifest["components"]["personas"] = [{"source": "persona/missing.md", "name": "x"}]
    report = validate_bytes(_manifest_zip(manifest), installed_modules={})
    assert any(issue["code"] == "BUNDLE_REF_MISSING" for issue in report["issues"])


def test_unknown_agent_keys_are_rejected():
    manifest = _base_manifest()
    manifest["requires"]["modules"] = []
    manifest["components"]["agent"] = [{"source": "configs/agent.json"}]
    data = _manifest_zip(manifest, {"configs/agent.json": json.dumps({"group_enable": True, "nonsense": 1})})
    report = validate_bytes(data, installed_modules={}, agent_schema={"group_enable": {}})
    assert any(issue["code"] == "BUNDLE_KEY_UNKNOWN" for issue in report["issues"])


def test_plaintext_secret_in_content_is_reported():
    manifest = _base_manifest()
    manifest["requires"]["modules"] = []
    manifest["components"]["agent"] = [{"source": "configs/agent.json"}]
    data = _manifest_zip(
        manifest,
        {"configs/agent.json": json.dumps({"api_key": "sk-live-abcdef123456"})},
    )
    report = validate_bytes(data, installed_modules={})
    assert any(issue["code"] == "BUNDLE_SECRET_LEAK" for issue in report["issues"])


def test_checksum_mismatch_is_rejected():
    manifest = _base_manifest()
    manifest["requires"]["modules"] = []
    data = _manifest_zip(manifest, {"checksums.sha256": "0" * 64 + "  bundle.json\n"})
    with pytest.raises(BundleError) as exc:
        validate_bytes(data, installed_modules={})
    assert exc.value.code == "BUNDLE_CHECKSUM_MISMATCH"
