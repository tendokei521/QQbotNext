"""日志导出 API 的安全与基础逻辑测试。"""

import json
import zipfile
from pathlib import Path

import pytest

from app.webui.api.logs import _safe_resolve


def _mkdir_logs(tmp_path: Path) -> Path:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for name in ("debug.log", "warn.log", "errors.log", "user.log"):
        (log_dir / name).write_text(f"content-{name}", encoding="utf-8")
    archive = log_dir / "2026-08-29-00"
    archive.mkdir()
    (archive / "debug.log").write_text("archived-debug", encoding="utf-8")
    (archive / "user.log").write_text("archived-user", encoding="utf-8")
    return log_dir


def test_safe_resolve_current_and_archive(tmp_path):
    log_dir = _mkdir_logs(tmp_path)

    current = _safe_resolve(log_dir, "", "debug.log")
    assert current.name == "debug.log"
    assert current.parent == log_dir

    current_alias = _safe_resolve(log_dir, "current", "debug.log")
    assert current_alias == current

    archived = _safe_resolve(log_dir, "2026-08-29-00", "debug.log")
    assert archived.name == "debug.log"
    assert archived.parent.name == "2026-08-29-00"


def test_safe_resolve_rejects_bad_input(tmp_path):
    log_dir = _mkdir_logs(tmp_path)

    with pytest.raises(ValueError):
        _safe_resolve(log_dir, "../etc", "debug.log")
    with pytest.raises(ValueError):
        _safe_resolve(log_dir, "", "evil.txt")
    with pytest.raises(ValueError):
        _safe_resolve(log_dir, "", "")


def test_zip_exports_expected_arcnames(tmp_path):
    log_dir = _mkdir_logs(tmp_path)
    files = [
        (_safe_resolve(log_dir, "", "debug.log"), "debug.log"),
        (_safe_resolve(log_dir, "2026-08-29-00", "user.log"), "2026-08-29-00/user.log"),
    ]

    zip_path = tmp_path / "out.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, arcname in files:
            zf.write(path, arcname)

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert names == ["debug.log", "2026-08-29-00/user.log"]
    assert json.dumps(names)
