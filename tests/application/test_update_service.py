"""P5-5：可回滚更新（版本比对 / 暂存校验 / 备份 / 切换 / 回滚）。

锁定：①②③——不新不装、校验不符不落盘、切换前先备份、回滚能退回旧文件与旧数据库。
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from app.application.update import UpdateError, UpdateService
from app.application.update.version import compare_versions, read_local_version


def _kit(files: dict[str, str], version: str = "2.0.0") -> bytes:
    payload = {
        "QQBotNext/deploy.json": json.dumps({"schema": "qqbotnext.deploykit/1", "version": version}),
        **{f"QQBotNext/{name}": text for name, text in files.items()},
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in payload.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def _env(tmp_path, version="1.0.0"):
    root = tmp_path / "app_root"
    data = root / "data"
    (root / "app").mkdir(parents=True)
    data.mkdir(parents=True)
    (root / "app" / "core.py").write_text("VERSION = 'old'", encoding="utf-8")
    (root / "main.py").write_text("# old main", encoding="utf-8")
    (root / "pyproject.toml").write_text(f'version = "{version}"\n', encoding="utf-8")
    (root / ".env").write_text("TOKEN=old\n", encoding="utf-8")
    (data / "app.db").write_text("old-db", encoding="utf-8")
    return root, data


def test_compare_versions():
    assert compare_versions("2.0.0", "1.9.9") == 1
    assert compare_versions("1.0.0", "1.0.0") == 0
    assert compare_versions("1.0.0-rc1", "1.0.0") == -1
    with pytest.raises(ValueError):
        compare_versions("abc", "1.0.0")


def test_read_local_version(tmp_path):
    root, _ = _env(tmp_path, version="3.1.4")
    assert read_local_version(root) == "3.1.4"
    assert read_local_version(tmp_path / "nope") == "0.0.0"


def test_check_reports_newer_release(tmp_path):
    root, data = _env(tmp_path)
    service = UpdateService(root, data, version="1.0.0")

    same = service.check({"tag_name": "v1.0.0"})
    assert same["update_available"] is False

    newer = service.check({
        "tag_name": "v2.0.0",
        "body": "notes",
        "assets": [
            {"name": "source.tar.gz", "browser_download_url": "https://x/s.tar.gz"},
            {"name": "QQBotNext-2.0.0-kit.zip", "browser_download_url": "https://x/k.zip",
             "sha256": "a" * 64},
        ],
    })
    assert newer["update_available"] is True
    assert newer["latest"] == "2.0.0"
    assert newer["asset"]["name"] == "QQBotNext-2.0.0-kit.zip"
    assert newer["notes"] == "notes"


def test_stage_verifies_and_does_not_touch_live_files(tmp_path):
    root, data = _env(tmp_path)
    kit = _kit({"app/core.py": "VERSION = 'new'", "main.py": "# new main"}, version="2.0.0")
    service = UpdateService(root, data, version="1.0.0", fetcher=lambda url: kit)

    result = service.stage({
        "name": "kit.zip", "url": "https://x/k.zip",
        "sha256": hashlib.sha256(kit).hexdigest(), "size": len(kit),
    })

    assert result["version"] == "2.0.0"
    assert result["files"] >= 2
    assert (root / "app" / "core.py").read_text(encoding="utf-8") == "VERSION = 'old'", "暂存不能改线上"
    assert service.status()["staged"] == ["2.0.0"]


def test_stage_rejects_checksum_mismatch_and_older_version(tmp_path):
    root, data = _env(tmp_path)
    kit = _kit({"app/core.py": "x"}, version="2.0.0")
    service = UpdateService(root, data, version="1.0.0", fetcher=lambda url: kit)

    with pytest.raises(UpdateError) as exc:
        service.stage({"url": "https://x/k.zip", "sha256": "0" * 64})
    assert exc.value.code == "UPDATE_CHECKSUM_MISMATCH"
    assert service.status()["staged"] == []

    older = _kit({"app/core.py": "x"}, version="0.9.0")
    service_old = UpdateService(root, data, version="1.0.0", fetcher=lambda url: older)
    with pytest.raises(UpdateError) as exc2:
        service_old.stage({"url": "https://x/k.zip"})
    assert exc2.value.code == "UPDATE_NOT_NEWER"


def test_apply_backs_up_and_rollback_restores(tmp_path):
    root, data = _env(tmp_path)
    kit = _kit({"app/core.py": "VERSION = 'new'", "main.py": "# new main"}, version="2.0.0")
    service = UpdateService(root, data, version="1.0.0", fetcher=lambda url: kit)
    service.stage({"url": "https://x/k.zip"})

    applied = service.apply("2.0.0")

    assert applied["replaced"] == 2
    assert "app.db" in applied["backup"] and ".env" in applied["backup"]
    assert (root / "app" / "core.py").read_text(encoding="utf-8") == "VERSION = 'new'"
    assert (data / "app.db").read_text(encoding="utf-8") == "old-db", "更新不该动数据库内容"

    # 模拟更新后数据库被新版本改坏
    (data / "app.db").write_text("broken-by-new-version", encoding="utf-8")

    rolled = service.rollback("2.0.0")

    assert rolled["restored_files"] == 2
    assert (root / "app" / "core.py").read_text(encoding="utf-8") == "VERSION = 'old'"
    assert (root / "main.py").read_text(encoding="utf-8") == "# old main"
    assert (data / "app.db").read_text(encoding="utf-8") == "old-db", "回滚要恢复备份的数据库"
    assert (root / ".env").read_text(encoding="utf-8") == "TOKEN=old\n"
    assert service.status()["rollback_available"] is False


def test_apply_without_staged_fails(tmp_path):
    root, data = _env(tmp_path)
    service = UpdateService(root, data, version="1.0.0")
    with pytest.raises(UpdateError) as exc:
        service.apply()
    assert exc.value.code == "UPDATE_NOTHING_STAGED"
    with pytest.raises(UpdateError) as exc2:
        service.rollback()
    assert exc2.value.code == "UPDATE_NO_ROLLBACK"


def test_kit_without_app_dir_is_rejected(tmp_path):
    root, data = _env(tmp_path)
    bad = _kit({"docs/readme.md": "x"}, version="2.0.0")
    service = UpdateService(root, data, version="1.0.0", fetcher=lambda url: bad)
    with pytest.raises(UpdateError) as exc:
        service.stage({"url": "https://x/k.zip"})
    assert exc.value.code == "UPDATE_BAD_KIT"
