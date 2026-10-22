"""插件软卸载 / zip 安装 / 恢复安装测试。"""

import zipfile
from pathlib import Path

import pytest

from app.application.module_install_service import ModuleInstallService
from app.infrastructure.config.config_service import ConfigService
from app.plugin.base import ServiceAccess
from app.plugin.registry import ModuleRegistry

LOCAL_MODULE = '''
from app.plugin import BaseModule

class Module(BaseModule):
    name = "测试模块"
    sign = "TestMod"
    description = "install 测试"
    permission = "member"
    default_config = {"x": 1}
'''

ZIP_PLUGIN = '''
from app.plugin import BaseModule

class Module(BaseModule):
    name = "zip 插件"
    sign = "ZipMod"
    description = "zip 插件测试"
    permission = "member"
    default_config = {"y": 2}
'''


class _TaskStub:
    def cancel_owner(self, owner):
        return 0


def _make_module_root(tmp_path: Path) -> Path:
    """搭一个插件根：`local/` 放本地插件，`plugins/` 是 zip 安装目录。"""
    root = tmp_path / "plugin_root"
    (root / "local" / "testmod").mkdir(parents=True)
    (root / "local" / "__init__.py").write_text("", encoding="utf-8")
    (root / "local" / "testmod" / "__init__.py").write_text("", encoding="utf-8")
    (root / "local" / "testmod" / "main.py").write_text(LOCAL_MODULE, encoding="utf-8")
    (root / "plugins").mkdir(parents=True, exist_ok=True)
    return root


def _make_registry(tmp_path, container):
    root = _make_module_root(tmp_path)

    cfg = container.get(ConfigService)
    svc = ServiceAccess(cache=None, config_service=cfg, task_manager=_TaskStub(), settings=None)
    install_service = ModuleInstallService(root / "uninstalled_modules.json")
    reg = ModuleRegistry(
        plugins_dir=root / "local",
        zip_plugins_dir=root / "plugins",
        config_service=cfg,
        services=svc,
        install_service=install_service,
        log=None,
    )
    return root, reg, install_service


@pytest.fixture
def setup_registry(container, tmp_path):
    root, reg, install_service = _make_registry(tmp_path, container)
    yield root, reg, install_service


def test_module_install_service_roundtrip(tmp_path):
    state_file = tmp_path / "uninstalled_modules.json"
    svc = ModuleInstallService(state_file)
    assert svc.is_uninstalled("demo") is False

    svc.uninstall("demo", source="zip", display_name="Demo", version="1.0.0")
    assert svc.is_uninstalled("demo") is True
    items = svc.list_uninstalled()
    assert len(items) == 1
    assert items[0]["module_name"] == "demo"
    assert items[0]["source"] == "zip"
    assert items[0]["version"] == "1.0.0"

    svc.reinstall("demo")
    assert svc.is_uninstalled("demo") is False
    assert svc.list_uninstalled() == []


async def test_local_module_soft_uninstall_keeps_files(setup_registry):
    root, reg, install_service = setup_registry

    assert await reg.load_all(bot_id=123) == 1
    assert reg.get("testmod", 123) is not None

    await reg.uninstall_module("testmod")
    assert reg.get("testmod", 123) is None
    assert (root / "local" / "testmod" / "main.py").exists()
    assert install_service.is_uninstalled("testmod") is True

    ok = await reg.reinstall_module("testmod", 123)
    assert ok is True
    assert reg.get("testmod", 123) is not None


async def test_install_from_zip_and_soft_uninstall(setup_registry):
    root, reg, install_service = setup_registry
    plugin_dir = root / "src_demo"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / "module.json").write_text(
        '{"name": "demo", "display_name": "Zip 插件", "version": "1.2.3"}',
        encoding="utf-8",
    )
    (plugin_dir / "main.py").write_text(ZIP_PLUGIN, encoding="utf-8")
    zip_path = root / "demo.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        for file in plugin_dir.rglob("*"):
            if file.is_file():
                z.write(file, f"demo/{file.relative_to(plugin_dir)}")

    info = await reg.install_from_zip(zip_path)
    assert info["module_name"] == "demo"
    assert info["version"] == "1.2.3"

    module = reg.get("demo", None)
    assert module is not None
    assert module.source == "zip"
    assert module.version == "1.2.3"

    await reg.uninstall_module("demo")
    assert reg.get("demo", None) is None
    assert (root / "plugins" / "demo" / "main.py").exists()
    assert install_service.is_uninstalled("demo") is True

    ok = await reg.reinstall_module("demo", None)
    assert ok is True
    assert reg.get("demo", None) is not None
