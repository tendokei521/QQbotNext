"""模块注册表测试：加载 / 卸载 / 热重载。"""

import sys

import pytest

from app.infrastructure.config.config_service import ConfigService
from app.plugin.base import ServiceAccess
from app.plugin.registry import ModuleRegistry

TEST_MODULE = '''
from app.plugin import BaseModule

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
    mod_dir = tmp_path / "plugins" / "testmod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "__init__.py").write_text("", encoding="utf-8")
    (mod_dir / "main.py").write_text(TEST_MODULE, encoding="utf-8")
    (tmp_path / "plugins" / "__init__.py").write_text("", encoding="utf-8")

    # 按路径加载，无需动 sys.path、也不会遮蔽项目的任何包
    cfg = container.get(ConfigService)
    svc = ServiceAccess(cache=None, config_service=cfg, task_manager=_TaskStub(), settings=settings)
    reg = ModuleRegistry(plugins_dir=tmp_path / "plugins", config_service=cfg, services=svc, log=None)
    yield reg


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


def test_discover_only_counts_without_instantiating(registry, caplog):
    """启动路径：只发现不实例化（实例按账号装配），且只打一行"预加载完成"。"""
    import logging

    with caplog.at_level(logging.INFO, logger="app.plugin.registry"):
        count = registry.discover()

    assert count == 1
    assert registry.loaded_map() == {}, "discover 不能创建任何实例"
    assert "模块预加载完成: 1 个" in caplog.text


async def test_per_account_load_is_quiet_when_nothing_is_added(registry, caplog):
    """登录时补齐实例：没有新增就不该刷 INFO（旧实现每账号一行"0 个新增"）。"""
    import logging

    await registry.load_all(bot_id=123)
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="app.plugin.registry"):
        again = await registry.load_all(bot_id=123)

    assert again == 0
    assert "模块加载完成" not in caplog.text
    assert "[Module] Bot" not in caplog.text


async def test_boot_sequence_creates_one_instance_per_account(registry):
    """discover + 按账号装配：不应该多出 bot_id=None 的重复实例。"""
    registry.discover()
    await registry.load_all(bot_id=123)
    await registry.load_all(bot_id=456)

    assert set(registry.loaded_map()["testmod"]) == {123, 456}


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
    bad_dir = tmp_path / "plugins" / "badmod"
    bad_dir.mkdir(parents=True)
    (bad_dir / "__init__.py").write_text("", encoding="utf-8")
    (bad_dir / "main.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    (tmp_path / "plugins" / "__init__.py").write_text("", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    sys.modules.pop("modules", None)
    try:
        cfg = container.get(ConfigService)
        reg = ModuleRegistry(plugins_dir=tmp_path / "plugins", config_service=cfg,
                             services=ServiceAccess(), log=None)
        ok = await reg.load_single("badmod", 123)
        assert ok is False
    finally:
        sys.path.remove(str(tmp_path))
        for key in [k for k in sys.modules if k == "modules" or k.startswith("modules.")]:
            del sys.modules[key]
