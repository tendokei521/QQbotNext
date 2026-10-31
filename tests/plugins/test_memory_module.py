"""长期记忆模块契约测试（v3）：装配接线 / 配置键 / 启用门 / 卸载。"""

from __future__ import annotations

from app.agent.config_partition import MODULE_KEYS
from app.agent.policy import policy_get
from app.plugins.llm_memory.main import OWNED_KEYS, Module
from app.plugins.llm_memory.src.schemas import MEMORY_SCHEMA


class _Hooks:
    def __init__(self) -> None:
        self.registered: list[dict] = []

    def register(self, **kwargs):
        self.registered.append(kwargs)


class _Runtime:
    """真实 v3 运行时形状：没有 memory 字段，只有模块挂的句柄/视图槽。"""

    def __init__(self) -> None:
        self.bot_id = "12345"
        self.config = {}
        self.memory_options = None
        self.memory_service = None
        self.context_providers: list = []
        self.tool_sources: list = []
        self.llm_hooks = _Hooks()


class _CfgService:
    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})

    def get_module_config(self, *args, **kwargs):
        return dict(self.data)

    async def save_module_config(self, *args, **kwargs):
        return None

    def get_module_authority(self, *args, **kwargs):
        return {"enabled": True}

    def set_module_authority(self, *args, **kwargs):
        return None


def _make_module(runtime: _Runtime, values: dict | None = None) -> Module:
    from app.plugin.base import ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess

    service = _CfgService(values or {})
    ctx = ModuleContext(
        module_name="llm_memory",
        bot_id="12345",
        config=ModuleConfig("llm_memory", "12345", Module.default_config, service),
        authority=ModuleAuthority("llm_memory", "12345", service),
        services=ServiceAccess(runtime=lambda bot_id: runtime),
    )
    return Module(ctx)


def test_config_keys_are_single_source():
    assert set(Module.default_config) == set(OWNED_KEYS) == set(MEMORY_SCHEMA)
    assert set(OWNED_KEYS) == set(MODULE_KEYS["llm_memory"])
    for key, field in MEMORY_SCHEMA.items():
        assert Module.default_config[key] == field.get("default"), key


def test_enable_gate_has_no_legacy_experimental_switch(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    runtime = _Runtime()  # agent 配置为空：不该再被 experimental_long_term_memory 卡住
    module = _make_module(runtime, {"memory_enable": True})

    # 装配后模块视图生效：只由 memory_enable 决定
    asyncio.run(module.on_load())
    assert module.memory_manager().enabled() is True
    asyncio.run(module.on_unload())


def test_on_load_wires_providers_tools_hooks_and_service(tmp_path, monkeypatch):
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    runtime = _Runtime()
    runtime.config = {"memory_enable": False}
    module = _make_module(runtime, {"memory_enable": True})

    import asyncio

    asyncio.run(module.on_load())

    assert runtime.memory_service is module.memory_manager()
    assert policy_get(runtime, "memory", "memory_enable") is True
    assert [provider.name for provider in runtime.context_providers] == ["memory"]
    assert [source.name for source in runtime.tool_sources] == ["memory_tools"]
    stages = {(hook["stage"], hook["order"]) for hook in runtime.llm_hooks.registered}
    assert ("pre_request", 60) in stages and ("turn_completed", 60) in stages

    asyncio.run(module.on_unload())
    assert runtime.memory_service is None and runtime.memory_options is None
    assert runtime.context_providers == [] and runtime.tool_sources == []


def test_commands_cover_memory_surface():
    from app.plugin.commands import CommandSpec

    names = {spec.name: spec for spec, _method in Module.collect_commands()}
    assert {"memory", "memory list", "memory search", "memory profile", "memory remember",
            "memory forget", "memory clear", "memory audit", "memory export"} <= set(names)
    memory_group: CommandSpec = names["memory"]
    # 兼容旧习惯：#llm memory 与 /memory 都能触发
    assert "#llm " in memory_group.prefixes and "/" in memory_group.prefixes
