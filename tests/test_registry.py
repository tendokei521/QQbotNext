"""模块注册表测试：加载 / 卸载 / 热重载。"""

import sys

import pytest

from app.infrastructure.config.config_service import ConfigService
from app.modules.base import ServiceAccess
from app.modules.registry import ModuleRegistry

TEST_MODULE = '''
from app.modules import BaseModule

class Module(BaseModule):
    name = "测试模块"
    sign = "TestMod"
    description = "registry 测试"
    permission = "member"
    subscribe = ("message_group",)
    default_config = {"x": 1}

    async def handle(self, event):
        self.handled = event
'''


class _TaskStub:
    def cancel_owner(self, owner):
        return 0


@pytest.fixture
async def registry(settings, container, tmp_path):
    mod_dir = tmp_path / "module" / "modules" / "testmod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "__init__.py").write_text("", encoding="utf-8")
    (mod_dir / "module.py").write_text(TEST_MODULE, encoding="utf-8")
    (tmp_path / "module" / "modules" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "module" / "__init__.py").write_text("", encoding="utf-8")

    # 让临时 module 包优先于项目 module 包（先清可能残留的 module.* 子树）
    for key in [k for k in sys.modules if k == "module" or k.startswith("module.")]:
        del sys.modules[key]
    sys.path.insert(0, str(tmp_path))
    cfg = container.get(ConfigService)
    svc = ServiceAccess(cache=None, config_service=cfg, task_manager=_TaskStub(), settings=settings)
    reg = ModuleRegistry(modules_dir=tmp_path / "module" / "modules", config_service=cfg, services=svc, log=None)
    yield reg
    sys.path.remove(str(tmp_path))
    # 清理临时 module 包（含顶层），避免遮蔽项目的 module
    for key in [k for k in sys.modules if k == "module" or k.startswith("module.")]:
        del sys.modules[key]


async def test_load_single(registry):
    ok = await registry.load_single("testmod", 123)
    assert ok is True
    m = registry.get("testmod", 123)
    assert m is not None
    assert m.sign == "TestMod"
    assert m.config.get("x") == 1


async def test_load_all(registry):
    n = await registry.load_all(bot_id=123)
    assert n == 1
    assert "testmod" in registry.module_names()


async def test_unload(registry):
    await registry.load_single("testmod", 123)
    await registry.unload(123)
    assert registry.get("testmod", 123) is None


async def test_reload_reimports(registry):
    await registry.load_single("testmod", 123)
    m1 = registry.get("testmod", 123)
    ok = await registry.reload_single("testmod", 123)
    assert ok is True
    m2 = registry.get("testmod", 123)
    assert m2 is not None and m1 is not m2


async def test_bad_module_is_skipped(settings, container, tmp_path):
    bad_dir = tmp_path / "modules" / "badmod"
    bad_dir.mkdir(parents=True)
    (bad_dir / "__init__.py").write_text("", encoding="utf-8")
    (bad_dir / "module.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    (tmp_path / "modules" / "__init__.py").write_text("", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    sys.modules.pop("modules", None)
    try:
        cfg = container.get(ConfigService)
        reg = ModuleRegistry(modules_dir=tmp_path / "modules", config_service=cfg,
                             services=ServiceAccess(), log=None)
        ok = await reg.load_single("badmod", 123)
        assert ok is False
    finally:
        sys.path.remove(str(tmp_path))
        for key in [k for k in sys.modules if k == "modules" or k.startswith("modules.")]:
            del sys.modules[key]
