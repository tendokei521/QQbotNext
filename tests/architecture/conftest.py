"""architecture 测试的共享夹具。"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_launcher_cache(tmp_path, monkeypatch):
    """`start.py` 的缓存指到临时目录：跑测试不许动真机的启动缓存。"""
    monkeypatch.setenv("QQBOT_LAUNCHER_CACHE", str(tmp_path / ".launcher-cache.json"))
