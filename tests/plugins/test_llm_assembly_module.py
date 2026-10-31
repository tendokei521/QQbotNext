"""`llm_assembly` 模块：接管 `assembly` 槽，自己持有 10 个组装策略键。

验证要点：
1. 键与 `config_partition.MODULE_KEYS["llm_assembly"]` 一致；
2. 模块配置**真的**影响装配结果（这是"策略搬走"的实质）；
3. 与 agent 配置冲突时模块优先，模块缺值时回退；
4. 装/卸 `runtime.assembler` 正确切换；
5. `prompt_dump_*` 通过 `runtime.assembly_options` 生效。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.assembly.assembler import PromptAssembler, PromptRequest
from app.plugins.llm_assembly.main import OWNED_KEYS, Module
from app.plugins.llm_assembly.service import AssemblyAdapter


def test_module_declares_assembly_capability():
    assert "assembly" in Module.supersedes
    assert Module.category == "LLM"


# ---------- 模块配置真的影响装配结果 ----------


def _req(config=None, **over):
    base = dict(
        config=config if config is not None else {},
        session_id="group_1",
        is_private=False,
        user_text="本轮消息",
        history=[{"role": "user", "content": "上一句"}],
    )
    base.update(over)
    return PromptRequest(**base)


def test_module_config_drives_behavior_block():
    """模块自己的配置能改变装配结果：``behavior_rules_override`` 整体替换行为段。

    行为段本身由提示词方案决定（agent 级 ``prompt_preset``）；本模块持有的是
    "覆盖正文"这类细调键，所以用覆盖文本来验证"模块配置真的进了装配"。
    """
    agent_cfg = {"system_prompt": "你是助手", "prompt_preset": "chat"}

    plain = PromptAssembler().describe(_req(config=dict(agent_cfg)))
    adapter = AssemblyAdapter(        SimpleNamespace(
            get=lambda k, d=None: {
                            "behavior_rules_override": "自定义行为规则",
            }.get(k, d)
        ),
        OWNED_KEYS,
    )
    with_module = PromptAssembler().describe(_req(config=adapter.view_for(dict(agent_cfg))))

    plain_req = _req(config=dict(agent_cfg))
    module_adapter = adapter
    module_req = _req(config=module_adapter.view_for(dict(agent_cfg)))

    def _behavior_text(req):
        for msg in PromptAssembler().build(req):
            text = str(msg.get("content") or "")
            if msg.get("role") == "system" and "【安全规则" in text:
                return text
        return ""

    assert _behavior_text(plain_req) != _behavior_text(module_req), "模块配置没有影响到装配结果"
    assert "自定义行为规则" in _behavior_text(module_req)


def test_module_config_overrides_agent_config():
    """同一个键在两边都有值时，以模块为准。"""
    agent_cfg = {"system_prompt": "你是助手", "user_message_format": "default"}
    adapter = AssemblyAdapter(
        SimpleNamespace(get=lambda k, d=None: {"user_message_format": "single_line"}.get(k, d)),
        OWNED_KEYS,
    )
    view = adapter.view_for(agent_cfg)
    assert view.get("user_message_format") == "single_line"
    assert view.get("system_prompt") == "你是助手"


# ---------- 装/卸 ----------


class _Runtime:
    def __init__(self):
        self.bot_id = "12345"
        self.config = {"system_prompt": "你是助手"}
        self.assembler = PromptAssembler()
        self.assembly_options = None


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
    from app.plugin.base import (
        ModuleAuthority,
        ModuleConfig,
        ModuleContext,
        ServiceAccess,
    )

    service = _CfgService(values or {})
    ctx = ModuleContext(
        module_name="llm_assembly",
        bot_id="12345",
        config=ModuleConfig("llm_assembly", "12345", Module.default_config, service),
        authority=ModuleAuthority("llm_assembly", "12345", service),
        services=ServiceAccess(runtime=lambda bot_id: runtime),
    )
    return type("M", (Module,), {})(ctx)


async def test_on_load_installs_adapter_and_options():
    runtime = _Runtime()
    module = _make_module(runtime, {"behavior_rules_override": "自定义"})

    await module.on_load()
    assert isinstance(runtime.assembler, AssemblyAdapter)
    assert runtime.assembly_options is not None
    assert runtime.assembly_options.get("behavior_rules_override") == "自定义"

    await module.on_unload()
    assert isinstance(runtime.assembler, PromptAssembler)
    assert runtime.assembly_options is None


async def test_on_load_without_runtime_is_safe():
    from app.plugin.base import (
        ModuleAuthority,
        ModuleConfig,
        ModuleContext,
        ServiceAccess,
    )

    service = _CfgService()
    ctx = ModuleContext(
        module_name="llm_assembly",
        bot_id="1",
        config=ModuleConfig("llm_assembly", "1", {}, service),
        authority=ModuleAuthority("llm_assembly", "1", service),
        services=ServiceAccess(runtime=lambda bot_id: None),
    )
    module = type("M", (Module,), {})(ctx)
    await module.on_load()
    await module.on_unload()


# ---------- prompt_dump 走模块配置 ----------
