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

import pytest

from app.agent.assembly.assembler import PromptAssembler, PromptRequest
from app.agent.config_partition import MODULE_KEYS
from app.plugins.llm_assembly.main import OWNED_KEYS, Module, owned_keys
from app.plugins.llm_assembly.service import AssemblyAdapter


def test_owned_keys_match_partition():
    assert set(OWNED_KEYS) == set(MODULE_KEYS["llm_assembly"])
    assert owned_keys() == OWNED_KEYS


def test_default_config_and_schema_cover_owned_keys():
    missing_default = [k for k in OWNED_KEYS if k not in Module.default_config]
    missing_schema = [k for k in OWNED_KEYS if k not in Module.config_schema]
    assert not missing_default, f"default_config 缺: {missing_default}"
    assert not missing_schema, f"config_schema 缺: {missing_schema}"


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


def test_module_profile_drives_behavior_block():
    """`assembly_profile=qq_agent` + 行为规则开启 → `behavior` 块真的产出内容。

    用 `describe()` 按块名断言，比在文本里搜关键词稳（行为段正文由
    `persona_rules` 生成，措辞会变）。
    """
    agent_cfg = {"system_prompt": "你是助手", "assembly_profile": "blocks"}

    plain = PromptAssembler().describe(_req(config=dict(agent_cfg)))
    adapter = AssemblyAdapter(
        SimpleNamespace(
            get=lambda k, d=None: {
                "assembly_profile": "qq_agent",
                "behavior_rules_enable": True,
            }.get(k, d)
        ),
        OWNED_KEYS,
    )
    with_module = PromptAssembler().describe(_req(config=adapter.view_for(dict(agent_cfg))))

    def _behavior_chars(rows):
        return next(r["chars"] for r in rows if r["block"] == "behavior")

    assert _behavior_chars(plain) == 0, "blocks 档不该有行为段"
    assert _behavior_chars(with_module) > 0, "模块配置没有影响到装配结果"


def test_adapter_build_is_equivalent_to_manual_view():
    """`adapter.build(req)` 与"手工把 req.config 换成视图再 build"结果一致。"""
    adapter = AssemblyAdapter(
        SimpleNamespace(get=lambda k, d=None: {"assembly_profile": "qq_agent"}.get(k, d)),
        OWNED_KEYS,
    )
    via_adapter = adapter.build(_req(config={"system_prompt": "你是助手"}))
    manual = PromptAssembler().build(
        _req(config=adapter.view_for({"system_prompt": "你是助手"}))
    )
    assert via_adapter == manual


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


def test_module_missing_key_falls_back_to_agent_config():
    """迁移期：模块还没有该键 → 回退 agent 配置，升级行为不变。"""
    agent_cfg = {"user_message_format": "labeled"}
    adapter = AssemblyAdapter(SimpleNamespace(get=lambda k, d=None: d), OWNED_KEYS)
    assert adapter.view_for(agent_cfg).get("user_message_format") == "labeled"


def test_adapter_restores_req_config_after_build():
    """装配用的配置视图是本轮临时的，build 后必须还原（不污染后续读取）。"""
    original = {"system_prompt": "你是助手"}
    req = _req(config=original)
    adapter = AssemblyAdapter(SimpleNamespace(get=lambda k, d=None: d), OWNED_KEYS)
    adapter.build(req)
    assert req.config is original


def test_adapter_describe_reports_profile():
    adapter = AssemblyAdapter(
        SimpleNamespace(
            get=lambda k, d=None: {"assembly_profile": "qq_agent", "behavior_rules_enable": True}.get(k, d)
        ),
        OWNED_KEYS,
    )
    info = adapter.describe()
    assert info["profile"] == "qq_agent"
    assert info["behavior_rules"] is True
    assert info["owned_keys"] == len(OWNED_KEYS)


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
    module = _make_module(runtime, {"assembly_profile": "qq_agent"})

    await module.on_load()
    assert isinstance(runtime.assembler, AssemblyAdapter)
    assert runtime.assembly_options is not None
    assert runtime.assembly_options.get("assembly_profile") == "qq_agent"

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


async def test_prompt_dump_reads_assembly_options(tmp_path, monkeypatch):
    """`prompt_dump_*` 归本模块：`assembly_options` 里的开关要生效。"""
    from app.agent.assembly import dump

    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    runtime = SimpleNamespace(
        bot_id="777",
        config={"prompt_dump_enable": False},          # agent 侧关着
        assembly_options=SimpleNamespace(
            get=lambda k, d=None: {"prompt_dump_enable": True, "prompt_dump_max_entries": 5}.get(k, d)
        ),
    )
    ok = dump.record(runtime, [{"role": "user", "content": "hi"}], session_id="group_1")
    assert ok is True, "assembly_options 里开启后应当落盘"


@pytest.mark.parametrize("enabled", [False, True])
def test_prompt_dump_falls_back_to_agent_config(tmp_path, monkeypatch, enabled):
    """没有接管者时退回 agent 配置（升级行为不变）。"""
    from app.agent.assembly import dump

    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    runtime = SimpleNamespace(bot_id="778", config={"prompt_dump_enable": enabled})
    assert dump.record(runtime, [{"role": "user", "content": "hi"}]) is enabled
