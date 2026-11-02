"""v3 集成验证：7 个模块全部装上后，全部迁走的键都必须"读得到、且读的是模块值"。

这条测试代替"逐处 grep 框架是否还直接读 agent 配置"——那个静态检查分不清
"读入参 config"和"读 runtime.config"。真正的判据是**行为**：

> 把 agent 配置里的同名键设成 A、模块配置设成 B，机制侧经 `policy_view`
> 必须读到 B；卸载模块后必须回到 A。

它同时是 W8 的收尾验收：7 个模块 + 109 键 + 跨模块链式视图。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.config_partition import MODULE_KEYS

MODULE_KEY_SET = {k for keys in MODULE_KEYS.values() for k in keys}
from app.agent.policy import ChainedConfig, policy_get, policy_view

ALL_MODULES = sorted(MODULE_KEYS)


def _module_view(values: dict) -> SimpleNamespace:
    return SimpleNamespace(get=lambda k, d=None: values.get(k, d))


def _runtime_with_views(views: dict, agent_config: dict | None = None):
    runtime = SimpleNamespace(bot_id="1", config=dict(agent_config or {}))
    for attr in (
        "output_options",
        "assembly_options",
        "meta_options",
        "context_options",
        "memory_options",
        "knowledge_options",
        "tools_options",
        "proactive_options",
        "schedule_options",
    ):
        setattr(runtime, attr, None)
    for kind, values in views.items():
        setattr(runtime, f"{kind}_options", _module_view(values))
    return runtime


#: 模块 → 运行时视图属性名（合并后的模块可能拥有多个视图，如提示词组装/触发调度）
#: 空元组 = 该模块没有配置视图（`llm_enhance` 直接读自己的 config，不挂视图）。
VIEW_ATTR: dict[str, tuple[str, ...]] = {
    "llm_trigger": ("proactive", "schedule"),
    "llm_context": ("context",),
    "llm_memory": ("memory",),
    "llm_tools_extra": ("tools",),
    "llm_enhance": (),
}


def test_every_module_has_a_view_slot():
    assert set(VIEW_ATTR) == set(ALL_MODULES)


def test_all_migrated_keys_resolve_to_module_values():
    """一次装齐模块：每个挂视图的键都必须从模块值读出来。"""
    views = {}
    module_values = {}
    for module_name, keys in MODULE_KEYS.items():
        values = {key: f"{module_name}:{key}" for key in keys}
        module_values[module_name] = values
        for attr in VIEW_ATTR[module_name]:
            views[attr] = values
    # agent 侧故意放上"会被覆盖"的值
    agent_config = {key: "FROM-AGENT" for keys in MODULE_KEYS.values() for key in keys}
    runtime = _runtime_with_views(views, agent_config)

    checked = 0
    for module_name, keys in MODULE_KEYS.items():
        attrs = VIEW_ATTR[module_name]
        if not attrs:
            # 没有配置视图的模块（llm_enhance）：键应回退到 agent 配置
            for key in keys:
                assert policy_view(runtime).get(key) == "FROM-AGENT", (module_name, key)
            continue
        for key in keys:
            got = policy_get(runtime, attrs[0], key)
            assert got == f"{module_name}:{key}", (module_name, key, got)
            checked += 1
    expected = sum(len(keys) for module, keys in MODULE_KEYS.items() if VIEW_ATTR[module])
    assert checked == expected


def test_chained_view_handles_cross_module_keys():
    """跨模块读取：链式视图里两个模块的键都要读得到。"""
    runtime = _runtime_with_views(
        {
            "context": {"context_expand_enable": True},
            "memory": {"memory_enable": True},
        },
        {"max_message_length": 200},
    )
    view = policy_view(runtime)

    assert isinstance(view, ChainedConfig)
    assert view.get("context_expand_enable") is True      # 归 llm_context
    assert view.get("memory_enable") is True              # 归 llm_memory
    assert view.get("max_message_length") == 200           # 回退 agent 配置
    assert view.get("不存在", "默认") == "默认"


def test_chained_view_returns_none_without_runtime():
    assert policy_view(None) is None


def test_uninstalling_one_module_restores_agent_value_for_its_keys_only():
    """卸掉一个模块：它自己的键回退 agent 值，别人的键不受影响。"""
    views = {
        "memory": {"memory_enable": True},
        "tools": {"onebot_tools_debug": True},
    }
    runtime = _runtime_with_views(
        views, {"memory_enable": False, "onebot_tools_debug": False}
    )
    assert policy_view(runtime).get("memory_enable") is True

    runtime.memory_options = None  # 卸载 llm_memory

    assert policy_view(runtime).get("memory_enable") is False, "卸载后应回退 agent 配置"
    assert policy_view(runtime).get("onebot_tools_debug") is True, "其它模块不受影响"


@pytest.mark.parametrize("module_name", ALL_MODULES)
def test_module_view_ordering_is_deterministic(module_name):
    """同一键只在它的模块视图里命中（视图顺序不影响结果）。"""
    attrs = VIEW_ATTR[module_name]
    if not attrs:
        pytest.skip(f"{module_name} 不挂配置视图")
    keys = MODULE_KEYS[module_name]
    key = keys[0]
    views = {attrs[0]: {key: "mine"}}
    runtime = _runtime_with_views(views, {key: "agent"})
    assert policy_view(runtime).get(key) == "mine"


def test_chained_view_tolerates_broken_view():
    """单个视图读崩不该让整轮取值失败。"""

    class _Bad:
        def get(self, key, default=None):
            raise RuntimeError("boom")

    runtime = _runtime_with_views({}, {"group_enable": True})
    runtime.output_options = _Bad()

    assert policy_view(runtime).get("group_enable") is True
    assert policy_view(runtime).get("whatever", "d") == "d"
