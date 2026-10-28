"""模块卸载 / 热重载：必须 **await** 后台任务取消（带 5s 上限）。

历史问题（`app/plugin/registry.py::_teardown_instance`）：

    task_manager.cancel_owner(f"module:{module_name}:{bot_id}")   # 只 cancel，不等待

任务可能还在 finally 里写配置/注销钩子，模块却已被卸载；热重载紧接着加载新实例
→ 旧任务与新实例并存。修法：改用 ``cancel_owner_await`` 并 ``wait_for(5s)``，
同时兼容只有 ``cancel_owner`` 的轻量 task_manager（测试 stub / 旧实现）。
"""

from __future__ import annotations

from app.infrastructure.config.config_service import ConfigService
from app.plugin.base import ServiceAccess
from app.plugin.registry import ModuleRegistry

TEST_MODULE = '''
from app.plugin import BaseModule

class Module(BaseModule):
    name = "测试模块"
    sign = "TestMod"
    description = "unload await 测试"
    permission = "member"
    subscribe = ("message_group",)

    async def handle(self, event):
        pass
'''


class _AwaitableTaskManager:
    """记录调用方式：应当走 async 版并等待。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def cancel_owner(self, owner):
        self.calls.append(("sync", owner))
        return 0

    async def cancel_owner_await(self, owner):
        self.calls.append(("await", owner))
        return 1


class _LegacyTaskManager:
    """旧接口 stub：只有 cancel_owner。"""

    def __init__(self) -> None:
        self.owners: list[str] = []

    def cancel_owner(self, owner):
        self.owners.append(owner)
        return 1


def _make_registry(settings, container, tmp_path, task_manager) -> ModuleRegistry:
    mod_dir = tmp_path / "plugins" / "testmod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "__init__.py").write_text("", encoding="utf-8")
    (mod_dir / "main.py").write_text(TEST_MODULE, encoding="utf-8")
    (tmp_path / "plugins" / "__init__.py").write_text("", encoding="utf-8")

    config_service = container.get(ConfigService)
    services = ServiceAccess(
        cache=None, config_service=config_service, task_manager=task_manager, settings=settings
    )
    return ModuleRegistry(
        plugins_dir=tmp_path / "plugins",
        config_service=config_service,
        services=services,
        log=None,
    )


async def test_unload_awaits_module_tasks(settings, container, tmp_path):
    task_manager = _AwaitableTaskManager()
    registry = _make_registry(settings, container, tmp_path, task_manager)
    assert await registry.load_single("testmod", 123) is True

    await registry.unload(123)

    assert ("await", "module:testmod:123") in task_manager.calls
    assert ("sync", "module:testmod:123") not in task_manager.calls, (
        "有 cancel_owner_await 时不能退回只 cancel 的旧接口"
    )


async def test_unload_supports_legacy_task_manager(settings, container, tmp_path):
    task_manager = _LegacyTaskManager()
    registry = _make_registry(settings, container, tmp_path, task_manager)
    assert await registry.load_single("testmod", 123) is True

    await registry.unload(123)

    assert task_manager.owners == ["module:testmod:123"]
