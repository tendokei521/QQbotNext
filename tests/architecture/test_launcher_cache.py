"""启动检查缓存的测试（start.py 只允许标准库，这里同样只测纯逻辑）。"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("launcher_start", ROOT / "start.py")
assert _SPEC and _SPEC.loader
launcher = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(launcher)


@pytest.fixture
def fake_venv(tmp_path):
    """造一个假的 venv 结构。"""
    scripts = tmp_path / "venv" / ("Scripts" if launcher.IS_WINDOWS else "bin")
    scripts.mkdir(parents=True)
    python = scripts / ("python.exe" if launcher.IS_WINDOWS else "python3")
    python.write_bytes(b"fake")
    site = tmp_path / "venv" / ("Lib" if launcher.IS_WINDOWS else "lib") / "site-packages"
    site.mkdir(parents=True)
    return python, site


def _key(python, **kwargs):
    return launcher.cache_key(python, python_version="3.12.1", **kwargs)


# ==================== 指纹 ====================


def test_cache_key_is_stable_for_same_environment(fake_venv):
    python, _ = fake_venv
    assert _key(python) == _key(python), "同样环境应产出同样指纹"


def test_cache_invalidated_when_groups_change(fake_venv):
    python, _ = fake_venv
    base = _key(python)
    dev = _key(python, dev=True)
    assert base != dev, "--dev 装了额外依赖，必须重查"


def test_cache_invalidated_when_python_replaced(fake_venv):
    python, _ = fake_venv
    before = _key(python)
    python.write_bytes(b"another-python-binary")   # 大小/mtime 变化
    after = _key(python)
    assert before != after


def test_cache_invalidated_when_site_packages_change(fake_venv):
    import os

    python, site = fake_venv
    old = time.time() - 60
    os.utime(site, (old, old))
    before = _key(python)

    (site / "something_new").write_text("x", encoding="utf-8")  # 模拟装了个包
    os.utime(site, None)
    after = _key(python)
    assert before != after, "site-packages 变了必须让缓存失效"


# ==================== 匹配判定 ====================


def test_cache_matches_roundtrip(tmp_path, fake_venv):
    python, _ = fake_venv
    cache_file = tmp_path / "cache.json"
    key = _key(python)
    assert launcher.save_cache(key, cache_file)
    record = launcher.load_cache(cache_file)
    ok, why = launcher.cache_matches(record, key)
    assert ok, why


def test_cache_miss_reports_reason(tmp_path, fake_venv):
    python, _ = fake_venv
    cache_file = tmp_path / "cache.json"
    launcher.save_cache(_key(python), cache_file)
    record = launcher.load_cache(cache_file)

    ok, why = launcher.cache_matches(record, _key(python, dev=True))
    assert not ok
    assert "可选依赖组" in why, why


def test_missing_cache_is_a_miss(tmp_path):
    ok, why = launcher.cache_matches(None, {})
    assert not ok
    assert "首次" in why


def test_corrupted_cache_is_a_miss(tmp_path):
    cache_file = tmp_path / "cache.json"
    cache_file.write_text("{not json", encoding="utf-8")
    assert launcher.load_cache(cache_file) is None


def test_expired_cache_is_a_miss(tmp_path, fake_venv):
    python, _ = fake_venv
    cache_file = tmp_path / "cache.json"
    key = _key(python)
    launcher.save_cache(key, cache_file)
    record = launcher.load_cache(cache_file)
    record["checked_at"] = time.time() - (launcher.CACHE_MAX_AGE_DAYS + 1) * 86400
    cache_file.write_text(json.dumps(record), encoding="utf-8")

    ok, why = launcher.cache_matches(launcher.load_cache(cache_file), key)
    assert not ok
    assert "过期" in why


def test_clear_cache_removes_file(tmp_path, fake_venv):
    python, _ = fake_venv
    cache_file = tmp_path / "cache.json"
    launcher.save_cache(_key(python), cache_file)
    assert cache_file.exists()
    launcher.clear_cache(cache_file)
    assert not cache_file.exists()


def test_save_cache_creates_parent_dir(tmp_path, fake_venv):
    python, _ = fake_venv
    nested = tmp_path / "data" / "sub" / "cache.json"
    assert launcher.save_cache(_key(python), nested)
    assert nested.exists()


# ==================== 参数 ====================


def test_recheck_flag_exists():
    args = launcher.parse_args(["--recheck"])
    assert args.recheck is True
    assert launcher.parse_args([]).recheck is False


# ==================== 主流程：--force-launch 不写缓存 ====================


class _Done:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_force_launch_does_not_write_cache(tmp_path, monkeypatch):
    """依赖不齐 + --force-launch：允许启动，但不得把"不齐"写进缓存。

    缓存记的是「环境已就绪」这个结论。若强行启动后仍写缓存，下次打开 start.py
    会命中缓存跳过检查，用户永远看不到依赖不齐的真实原因。
    """
    venv = tmp_path / "venv"
    scripts = venv / ("Scripts" if launcher.IS_WINDOWS else "bin")
    scripts.mkdir(parents=True)
    python = scripts / ("python.exe" if launcher.IS_WINDOWS else "python3")
    python.write_bytes(b"fake")
    (venv / ("Lib" if launcher.IS_WINDOWS else "lib") / "site-packages").mkdir(parents=True)

    cache_file = tmp_path / "cache.json"
    monkeypatch.setenv("QQBOT_LAUNCHER_CACHE", str(cache_file))

    def fake_runner(cmd, **kwargs):
        joined = " ".join(str(item) for item in cmd)
        if "check_deps.py" in joined:
            return _Done(launcher.EXIT_DEPS)  # 依赖不齐
        if "print(" in joined:  # venv_python_version
            return _Done(0, stdout=b"3.12.1")
        return _Done(0)

    rc = launcher.main(
        ["--venv", str(venv), "--force-launch", "--no-open"],
        runner=fake_runner,
        in_process=True,  # 不走 execv，交给假 runner 返回
    )

    assert rc == launcher.EXIT_OK, "假 runner 的启动返回码应为 0"
    assert not cache_file.exists(), "--force-launch 启动后不得写启动缓存"

