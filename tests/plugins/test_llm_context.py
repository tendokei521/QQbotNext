"""`llm_context` 模块（10 键）与各读取点的接线测试。

重点验证"策略真的搬走了"：把模块值设成与 agent 值相反，断言**框架机制侧的读取**
（指代、工具前置条件、历史查询阈值）跟着模块走，卸载后回退。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.policy import policy_get
from app.plugins.llm_context.main import Module

# ---------- 框架侧读取点跟随模块 ----------


def test_referent_reads_module_config():
    from app.plugins.llm_context.src.referent import _cfg

    runtime = SimpleNamespace(
        config={"referent_prefetch_max": 9},
        context_options=SimpleNamespace(
            get=lambda k, d=None: {"referent_prefetch_max": 3}.get(k, d)
        ),
    )
    assert _cfg(runtime, "referent_prefetch_max", 2) == 3


def test_session_tools_read_module_config():
    from app.agent.tools.session_tools import _cfg

    runtime = SimpleNamespace(
        config={"history_auto_qq_min_local": 20},
        context_options=SimpleNamespace(
            get=lambda k, d=None: {"history_auto_qq_min_local": 1}.get(k, d)
        ),
    )
    ctx = SimpleNamespace(runtime=runtime)
    assert _cfg(ctx, "history_auto_qq_min_local", 4) == 1


async def test_tool_source_expand_gate_follows_module():
    """`context_expand_enable` 关掉后，`expand_*` 工具不再注入。"""
    from app.agent.loop.tool_source import _collect_llm_ext

    runtime = SimpleNamespace(
        bot_id="1",
        config={"context_expand_enable": True},   # agent 侧开着
        context_options=SimpleNamespace(          # 模块侧关掉
            get=lambda k, d=None: {"context_expand_enable": False}.get(k, d)
        ),
        llm_tools=None,
        skills=None,
        memory=None,
        knowledge=None,
        mcp_manager=None,
    )
    specs, _skills, _ctx = await _collect_llm_ext(runtime, None, "group_1", False, False)
    names = {s.name for s in specs}
    assert not {"expand_recent", "expand_message", "expand_user"} & names


# ---------- 装/卸 ----------


class _Hooks:
    def __init__(self):
        self.registered: list[dict] = []

    def register(self, **kwargs):
        self.registered.append(kwargs)


class _Runtime:
    def __init__(self):
        self.bot_id = "12345"
        self.config = {"context_expand_enable": True}
        self.context_options = None
        self.context_providers: list = []
        self.tool_sources: list = []
        self.llm_hooks = _Hooks()


class _CfgService:
    def __init__(self, data=None):
        self.data = data or {}

    def get_module_config(self, *a, **k):
        return dict(self.data)

    def set_module_config(self, *a, **k):
        return None

    def get_module_authority(self, *a, **k):
        return {"enabled": True}

    def set_module_authority(self, *a, **k):
        return None


def _make_module(runtime, values=None):
    from app.plugin.base import ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess

    service = _CfgService(values or {})
    ctx = ModuleContext(
        module_name="llm_context",
        bot_id="12345",
        config=ModuleConfig("llm_context", "12345", Module.default_config, service),
        authority=ModuleAuthority("llm_context", "12345", service),
        services=ServiceAccess(runtime=lambda bot_id: runtime),
    )
    return type("M", (Module,), {})(ctx)


async def test_on_load_installs_view_and_unload_clears():
    runtime = _Runtime()
    module = _make_module(runtime, {"context_expand_enable": False})

    await module.on_load()
    assert policy_get(runtime, "context", "context_expand_enable") is False
    assert [p.name for p in runtime.context_providers] == ["referent"]
    assert [s.name for s in runtime.tool_sources] == ["context_expand"]

    await module.on_unload()
    assert runtime.context_options is None
    assert policy_get(runtime, "context", "context_expand_enable") is True


async def test_on_load_without_runtime_is_safe():
    from app.plugin.base import ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess

    service = _CfgService()
    ctx = ModuleContext(
        module_name="llm_context",
        bot_id="1",
        config=ModuleConfig("llm_context", "1", {}, service),
        authority=ModuleAuthority("llm_context", "1", service),
        services=ServiceAccess(runtime=lambda bot_id: None),
    )
    module = type("M", (Module,), {})(ctx)
    await module.on_load()
    await module.on_unload()
