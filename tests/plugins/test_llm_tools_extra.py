"""`llm_tools_extra`（9 键）模块测试。

验证工具侧 5 个读取点跟随模块配置（OneBot 门控/名单、MCP 列表、
戳一戳冷却、调试开关、工具结果回应要求），卸载后回退。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.config_partition import MODULE_KEYS
from app.agent.policy import policy_get
from app.plugins.llm_tools_extra.module import OWNED_KEYS, Module, owned_keys


def test_owned_keys_match_partition():
    assert set(OWNED_KEYS) == set(MODULE_KEYS["llm_tools_extra"])
    assert owned_keys() == OWNED_KEYS


def test_default_config_and_schema_cover_owned_keys():
    assert not [k for k in OWNED_KEYS if k not in Module.default_config]
    assert not [k for k in OWNED_KEYS if k not in Module.config_schema]


def test_module_declares_capabilities():
    assert "onebot_tools" in Module.supersedes
    assert "mcp" in Module.supersedes
    assert Module.category == "LLM"


# ---------- OneBot 策略 ----------


def test_onebot_policy_reads_module_config():
    from app.agent.tools.onebot.security import resolve_tool_policy

    runtime = SimpleNamespace(
        config={"onebot_tools_enable": False},
        tools_options=SimpleNamespace(
            get=lambda k, d=None: {
                "onebot_tools_enable": True,
                "onebot_tools_denied": ["set_group_ban"],
            }.get(k, d)
        ),
    )
    allowed = resolve_tool_policy(runtime, {"name": "send_group_msg", "permission": "member"})
    denied = resolve_tool_policy(runtime, {"name": "set_group_ban", "permission": "member"})
    assert allowed["enabled"] is True
    assert denied["enabled"] is False


def test_onebot_policy_falls_back_without_module():
    from app.agent.tools.onebot.security import resolve_tool_policy

    runtime = SimpleNamespace(config={"onebot_tools_enable": False})
    policy = resolve_tool_policy(runtime, {"name": "send_group_msg", "permission": "member"})
    assert policy["enabled"] is False


def test_poke_cooldown_reads_module_config():
    from app.agent.tools.onebot.executor import _poke_cooldown

    runtime = SimpleNamespace(
        config={"poke_cooldown_seconds": 99},
        tools_options=SimpleNamespace(get=lambda k, d=None: {"poke_cooldown_seconds": 5}.get(k, d)),
    )
    assert _poke_cooldown(runtime) == 5.0


def test_poke_cooldown_falls_back_without_module():
    from app.agent.tools.onebot.executor import _poke_cooldown

    runtime = SimpleNamespace(config={"poke_cooldown_seconds": 42})
    assert _poke_cooldown(runtime) == 42.0


async def test_onebot_gate_follows_module():
    """模块关掉 OneBot 工具后，工具清单里不该出现 OneBot 来源的工具。"""
    from app.agent.loop.tool_source import _collect_llm_ext

    runtime = SimpleNamespace(
        bot_id="1",
        config={"onebot_tools_enable": True},
        tools_options=SimpleNamespace(
            get=lambda k, d=None: {"onebot_tools_enable": False}.get(k, d)
        ),
        llm_tools=None,
        skills=None,
        memory=None,
        knowledge=None,
        mcp_manager=None,
    )
    specs, _skills, _ctx = await _collect_llm_ext(runtime, None, "group_1", False, False)
    assert not [s for s in specs if s.source == "onebot"]


# ---------- MCP ----------


def test_mcp_reads_module_config():
    from app.agent.tools.mcp import MCPManager

    runtime = SimpleNamespace(
        bot_id="1",
        config={"mcp_servers": []},
        tools_options=SimpleNamespace(
            get=lambda k, d=None: {"mcp_servers": [{"name": "fs", "command": "x"}]}.get(k, d)
        ),
    )
    mgr = MCPManager(runtime)
    assert mgr.enabled() is True


def test_mcp_falls_back_without_module():
    from app.agent.tools.mcp import MCPManager

    runtime = SimpleNamespace(bot_id="2", config={"mcp_servers": []})
    assert MCPManager(runtime).enabled() is False


# ---------- 工具结果回应要求 ----------


def test_reply_directive_reads_module_config():
    from app.agent.loop.tool_loop import reply_directive
    from app.agent.policy import policy_config

    runtime = SimpleNamespace(
        config={"tool_result_directive": "agent 文案"},
        tools_options=SimpleNamespace(
            get=lambda k, d=None: {"tool_result_directive": "模块文案"}.get(k, d)
        ),
    )
    assert reply_directive(policy_config(runtime, "tools")) == "模块文案"


def test_reply_directive_disabled_by_module():
    from app.agent.loop.tool_loop import reply_directive
    from app.agent.policy import policy_config

    runtime = SimpleNamespace(
        config={"tool_result_directive_enable": True},
        tools_options=SimpleNamespace(
            get=lambda k, d=None: {"tool_result_directive_enable": False}.get(k, d)
        ),
    )
    assert reply_directive(policy_config(runtime, "tools")) == ""


# ---------- 装/卸 ----------


class _Runtime:
    def __init__(self):
        self.bot_id = "12345"
        self.config = {"onebot_tools_enable": True}
        self.tools_options = None


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
        module_name="llm_tools_extra",
        bot_id="12345",
        config=ModuleConfig("llm_tools_extra", "12345", Module.default_config, service),
        authority=ModuleAuthority("llm_tools_extra", "12345", service),
        services=ServiceAccess(runtime=lambda bot_id: runtime),
    )
    return type("M", (Module,), {})(ctx)


async def test_on_load_installs_view_and_unload_clears():
    runtime = _Runtime()
    module = _make_module(runtime, {"onebot_tools_enable": False})

    await module.on_load()
    assert policy_get(runtime, "tools", "onebot_tools_enable") is False
    assert module.onebot_enabled is False

    await module.on_unload()
    assert runtime.tools_options is None
    assert policy_get(runtime, "tools", "onebot_tools_enable") is True


async def test_on_load_without_runtime_is_safe():
    from app.plugin.base import ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess

    service = _CfgService()
    ctx = ModuleContext(
        module_name="llm_tools_extra",
        bot_id="1",
        config=ModuleConfig("llm_tools_extra", "1", {}, service),
        authority=ModuleAuthority("llm_tools_extra", "1", service),
        services=ServiceAccess(runtime=lambda bot_id: None),
    )
    module = type("M", (Module,), {})(ctx)
    await module.on_load()
    await module.on_unload()


@pytest.mark.parametrize("key", ["onebot_tools_max_result", "mcp_servers", "tool_result_directive"])
async def test_keys_overridable_by_module(key):
    runtime = _Runtime()
    runtime.config = {key: "agent"}
    module = _make_module(runtime, {key: "module"})
    await module.on_load()
    assert policy_get(runtime, "tools", key) == "module"
    await module.on_unload()
