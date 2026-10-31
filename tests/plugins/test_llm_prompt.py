"""`llm_prompt` 模块（提示词组装）：组装 7 键 + 消息元信息 14 键，共 21 键。

由 `llm_assembly` 与 `llm_message_meta` 两个旧模块合并而来，断言随实现一起搬，
只改模块路径/导入。验证要点：

1. 键与设计一致（21 键，键名一字未改）；
2. 模块配置**真的**影响装配结果与元信息渲染；
3. 与 agent 配置冲突时模块优先，模块缺值时回退；
4. 装/卸 `runtime.assembler` / `assembly_options` / `meta_options` 正确切换；
5. `prompt_dump_*` 通过 `runtime.assembly_options` 生效；
6. 元信息两个 `pre_request` 钩子由本模块自己注册。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.assembly.assembler import PromptAssembler, PromptRequest
from app.agent.policy import policy_config, policy_get
from app.plugins.llm_prompt.main import ASSEMBLY_KEYS, META_KEYS, OWNED_KEYS, Module
from app.plugins.llm_prompt.src.assembly import AssemblyAdapter

# ---------- 键归属 ----------


def test_owned_keys_are_the_union_of_both_old_modules():
    """21 = 组装 7 + 元信息 14，顺序先组装后元信息。"""
    assert len(OWNED_KEYS) == 21
    assert OWNED_KEYS == ASSEMBLY_KEYS + META_KEYS
    assert len(ASSEMBLY_KEYS) == 7
    assert len(META_KEYS) == 14
    assert set(OWNED_KEYS) == set(Module.default_config)
    assert set(OWNED_KEYS) == set(Module.config_schema)


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

    adapter = AssemblyAdapter(
        SimpleNamespace(
            get=lambda k, d=None: {
                "behavior_rules_override": "自定义行为规则",
            }.get(k, d)
        ),
        ASSEMBLY_KEYS,
    )

    plain_req = _req(config=dict(agent_cfg))
    module_req = _req(config=adapter.view_for(dict(agent_cfg)))

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
        ASSEMBLY_KEYS,
    )
    view = adapter.view_for(agent_cfg)
    assert view.get("user_message_format") == "single_line"
    assert view.get("system_prompt") == "你是助手"


# ---------- policy_config 统一入口 ----------


def test_policy_config_prefers_module_view():
    base = {"include_time": False, "group_enable": True}
    view = SimpleNamespace(get=lambda k, d=None: {"include_time": True}.get(k, d))
    runtime = SimpleNamespace(config=base, meta_options=view)

    assert policy_config(runtime, "meta") is view
    assert policy_get(runtime, "meta", "include_time") is True


def test_policy_config_falls_back_to_agent_config():
    base = {"include_time": False}
    runtime = SimpleNamespace(config=base)
    assert policy_config(runtime, "meta") is base
    assert policy_get(runtime, "meta", "include_time", True) is False


# ---------- 元信息渲染真的走模块配置 ----------


def test_enhance_reads_module_meta_config():
    """`_ctx_cfg` 必须读 `runtime.meta_options`（这是"策略搬进模块"的实证）。"""
    from app.plugins.llm_prompt.src.meta import _ctx_cfg

    base = {"include_time": False, "include_sender": False}
    view = SimpleNamespace(
        get=lambda k, d=None: {"include_time": True, "include_sender": False}.get(k, d)
    )
    runtime = SimpleNamespace(config=base, meta_options=view)
    ctx = SimpleNamespace(runtime=runtime)

    assert _ctx_cfg(ctx, "include_time", False) is True
    assert _ctx_cfg(ctx, "include_sender", True) is False


def test_history_cleaner_uses_module_config():
    """`clean_output_parentheses` 归本模块：关掉后历史里保留括号。"""
    from app.agent.loop.assistant_output import _clean_output_for_history

    view = SimpleNamespace(get=lambda k, d=None: {"clean_output_parentheses": False}.get(k, d))
    kept = _clean_output_for_history(view, "说话（小声）结束")
    assert "（小声）" in kept

    enabled = SimpleNamespace(get=lambda k, d=None: True)
    stripped = _clean_output_for_history(enabled, "说话（小声）结束")
    assert "（小声）" not in stripped


# ---------- 装/卸 ----------


class _Hooks:
    """最小钩子注册表：记录模块注册了什么。"""

    def __init__(self):
        self.registered: list[dict] = []

    def register(self, **kwargs):
        self.registered.append(kwargs)


class _Runtime:
    def __init__(self):
        self.bot_id = "12345"
        # 合并前两份 _Runtime 的 agent 配置并集：组装侧读 system_prompt，
        # 元信息侧读 include_time（卸载后应回退到这里的值）
        self.config = {"system_prompt": "你是助手", "include_time": False}
        self.assembler = PromptAssembler()
        self.assembly_options = None
        self.meta_options = None
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
    from app.plugin.base import (
        ModuleAuthority,
        ModuleConfig,
        ModuleContext,
        ServiceAccess,
    )

    service = _CfgService(values or {})
    ctx = ModuleContext(
        module_name="llm_prompt",
        bot_id="12345",
        config=ModuleConfig("llm_prompt", "12345", Module.default_config, service),
        authority=ModuleAuthority("llm_prompt", "12345", service),
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
        module_name="llm_prompt",
        bot_id="1",
        config=ModuleConfig("llm_prompt", "1", {}, service),
        authority=ModuleAuthority("llm_prompt", "1", service),
        services=ServiceAccess(runtime=lambda bot_id: None),
    )
    module = type("M", (Module,), {})(ctx)
    await module.on_load()
    await module.on_unload()


async def test_on_load_installs_view_and_unload_clears():
    runtime = _Runtime()
    module = _make_module(runtime, {"include_time": True})

    await module.on_load()
    assert runtime.meta_options is not None
    assert policy_get(runtime, "meta", "include_time") is True
    # 钩子是模块自己注册的（框架不再 install 元信息钩子）
    stages = {(h["stage"], h["order"]) for h in runtime.llm_hooks.registered}
    assert ("pre_request", -100) in stages and ("pre_request", 20) in stages

    await module.on_unload()
    assert runtime.meta_options is None
    assert policy_get(runtime, "meta", "include_time") is False


# ---------- owned 子集不能合并 ----------


async def test_assembly_view_only_owns_assembly_keys():
    """装配视图只叠组装 7 键；元信息视图只叠元信息 14 键（合并成 21 会改行为）。"""
    runtime = _Runtime()
    module = _make_module(runtime, {"include_time": False, "user_message_format": "single_line"})

    await module.on_load()
    assert runtime.assembler.owned_keys == ASSEMBLY_KEYS
    assert runtime.meta_options.owned_keys == frozenset(META_KEYS)
    # 元信息键没有泄漏进装配视图（否则 request.dump / 行为规则读到的口径会变）
    assert "include_time" not in runtime.assembly_options.owned_keys
    await module.on_unload()
