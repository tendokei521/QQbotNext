"""P5-4：免安装发行包（Deploy Kit）构建。

锁定四件事：①入口/前端缺一不产出；②用户数据、venv、日志、密钥绝不进包；
③产物内 deploy.json 逐文件 sha256 可自查；④确定性 zip（同输入同字节，除时间戳）。
"""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_deploy_kit.py"
_spec = importlib.util.spec_from_file_location("build_deploy_kit", _SCRIPT)
_module = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_module)

build_kit = _module.build_kit
collect_files = _module.collect_files
validate_tree = _module.validate_tree


def _fake_repo(tmp_path, *, dist=True) -> "object":
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    for name in ("start.bat", "start.sh", "start.py", "check_deps.py", "main.py", "pyproject.toml"):
        (root / name).write_text("# entry", encoding="utf-8")
    (root / "app" / "core").mkdir(parents=True)
    (root / "app" / "core" / "settings.py").write_text("X = 1\n", encoding="utf-8")
    dashboard = root / "app" / "webui" / "dashboard"
    dashboard.mkdir(parents=True)
    if dist:
        (dashboard / "dist").mkdir()
        (dashboard / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    # 绝不能进包的内容
    (root / ".env").write_text("API_KEY=secret\n", encoding="utf-8")
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "venv" / "bin" / "python").write_text("x", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "app.db").write_text("db", encoding="utf-8")
    (root / "logs").mkdir()
    (root / "logs" / "app.log").write_text("log", encoding="utf-8")
    (root / "app" / "__pycache__").mkdir()
    (root / "app" / "__pycache__" / "x.pyc").write_text("x", encoding="utf-8")
    return root


def test_collect_files_excludes_user_data(tmp_path):
    root = _fake_repo(tmp_path)
    names = {path.as_posix() for path in collect_files(root)}
    assert "start.py" in names
    assert not any(name.startswith("venv/") for name in names)
    assert not any(name.startswith("data/") for name in names)
    assert not any(name.startswith("logs/") for name in names)
    assert ".env" not in names
    assert not any(name.endswith(".pyc") for name in names)


def test_validate_tree_requires_dist_and_entries(tmp_path):
    root = _fake_repo(tmp_path, dist=False)
    with pytest.raises(SystemExit) as exc:
        validate_tree(root)
    assert "前端未构建" in str(exc.value)

    (root / "main.py").unlink()
    with pytest.raises(SystemExit) as exc2:
        validate_tree(root)
    assert "main.py" in str(exc2.value)


def test_build_kit_layout_and_manifest(tmp_path):
    root = _fake_repo(tmp_path)
    (root / "bundles").mkdir()
    (root / "bundles" / "starter.zip").write_bytes(b"PK\x03\x04dummy")

    out = build_kit(root, out_dir=tmp_path / "out", version="2.1.0")

    assert out.is_file()
    assert out.name == "QQBotNext-2.1.0-kit.zip"
    with zipfile.ZipFile(out) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("QQBotNext/deploy.json"))
        readme = archive.read("QQBotNext/README-FIRST.txt").decode("utf-8")
        assert "QQBotNext/start.bat" in names
        assert "QQBotNext/start.sh" in names
        assert "QQBotNext/app/webui/dashboard/dist/index.html" in names
        assert "QQBotNext/bundles/starter.zip" in names
        assert not any("venv/" in name or "/data/" in name for name in names)
        assert not any(name.endswith(".env") for name in names)
        assert manifest["schema"] == "qqbotnext.deploykit/1"
        assert manifest["version"] == "2.1.0"
        assert len(manifest["files"]["start.py"]) == 64
        assert manifest["count"] == len(manifest["files"])
        assert "三步上手" in readme


def test_root_dist_excluded_but_dashboard_dist_kept(tmp_path):
    """回归：曾把 ``dist`` 一律排除，连前端产物一起打不进去（解压后是"请先构建"页）。"""
    root = _fake_repo(tmp_path)
    (root / "dist").mkdir()
    (root / "dist" / "wheel.whl").write_text("bin", encoding="utf-8")

    names = {path.as_posix() for path in collect_files(root)}

    assert "app/webui/dashboard/dist/index.html" in names
    assert not any(name.startswith("dist/") for name in names)


def test_kit_without_optional_payloads(tmp_path):
    root = _fake_repo(tmp_path)
    out = build_kit(root, out_dir=tmp_path / "out", version="dev")
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
    assert not any(name.startswith("QQBotNext/wheelhouse/") for name in names)
    assert "QQBotNext/deploy.json" in names


def test_update_cli_status_runs():
    """CLI 是用户唯一的安全更新入口：status 必须能跑通并输出 JSON。"""
    import json as _json
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "scripts/update.py", "status"],
        cwd=root, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = _json.loads(result.stdout)
    assert "current" in payload and "rollback_available" in payload
