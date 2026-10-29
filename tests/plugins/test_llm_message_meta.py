"""消息元信息模块（14 键）与统一策略配置入口 `policy_config` 的测试。"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.policy import policy_config, policy_get
from app.plugins.llm_message_meta.main import Module

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
    from app.plugins.llm_message_meta.src.service import _ctx_cfg

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
        self.config = {"include_time": False}
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
    from app.plugin.base import ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess

    service = _CfgService(values or {})
    ctx = ModuleContext(
        module_name="llm_message_meta",
        bot_id="12345",
        config=ModuleConfig("llm_message_meta", "12345", Module.default_config, service),
        authority=ModuleAuthority("llm_message_meta", "12345", service),
        services=ServiceAccess(runtime=lambda bot_id: runtime),
    )
    return type("M", (Module,), {})(ctx)


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
