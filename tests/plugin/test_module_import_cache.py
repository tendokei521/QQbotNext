"""插件代码只应被 exec 一次：按账号装配复用 sys.modules，热重载/覆盖安装才重导。

回归：``loader.instantiate_module`` 原先每次加载都 ``purge_module_cache`` 再
``exec_module``。N 个账号 → 同一插件被 exec N 次，模块级单例/连接池各一份，
``isinstance`` 跨账号判定失效。修复后：普通加载复用已导入模块；只有
``reload_single`` / ``reload_all`` / 覆盖安装，或入口文件指纹变化时才重新导入。
"""

from __future__ import annotations

import json
import os
import sys
import time
import zipfile

import pytest

from app.application.module_install_service import ModuleInstallService
from app.infrastructure.config.config_service import ConfigService
from app.plugin.base import ServiceAccess
from app.plugin.registry import ModuleRegistry

#: 插件模板：EXECUTIONS 是模块级列表，能直接观察"这个文件被 exec 了几次"
PLUGIN_SRC = '''
from app.plugin import BaseModule

EXECUTIONS = []
EXECUTIONS.append("exec")
MARK = "{mark}"


class Module(BaseModule):
    name = "导入计数"
    sign = "ImportProbe"
    description = "测试用"
    permission = "member"
    MARK = "{mark}"
'''


def _manifest(name: str, version: str = "1.0.0") -> str:
    return json.dumps(
        {"name": name, "display_name": "导入计数", "version": version, "api_version": 1},
        ensure_ascii=False,
    )


def _drop_plugin_modules(name: str) -> None:
    prefix = f"qqbot_plugin_{name}"
    for key in [k for k in sys.modules if k == prefix or k.startswith(prefix + ".")]:
        del sys.modules[key]


@pytest.fixture
async def loaded_env(container, tmp_path):
    """builtin 根下造插件，按 bot1 装配一份实例（走真实 loader）。"""
    builtin = tmp_path / "plugins"
    plugin = builtin / "importprobe"
    plugin.mkdir(parents=True)
    (plugin / "module.json").write_text(_manifest("importprobe"), encoding="utf-8")
    (plugin / "main.py").write_text(PLUGIN_SRC.format(mark="v1"), encoding="utf-8")

    registry = ModuleRegistry(
        plugins_dir=builtin,
        zip_plugins_dir=None,
        config_service=container.get(ConfigService),
        services=container.get(ServiceAccess),
        install_service=None,
    )
    assert await registry.load_all(bot_id="bot1") == 1
    try:
        yield registry, plugin
    finally:
        for bot_id in list(registry.loaded_map().get("importprobe", {})):
            await registry.unload_single("importprobe", bot_id)
        _drop_plugin_modules("importprobe")


async def test_second_account_reuses_imported_module(loaded_env):
    registry, _plugin = loaded_env
    plugin_module = sys.modules["qqbot_plugin_importprobe"]
    assert len(plugin_module.EXECUTIONS) == 1

    assert await registry.load_single("importprobe", "bot2") is True

    assert sys.modules["qqbot_plugin_importprobe"] is plugin_module, "第二个账号重新 exec 了插件"
    assert len(plugin_module.EXECUTIONS) == 1, "模块级单例应只有一份（N 个账号只 exec 一次）"
    # 实例仍然按账号各一份（模块对象共享，实例不共享）
    assert registry.get("importprobe", "bot1") is not registry.get("importprobe", "bot2")


async def test_reload_single_reimports(loaded_env):
    registry, _plugin = loaded_env
    before = sys.modules["qqbot_plugin_importprobe"]

    assert await registry.reload_single("importprobe", "bot1") is True

    after = sys.modules["qqbot_plugin_importprobe"]
    assert after is not before, "显式热重载必须重新 exec（否则改了磁盘代码也不生效）"
    assert len(after.EXECUTIONS) == 1


async def test_changed_entry_file_reimports(loaded_env):
    """入口文件被改过（mtime/大小变化）：下一次普通加载应重新导入。"""
    registry, plugin = loaded_env
    source_file = plugin / "main.py"
    source_file.write_text(PLUGIN_SRC.format(mark="v2"), encoding="utf-8")
    future = time.time() + 5
    os.utime(source_file, (future, future))  # 保险：确保指纹与首次导入时不同

    assert await registry.load_single("importprobe", "bot2") is True

    assert sys.modules["qqbot_plugin_importprobe"].MARK == "v2", "入口文件变了却仍复用旧模块"


def _write_zip(zip_path, *, name: str, mark: str) -> None:
    src = PLUGIN_SRC.format(mark=mark).replace('name = "导入计数"', 'name = "演示模块"')
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("module.json", _manifest(name))
        archive.writestr("main.py", src)


async def test_overwrite_install_reimports_new_code(container, tmp_path):
    """覆盖安装换了代码：重新加载必须拿到新模块对象（否则版本升级形同虚设）。"""
    root = tmp_path / "plugin_root"
    builtin = root / "builtin"
    installed = root / "plugins"
    builtin.mkdir(parents=True)
    installed.mkdir(parents=True)
    registry = ModuleRegistry(
        plugins_dir=builtin,
        zip_plugins_dir=installed,
        config_service=container.get(ConfigService),
        services=container.get(ServiceAccess),
        install_service=ModuleInstallService(root / "uninstalled_modules.json"),
        staging_dir=root / ".staging",
        backup_dir=root / ".backup",
    )
    try:
        v1 = tmp_path / "v1.zip"
        _write_zip(v1, name="demo", mark="v1")
        await registry.install_from_zip(v1)
        assert registry.get("demo", None).MARK == "v1"

        v2 = tmp_path / "v2.zip"
        _write_zip(v2, name="demo", mark="v2")
        await registry.install_from_zip(v2, force=True)

        assert registry.get("demo", None).MARK == "v2", "覆盖安装后仍在跑旧模块代码"
    finally:
        for bot_id in list(registry.loaded_map().get("demo", {})):
            await registry.unload_single("demo", bot_id)
        _drop_plugin_modules("demo")
