"""zip 安装/预检必须把重活丢到线程池，不能占住事件循环。

背景：安装器在 async 端点里同步解压 zip（200MB / 5000 文件可达数秒），
这段时间整个 asyncio 事件循环停摆——WS ping 超时、所有 Bot 被对端踢下线。
用例用 20MB / 2000 文件的包 + 10ms 心跳，断言单次停顿 < 100ms。
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import time
import zipfile

from app.application.module_install_service import ModuleInstallService
from app.infrastructure.config.config_service import ConfigService
from app.plugin.base import ServiceAccess
from app.plugin.registry import ModuleRegistry

MANIFEST = json.dumps(
    {"name": "bigmod", "display_name": "大包", "version": "1.0.0", "api_version": 1},
    ensure_ascii=False,
)

MAIN_SRC = '''
from app.plugin import BaseModule


class Module(BaseModule):
    name = "大包"
    sign = "BigMod"
    description = "测试用"
    permission = "member"
'''

#: 2000 个 10KB 文件 ≈ 20MB 解压量（文件数接近常见真实插件上限）
FILE_COUNT = 2000
FILE_SIZE = 10 * 1024
HEARTBEAT_INTERVAL = 0.01
MAX_PAUSE = 0.1


def _build_big_zip(zip_path):
    """造 20MB / 2000 文件的插件包；随机内容避免被 deflate 压成极小包。"""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("module.json", MANIFEST)
        archive.writestr("main.py", MAIN_SRC)
        for index in range(FILE_COUNT):
            archive.writestr(f"assets/blob_{index}.bin", os.urandom(FILE_SIZE))
    return zip_path


async def _heartbeat(gaps: list[float], stop: asyncio.Event) -> None:
    """每 HEARTBEAT_INTERVAL 记一次实际间隔；事件循环被阻塞时间隔会明显拉长。"""
    last = time.perf_counter()
    while not stop.is_set():
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        now = time.perf_counter()
        gaps.append(now - last)
        last = now


def _make_registry(container, tmp_path) -> ModuleRegistry:
    root = tmp_path / "plugin_root"
    builtin = root / "builtin"
    installed = root / "plugins"
    builtin.mkdir(parents=True)
    installed.mkdir(parents=True)
    return ModuleRegistry(
        plugins_dir=builtin,
        zip_plugins_dir=installed,
        config_service=container.get(ConfigService),
        services=container.get(ServiceAccess),
        install_service=ModuleInstallService(root / "uninstalled_modules.json"),
        staging_dir=root / ".staging",
        backup_dir=root / ".backup",
    )


def _drop_plugin_modules(name: str) -> None:
    prefix = f"qqbot_plugin_{name}"
    for key in [k for k in sys.modules if k == prefix or k.startswith(prefix + ".")]:
        del sys.modules[key]


async def test_install_large_zip_keeps_event_loop_responsive(container, tmp_path):
    """安装 20MB 包期间，事件循环的单次停顿必须远小于 WS 心跳超时。"""
    registry = _make_registry(container, tmp_path)
    zip_path = _build_big_zip(tmp_path / "big.zip")

    gaps: list[float] = []
    stop = asyncio.Event()
    beat = asyncio.create_task(_heartbeat(gaps, stop))
    await asyncio.sleep(HEARTBEAT_INTERVAL * 2)  # 预热：别把任务启动耗时算进来
    try:
        info = await registry.install_from_zip(zip_path)
    finally:
        stop.set()
        await beat
        _drop_plugin_modules("bigmod")

    assert info["module_name"] == "bigmod"
    assert info["files"] >= FILE_COUNT, "包体没有真正解压出来"
    assert gaps, "心跳任务没采集到数据"
    assert max(gaps) < MAX_PAUSE, f"安装期间事件循环最长停顿 {max(gaps) * 1000:.0f}ms（阈值 {MAX_PAUSE * 1000:.0f}ms）"


async def test_validate_zip_endpoint_runs_preflight_off_loop():
    """validate-zip 端点的同步预检必须在线程里跑（假 registry 用 sleep 放大阻塞）。"""
    from app.webui.api import modules as api_module

    class _SlowRegistry:
        def validate_zip(self, path):
            time.sleep(0.2)  # 同步阻塞；若在事件循环里跑会直接吃掉 200ms
            return {"ok": True, "plugin": {"name": "demo", "version": "1.0.0"}, "hint": ""}

    class _Upload:
        filename = "demo.zip"
        file = io.BytesIO(b"fake-zip-bytes")

    gaps: list[float] = []
    stop = asyncio.Event()
    beat = asyncio.create_task(_heartbeat(gaps, stop))
    await asyncio.sleep(HEARTBEAT_INTERVAL * 2)
    try:
        response = await api_module.validate_module_zip(
            request=None, file=_Upload(), registry=_SlowRegistry()
        )
    finally:
        stop.set()
        await beat

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["ok"] is True
    assert payload["plugin"]["name"] == "demo"
    assert max(gaps) < MAX_PAUSE, f"validate-zip 阻塞事件循环 {max(gaps) * 1000:.0f}ms"
