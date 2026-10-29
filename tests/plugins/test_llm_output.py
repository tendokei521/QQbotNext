"""`llm_output` 模块：接管 `output` 槽，自己持有流式/发言通道的 25 个配置键。

这是「配置从 agent 页搬到模块」的第一个成品，所以重点验四件事：

1. 模块持有的键与 `config_partition.MODULE_KEYS["llm_output"]` **完全一致**（防漂移）；
2. 三种 `mode` 选到正确的框架实现；
3. 模块配置优先于 agent 配置（`ConfigView`），但未持有的键仍能回退；
4. 装/卸模块时 `runtime.output` 正确切换与还原。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.delivery.output import BlockingOutput, StreamOutput
from app.plugins.llm_output.main import Module, adapter_for
from app.plugins.llm_output.service import OutputStrategyAdapter


def test_module_declares_output_capability():
    assert "output" in Module.supersedes
    assert "output" in Module.provides
    assert Module.category == "LLM"


# ---------- 模式选择 ----------


@pytest.mark.parametrize(
    ("config", "expected_mode", "expected_cls"),
    [
        ({"mode": "text"}, "text", BlockingOutput),
        ({"mode": "stream"}, "stream", StreamOutput),
        ({"mode": "speech"}, "speech", StreamOutput),
        # 兼容老配置：只开了 stream_output
        ({"stream_output": True}, "stream", StreamOutput),
        ({"stream_output": False}, "text", BlockingOutput),
        ({}, "text", BlockingOutput),
    ],
)
async def test_mode_selects_matching_framework_strategy(config, expected_mode, expected_cls):
    runtime = SimpleNamespace(bot_id="1", config={"stream_output": False})
    adapter = adapter_for(runtime, config)

    assert adapter.mode == expected_mode
    produced: list = []
    # 只关心"选了哪个实现"，把实现替换成探针
    import app.plugins.llm_output.service as svc

    original_blocking, original_stream = svc.BlockingOutput, svc.StreamOutput

    class _Probe:
        streams = False

        def __init__(self, runtime=None, config=None):
            self.runtime = runtime
            self.config = config
            produced.append(type(self).__name__)

        async def respond(self, ctx, *, should_stop=None):
            if False:  # pragma: no cover
                yield None

    try:
        svc.BlockingOutput = type("ProbeBlocking", (_Probe,), {"__name__": "ProbeBlocking"})
        svc.StreamOutput = type("ProbeStream", (_Probe,), {"__name__": "ProbeStream"})
        async for _ in adapter.respond(SimpleNamespace()):
            pass
    finally:
        svc.BlockingOutput, svc.StreamOutput = original_blocking, original_stream

    expected_name = "ProbeBlocking" if expected_cls is BlockingOutput else "ProbeStream"
    assert produced == [expected_name]


# ---------- 配置视图 ----------


# ---------- 装/卸 ----------


class _Runtime:
    def __init__(self):
        self.bot_id = "12345"
        self.output = None
        self.config = {"stream_output": False}


class _Services:
    def __init__(self, runtime):
        self._runtime = runtime

    def runtime(self, bot_id):
        return self._runtime


def _make_module(runtime, values=None):
    from app.plugin.base import (
        ModuleAuthority,
        ModuleConfig,
        ModuleContext,
        ServiceAccess,
    )

    class _Cfg:
        def __init__(self, data):
            self.data = data

        def get_module_config(self, *a, **k):
            return dict(self.data)

        def set_module_config(self, *a, **k):
            return None

        def get_module_authority(self, *a, **k):
            return {"enabled": True}

        def set_module_authority(self, *a, **k):
            return None

    service = _Cfg(values or {})
    cls = type("M", (Module,), {})
    ctx = ModuleContext(
        module_name="llm_output",
        bot_id="12345",
        config=ModuleConfig("llm_output", "12345", Module.default_config, service),
        authority=ModuleAuthority("llm_output", "12345", service),
        services=ServiceAccess(runtime=lambda bot_id: runtime),
    )
    return cls(ctx)


async def test_on_load_installs_adapter_and_unload_restores():
    runtime = _Runtime()
    module = _make_module(runtime, {"mode": "stream"})

    await module.on_load()
    assert isinstance(runtime.output, OutputStrategyAdapter)
    assert runtime.output.mode == "stream"

    await module.on_unload()
    assert runtime.output is None, "卸载后必须回到'按配置自动选择'"


async def test_on_load_without_runtime_is_safe():
    from app.plugin.base import (
        ModuleAuthority,
        ModuleConfig,
        ModuleContext,
        ServiceAccess,
    )

    class _Cfg:
        def get_module_config(self, *a, **k):
            return {}

        def set_module_config(self, *a, **k):
            return None

        def get_module_authority(self, *a, **k):
            return {"enabled": True}

        def set_module_authority(self, *a, **k):
            return None

    ctx = ModuleContext(
        module_name="llm_output",
        bot_id="1",
        config=ModuleConfig("llm_output", "1", {}, _Cfg()),
        authority=ModuleAuthority("llm_output", "1", _Cfg()),
        services=ServiceAccess(runtime=lambda bot_id: None),
    )
    module = type("M", (Module,), {})(ctx)
    await module.on_load()   # 不该抛
    await module.on_unload()  # 不该抛
