"""P5-1：协议端固定版本清单 + 校验安装。

锁定：只装清单内版本、sha256 不符拒绝且不留半装目录、可回滚上一版、支持用户覆盖清单。
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from app.application.protocol.installer import ProtocolInstaller
from app.application.protocol.manifest import ProtocolError, load_manifest, select_version


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def _write_manifest(root, protocol="napcat", version="1.2.0", payload=b"", sha=None, url="https://example.com/p.zip", size=None):
    root.mkdir(parents=True, exist_ok=True)
    entry = {
        "version": version,
        "url": url,
        "sha256": sha or hashlib.sha256(payload).hexdigest(),
    }
    if size is not None:
        entry["size"] = size
    (root / "manifest.json").write_text(
        json.dumps({"schema": "qqbotnext.protocol/1", "protocols": {protocol: {"versions": [entry]}}}),
        encoding="utf-8",
    )


def _installer(tmp_path, payload: bytes, **kwargs) -> ProtocolInstaller:
    _write_manifest(tmp_path / "protocol", payload=payload, **kwargs)
    return ProtocolInstaller(tmp_path, fetcher=lambda url: payload)


def test_builtin_manifest_has_no_unverified_versions(tmp_path):
    manifest = load_manifest(tmp_path)
    assert manifest["protocols"]["napcat"]["versions"] == []
    with pytest.raises(ProtocolError) as exc:
        select_version(manifest, "napcat")
    assert exc.value.code == "PROTOCOL_NO_VERSION"


def test_install_verifies_and_activates(tmp_path):
    payload = _zip_bytes({"napcat/napcat.exe": "binary", "config.json": "{}"})
    installer = _installer(tmp_path, payload)

    result = installer.install("napcat", "1.2.0")

    assert result["version"] == "1.2.0"
    installed = tmp_path / "protocol" / "napcat" / "1.2.0" / "config.json"
    assert installed.is_file()
    status = installer.status("napcat")
    assert status["installed"] is True and status["version"] == "1.2.0"


def test_checksum_mismatch_is_rejected_without_partial_install(tmp_path):
    payload = _zip_bytes({"a.txt": "x"})
    installer = _installer(tmp_path, payload, sha="0" * 64)

    with pytest.raises(ProtocolError) as exc:
        installer.install("napcat", "1.2.0")

    assert exc.value.code == "PROTOCOL_CHECKSUM_MISMATCH"
    assert not (tmp_path / "protocol" / "napcat" / "1.2.0").exists()
    assert installer.status("napcat")["installed"] is False


def test_size_mismatch_is_rejected(tmp_path):
    payload = _zip_bytes({"a.txt": "x"})
    installer = _installer(tmp_path, payload, size=999999)
    with pytest.raises(ProtocolError) as exc:
        installer.install("napcat", "1.2.0")
    assert exc.value.code == "PROTOCOL_CHECKSUM_MISMATCH"


def test_unsafe_paths_are_rejected(tmp_path):
    payload = _zip_bytes({"../evil.txt": "x"})
    installer = _installer(tmp_path, payload)
    with pytest.raises(ProtocolError) as exc:
        installer.install("napcat", "1.2.0")
    assert exc.value.code == "PROTOCOL_PACKAGE_UNSAFE"


def test_only_pinned_versions_can_be_installed(tmp_path):
    payload = _zip_bytes({"a.txt": "x"})
    installer = _installer(tmp_path, payload)
    with pytest.raises(ProtocolError) as exc:
        installer.install("napcat", "9.9.9")
    assert exc.value.code == "PROTOCOL_VERSION_UNKNOWN"
    with pytest.raises(ProtocolError):
        installer.install("no-such-protocol")


def test_rollback_switches_back(tmp_path):
    old_payload = _zip_bytes({"version.txt": "1"})
    root = tmp_path / "protocol"
    installer = ProtocolInstaller(tmp_path, fetcher=lambda url: old_payload)
    _write_manifest(root, version="1.0.0", payload=old_payload)
    installer.install("napcat", "1.0.0")

    new_payload = _zip_bytes({"version.txt": "2"})
    _write_manifest(root, version="2.0.0", payload=new_payload)
    installer2 = ProtocolInstaller(tmp_path, fetcher=lambda url: new_payload)
    installer2.install("napcat", "2.0.0")
    assert installer2.status("napcat")["version"] == "2.0.0"

    rolled = installer2.rollback("napcat")
    assert rolled["version"] == "1.0.0"
    assert installer2.status("napcat")["version"] == "1.0.0"
    assert (root / "napcat" / "1.0.0" / "version.txt").read_text() == "1"


def test_rollback_without_previous_fails(tmp_path):
    payload = _zip_bytes({"a.txt": "x"})
    installer = _installer(tmp_path, payload)
    installer.install("napcat", "1.2.0")
    with pytest.raises(ProtocolError) as exc:
        installer.rollback("napcat")
    assert exc.value.code == "PROTOCOL_NO_ROLLBACK"


def test_manifest_requires_sha256_and_http_url(tmp_path):
    root = tmp_path / "protocol"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "schema": "qqbotnext.protocol/1",
        "protocols": {"napcat": {"versions": [{"version": "1.0.0", "url": "file:///x"}]}},
    }), encoding="utf-8")
    with pytest.raises(ProtocolError) as exc:
        load_manifest(tmp_path)
    assert exc.value.code == "PROTOCOL_MANIFEST_INVALID"


def test_uninstall_removes_installed_versions(tmp_path):
    payload = _zip_bytes({"a.txt": "x"})
    installer = _installer(tmp_path, payload)
    installer.install("napcat", "1.2.0")
    result = installer.uninstall("napcat")
    assert result["removed"]
    assert installer.status("napcat")["installed"] is False
