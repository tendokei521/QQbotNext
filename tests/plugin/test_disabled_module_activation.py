"""模块关闭时不得被"装配激活"：on_load 不执行、不接管运行时能力。

契约（docs/MODULE_DEV.md「关闭 = 静默不装配」）：
- `authority.enabled=False` → 启动装配跳过 `on_load`（它会把输出策略/组装器/钩子/
  工具来源直接挂到 runtime 上，过去不受 enabled 约束，导致"关了模块、重启后又生效"）；
- 重新启用由 WebUI 的 `_sync_module_runtime(enabled=True)` 补跑 `on_load`；
- 关闭是静默状态，只留 debug 级技术日志，不产生用户可见日志。
"""

from __future__ import annotations

import types

from app.plugin.loader import activate


class _Log:
    def __init__(self) -> None:
        self.debugs: list[str] = []
        self.warnings: list[str] = []

    def debug(self, message: str) -> None:
        self.debugs.append(str(message))

    def warning(self, message: str) -> None:
        self.warnings.append(str(message))


class _Features:
    def __init__(self) -> None:
        self.acquired: list[object] = []

    def acquire_module(self, instance) -> None:
        self.acquired.append(instance)


class _Services:
    def __init__(self) -> None:
        self.features = _Features()


class _Module:
    module_name = "demo"

    def __init__(self, *, enabled: bool) -> None:
        self.authority = types.SimpleNamespace(enabled=enabled)
        self.load_count = 0

    async def on_load(self) -> None:
        self.load_count += 1


async def test_disabled_module_is_not_activated() -> None:
    log, services = _Log(), _Services()
    module = _Module(enabled=False)

    await activate(module, "demo", services, log)

    assert module.load_count == 0, "关闭的模块不该执行 on_load（会直挂 runtime 能力）"
    assert services.features.acquired == [], "关闭的模块不该建立 Feature 租约"
    assert log.warnings == [], "关闭是静默状态，不该产生用户可见日志"
    assert any("跳过 on_load" in line for line in log.debugs), "应留一条 debug 技术日志"


async def test_enabled_module_is_activated() -> None:
    log, services = _Log(), _Services()
    module = _Module(enabled=True)

    await activate(module, "demo", services, log)

    assert module.load_count == 1
    assert services.features.acquired == [module]


async def test_module_without_authority_keeps_legacy_behaviour() -> None:
    """没有 authority 的旧模块按启用处理（升级平滑）。"""

    class _Bare:
        module_name = "bare"

        def __init__(self) -> None:
            self.load_count = 0

        async def on_load(self) -> None:
            self.load_count += 1

    module = _Bare()
    await activate(module, "bare", None, _Log())

    assert module.load_count == 1
