"""递归子模块容器测试：parent.children / 配置命名空间 / 生命周期级联 / 不参与全局分发。"""

import sys

import pytest

from app.infrastructure.config.config_service import ConfigService
from app.modules.base import ServiceAccess
from app.modules.registry import ModuleRegistry

PARENT = '''
from app.modules import BaseModule

class Module(BaseModule):
    name = "父模块"
    sign = "Parent"
    description = "测试父模块"
    permission = "member"
    subscribe = ("message_group",)
    default_config = {"parent_key": "p"}
    config_schema = {}

    async def handle(self, event):
        # 由父模块调度子模块
        if "child" in self.children:
            await self.children["child"].handle(event)
'''

CHILD = '''
from app.modules import BaseModule

class Module(BaseModule):
    name = "子模块"
    sign = "Child"
    description = "测试子模块"
    permission = "member"
    subscribe = ()   # 子模块不订阅全局事件
    default_config = {"child_key": "c"}
    config_schema = {}

    async def on_load(self):
        self.loaded_events = getattr(self, "loaded_events", []) + ["on_load"]

    async def on_unload(self):
        self.loaded_events = getattr(self, "loaded_events", []) + ["on_unload"]

    async def handle(self, event):
        self.handled_event = event
'''


class _Svc:
    def __init__(self, cfg_service):
        self.cfg = cfg_service

    def get_module_config(self, module, bot_id):
        return self.cfg.get_module_config(module, bot_id)

    def set_module_config(self, module, bot_id, data, persist=True):
        self.cfg.set_module_config(module, bot_id, data, persist)


@pytest.fixture
async def parent_registry(settings, container, tmp_path):
    parent_dir = tmp_path / "module" / "modules" / "parent"
    child_dir = parent_dir / "child"
    child_dir.mkdir(parents=True)
    for d in (parent_dir, child_dir):
        (d / "__init__.py").write_text("", encoding="utf-8")
    (parent_dir / "module.py").write_text(PARENT, encoding="utf-8")
    (child_dir / "module.py").write_text(CHILD, encoding="utf-8")
    (tmp_path / "module" / "modules" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "module" / "__init__.py").write_text("", encoding="utf-8")

    # 清理可能残留的 module 包（其它测试可能导入了真实 module.modules），再插入临时路径
    for key in [k for k in sys.modules if k == "module" or k.startswith("module.")]:
        del sys.modules[key]
    sys.path.insert(0, str(tmp_path))
    try:
        cfg = container.get(ConfigService)
        svc = ServiceAccess(cache=None, config_service=cfg, task_manager=_StubTM(), settings=settings)
        reg = ModuleRegistry(modules_dir=tmp_path / "module" / "modules", config_service=cfg, services=svc, log=None)
        yield reg
    finally:
        sys.path.remove(str(tmp_path))
        for key in [k for k in sys.modules if k == "module" or k.startswith("module.")]:
            del sys.modules[key]


class _StubTM:
    def cancel_owner(self, owner):
        return 0


async def test_parent_loads_children(parent_registry):
    ok = await parent_registry.load_single("parent", 123)
    assert ok is True
    parent = parent_registry.get("parent", 123)
    assert "child" in parent.children
    child = parent.children["child"]
    assert child.module_name == "parent.child"
    assert child.bot_id == 123
    assert child.parent is parent


async def test_children_not_in_global_dispatch(parent_registry):
    await parent_registry.load_single("parent", 123)
    loaded_names = [m.module_name for m in parent_registry.loaded()]
    assert "parent" in loaded_names
    assert "parent.child" not in loaded_names  # 子模块由父调度


async def test_child_config_namespaced(parent_registry, container):
    await parent_registry.load_single("parent", 123)
    child = parent_registry.get("parent", 123).children["child"]
    assert child.config.get("child_key") == "c"
    # 写入子配置走命名空间键
    child.config.set("child_key", "c2")
    cfg = container.get(ConfigService)
    assert cfg.get_module_config("parent.child", 123)["child_key"] == "c2"


async def test_parent_handle_routes_to_child(parent_registry):
    await parent_registry.load_single("parent", 123)
    parent = parent_registry.get("parent", 123)
    child = parent.children["child"]
    event = type("E", (), {"event_type": "message_group", "bot_id": 123, "text": ""})()
    await parent.handle(event)
    assert child.handled_event is event


async def test_unload_cascades(parent_registry):
    await parent_registry.load_single("parent", 123)
    child = parent_registry.get("parent", 123).children["child"]
    await parent_registry.unload(123)
    assert parent_registry.get("parent", 123) is None
    assert parent_registry.get("parent.child", 123) is None
    assert getattr(child, "loaded_events", [])[-1] == "on_unload"  # 子模块 on_unload 被调用
