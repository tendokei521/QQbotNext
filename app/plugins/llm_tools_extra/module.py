"""工具扩展模块：接管 OneBot 工具 / MCP / 工具结果回应要求（9 键）。

## 它负责什么

- OneBot 通用工具（6 键）：总开关、黑白名单、结果长度上限、调试日志、戳一戳冷却；
- MCP 工具（1 键）：stdio server 列表；
- 「工具结果回应要求」（2 键）：拼在工具结果末尾、压住模型"复述资料"的那句指令。

## 机制在哪

工具注册/执行/权限/截断仍在框架（`tools/onebot/*`、`tools/mcp.py`、`tools/spec.py`），
本模块只提供策略配置：`on_load` 挂 `runtime.tools_options`，框架侧读取点全部
走 `policy_config(runtime, "tools")`。

> 联网搜索（Tavily）**不在本模块**：它的配置留在本体「高级功能」页。
"""

from __future__ import annotations

from typing import Any

from app.plugin import BaseModule

from .config_schema import SCHEMA

#: 本模块持有的键（与 `config_partition.MODULE_KEYS["llm_tools_extra"]` 一致）
OWNED_KEYS: tuple[str, ...] = (
    "onebot_tools_enable",
    "onebot_tools_allowed",
    "onebot_tools_denied",
    "onebot_tools_max_result",
    "onebot_tools_debug",
    "poke_cooldown_seconds",
    "mcp_servers",
    "tool_result_directive_enable",
    "tool_result_directive",
)


class Module(BaseModule):
    name = "工具扩展"
    sign = "LlmToolsExtra"
    description = "把 OneBot API 与 MCP server 暴露给模型，并控制工具结果的回应要求"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    order = 70
    provides = ("onebot_tools", "mcp")
    supersedes = ("onebot_tools", "mcp")
    default_config = {
        "onebot_tools_enable": False,
        "onebot_tools_allowed": [],
        "onebot_tools_denied": [],
        "onebot_tools_max_result": 2000,
        "onebot_tools_debug": False,
        "poke_cooldown_seconds": 20,
        "mcp_servers": [],
        "tool_result_directive_enable": True,
        "tool_result_directive": "",
    }
    config_schema = SCHEMA

    async def on_load(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        from app.agent.delivery.output import ConfigView

        runtime.tools_options = ConfigView(
            getattr(runtime, "config", None), self.config, OWNED_KEYS
        )

    async def on_unload(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        runtime.tools_options = None

    def _runtime(self) -> Any:
        services = getattr(getattr(self, "ctx", None), "services", None)
        getter = getattr(services, "runtime", None)
        return getter(self.bot_id) if callable(getter) else None

    @property
    def onebot_enabled(self) -> bool:
        return bool(self.config.get("onebot_tools_enable", False))


def owned_keys() -> tuple[str, ...]:
    """本模块持有的配置键（供迁移脚本与契约测试核对）。"""
    return OWNED_KEYS
