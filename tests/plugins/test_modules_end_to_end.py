"""端到端：真实容器里 9 个功能模块能被自动装配，并把实现装进运行时。

这是 v3 的最终验收——不是"模块文件存在"，而是"装机即生效"：

1. `registry.load_all(bot_id)` 扫到 9 个模块并实例化；
2. 它们的 `on_load` 把 `runtime.output` / `runtime.assembler` / 各 `*_options`
   视图装好（说明"框架不再自带，全由模块提供"）；
3. 本体 schema 只有 43 键，模块键一个都不在里面。
"""

from __future__ import annotations

import pytest

from app.agent.config_partition import CORE_KEYS, MODULE_KEYS
from app.agent.config_schema import SCHEMA

V3_MODULES = {
    "llm_output",
    "llm_assembly",
    "llm_message_meta",
    "llm_context",
    "llm_memory",
    "llm_knowledge",
    "llm_tools_extra",
    "llm_proactive",
    "llm_schedule",
}


async def test_all_nine_modules_load_and_install_into_runtime(container):
    from app.agent.runtime import AgentManager
    from app.plugin.registry import ModuleRegistry

    registry = container.get(ModuleRegistry)
    manager = container.get(AgentManager)
    bot_id = "999001"

    # 生产顺序（`bot_service.on_bot_login`）：先建运行时，再装配模块。
    # 反过来模块的 on_load 拿不到 runtime，就装不上任何东西。
    runtime = manager.ensure_runtime(bot_id, bot=None)
    assert runtime is not None

    await registry.load_all(bot_id, bot=None)

    loaded = {name for name in registry.loaded_map() if name in V3_MODULES}
    assert loaded == V3_MODULES, f"未装配的模块: {sorted(V3_MODULES - loaded)}"

    # 装槽型能力：对象已被模块替换
    assert runtime.output is not None, "llm_output 应装上输出策略"
    assert runtime.assembler is not None
    # v3 重写后：主动消息/定时任务是业务，通过钩子与基础接口工作，
    # 不再往运行时塞 `proactive`；调度器仍挂在 `runtime.scheduler`
    # （`#llm schedule` 指令要读它，但指令只做"是谁"的查询，不含业务逻辑）。
    assert runtime.scheduler is not None

    # 策略视图：9 个槽都应挂上
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
        assert getattr(runtime, attr, None) is not None, f"{attr} 没被模块挂上"

    # 卸载后全部回到 None（干净可逆）
    await registry.unload(bot_id)
    assert runtime.output is None
    assert runtime.scheduler is None
    assert runtime.meta_options is None


def _settings_type():
    from app.core.settings import Settings

    return Settings


def test_body_schema_has_no_module_keys():
    schema_keys = {
        key
        for key, value in SCHEMA.items()
        if isinstance(value, dict) and value.get("type") != "group"
    }
    migrated = {key for keys in MODULE_KEYS.values() for key in keys}
    assert not (schema_keys & migrated), sorted(schema_keys & migrated)
    assert schema_keys == set(CORE_KEYS)


@pytest.mark.parametrize("module_name", sorted(V3_MODULES))
def test_module_directory_is_importable(module_name):
    import importlib

    mod = importlib.import_module(f"app.plugins.{module_name}.module")
    assert hasattr(mod, "Module")
    assert getattr(mod.Module, "category", "") == "LLM"
    keys = getattr(mod, "OWNED_KEYS")
    assert set(keys) == set(MODULE_KEYS[module_name])
