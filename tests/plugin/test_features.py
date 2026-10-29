"""FeatureRegistry 测试（v3：只剩 4 个**高级接口**能力）。

v3 口径：
- 能力 = 可被接管的**机制**（`assembly` / `provider_invoker` / `output` / `agent`）；
- 业务（记忆/知识库/主动消息/定时任务/工具/元信息/指代）不是能力，由模块用
  基础接口自己实现，因此**不再出现在注册表里**。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.plugin.features import FeatureRegistry
from app.plugin.features.builtin import register_builtin_controllers


class _Owner:
    def __init__(self, name: str, **attrs):
        self.name = name
        for key, value in attrs.items():
            setattr(self, key, value)


class _AgentManager:
    def __init__(self, runtime=None):
        self._runtime = runtime

    def get_runtime(self, bot_id):
        return self._runtime


def _rt(**attrs):
    """最小可用运行时：够 4 个高级接口的 status()/suppress() 跑。"""
    base = {"bot_id": "1", "config": SimpleNamespace(enabled=True)}
    base.update(attrs)
    return SimpleNamespace(**base)


def _registry(runtime=None) -> FeatureRegistry:
    registry = FeatureRegistry()
    register_builtin_controllers(registry, _AgentManager(runtime))
    return registry


# ---------- 清单 ----------


# ---------- 装槽型：接管 / 交棒 / 还原 ----------


def test_output_feature_swaps_and_restores():
    runtime = SimpleNamespace(bot_id="1", output=None)
    registry = _registry(runtime)

    async def _respond(ctx, should_stop=None):
        pass

    owner = _Owner("自定义输出", respond=_respond)

    registry.suppress("output", owner, "1")
    assert runtime.output is owner

    registry.release("output", owner, "1")
    assert runtime.output is None, "释放后回到 None（框架按配置自动选择）"


def test_output_feature_rejects_owner_without_respond():
    runtime = SimpleNamespace(bot_id="1", output=None)
    registry = _registry(runtime)
    registry.suppress("output", _Owner("坏的"), "1")
    assert runtime.output is None


def test_rejected_takeover_does_not_leave_a_lease():
    """接管被拒（缺方法）时不能记租约：否则状态页显示"已被接管"，运行时却没换。"""
    runtime = SimpleNamespace(bot_id="1", output=None, assembler="default")
    registry = _registry(runtime)

    got = registry.suppress("output", _Owner("坏的"), "1")

    assert got == {}
    assert registry.is_suppressed("output", "1") is False
    assert runtime.output is None


def test_takeover_on_unassembled_runtime_is_refused():
    registry = _registry(runtime=None)

    got = registry.suppress("assembly", _Owner("插件"), "9")

    assert got == {}
    assert registry.is_suppressed("assembly", "9") is False


def test_assembly_feature_swaps_and_restores():
    runtime = SimpleNamespace(bot_id="1", assembler="default")
    registry = _registry(runtime)

    class _Assembler:
        def build(self, req):
            return []

    owner = _Owner("我的组装", build=lambda req: [])
    registry.suppress("assembly", owner, "1")
    assert runtime.assembler is owner

    registry.release("assembly", owner, "1")
    assert runtime.assembler is not owner


def test_provider_invoker_feature_swaps_and_restores():
    from app.agent.providers.invoker import ProviderInvoker

    runtime = SimpleNamespace(bot_id="1", invoker=ProviderInvoker())
    registry = _registry(runtime)

    async def _chat(**kw):
        return None

    async def _stream(**kw):
        return
        yield  # pragma: no cover

    owner = _Owner("我的调用策略", chat=_chat, stream=_stream)
    registry.suppress("provider_invoker", owner, "1")
    assert runtime.invoker is owner

    registry.release("provider_invoker", owner, "1")
    assert isinstance(runtime.invoker, ProviderInvoker)


# ---------- 多租约 ----------


def test_multiple_owners_first_wins_and_last_release_restores():
    runtime = SimpleNamespace(bot_id="1", output=None)
    registry = _registry(runtime)

    async def _respond(ctx, should_stop=None):
        pass

    first = _Owner("先来", respond=_respond)
    second = _Owner("后到", respond=_respond)

    registry.suppress("output", first, "1")
    registry.suppress("output", second, "1")
    assert runtime.output is first, "多租约「先到先得」"

    registry.release("output", second, "1")
    assert runtime.output is first, "只释放后到者不该还原"

    registry.release("output", first, "1")
    assert runtime.output is None


def test_status_reports_owners():
    runtime = SimpleNamespace(bot_id="1", output=None)
    registry = _registry(runtime)

    async def _respond(ctx, should_stop=None):
        pass

    owner = _Owner("我的输出", respond=_respond)
    registry.suppress("output", owner, "1")

    status = registry.query("output", "1")
    assert status["registered"] is True
    assert status["suppressed"] is True
    assert status["owners"][0]["name"] == "我的输出"


def test_unknown_feature_is_ignored():
    registry = _registry(SimpleNamespace(bot_id="1"))
    assert registry.suppress("memory", _Owner("x"), "1") == {}
    assert registry.suppress("不存在的能力", _Owner("y"), "1") == {}


def test_global_instance_cannot_take_over_bot_feature():
    registry = _registry(SimpleNamespace(bot_id=None))
    assert registry.suppress("output", _Owner("x"), None) == {}


@pytest.mark.parametrize("feature_id", ["agent", "assembly", "output", "provider_invoker"])
def test_every_feature_reports_status(feature_id):
    runtime = _rt()
    registry = _registry(runtime)
    status = registry.status("1")
    ids = [item["feature_id"] for item in status]
    assert feature_id in ids, f"缺 {feature_id}；实际 {ids}"
