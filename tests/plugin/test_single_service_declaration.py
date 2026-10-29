"""单一服务是**声明制**能力：声明位、元数据下发、以及「只允许响应式模块声明」。

三层门控的证据链：

1. `BaseModule.supports_single_service`（声明位，默认 False）；
2. 运行期只对声明模块生效 → `tests/plugin/test_authority.py` / `test_dispatcher.py`；
3. 写入只收声明模块 → `tests/webui/test_webui_single_service_api.py`；
4. 元数据下发（前端据此决定是否显示开关）→ 本文件。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app.plugin.loader as loader
from app.application.bot_service import BotService
from app.plugin.base import BaseModule
from app.plugin.registry import ModuleRegistry

PLUGINS_DIR = Path(__file__).resolve().parents[2] / "app" / "plugins"


async def _payload_for(container, *module_names):
    """把注册表指向仓库真实插件目录，加载指定模块后取 WebUI 元数据。"""
    registry = container.get(ModuleRegistry)
    registry.plugins_dir = PLUGINS_DIR
    bot_id = 20251001
    for name in module_names:
        assert await registry.load_single(name, bot_id) is True, f"{name} 未能加载"
    try:
        return container.get(BotService).get_modules_data(bot_id)
    finally:
        for name in module_names:
            await registry.unload_single(name, bot_id)


async def test_agent_virtual_module_declares_support(container):
    """框架级 Agent 恒支持单一服务（它没有 BaseModule 实例，走代理声明）。"""
    data = await _payload_for(container)

    assert data["agent"]["single_service"] is True


async def test_declaration_defaults_to_false_on_class():
    """新增模块默认不声明：能力必须是显式声明出来的。"""
    class _Plain(BaseModule):
        name = "plain"

    assert _Plain.supports_single_service is False
    assert _Plain.single_service_key == ""


def _local_module_classes():
    """导入每个本地插件的 Module 类（导入后清掉合成包名，避免污染 sys.modules）。"""
    for path in sorted(PLUGINS_DIR.iterdir()):
        if not (path / "main.py").is_file():
            continue
        try:
            mod = loader.import_plugin_module(path.name, path)
            cls = getattr(mod, "Module", None)
            if isinstance(cls, type) and issubclass(cls, BaseModule):
                yield path.name, cls
        finally:
            loader.purge_module_cache(path.name)


@pytest.mark.parametrize("module_name", ["llm_assembly", "llm_output", "llm_memory"])
def test_llm_strategy_modules_stay_undeclared(module_name):
    """纯 LLM 策略模块不声明（subscribe=()，单一服务对它们没有意义）。"""
    cls = dict(_local_module_classes()).get(module_name)
    assert cls is not None, f"{module_name} 未找到"
    assert cls.supports_single_service is False
