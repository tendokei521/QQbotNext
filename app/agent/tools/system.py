"""系统工具统一目录：集中收集/展示框架内置的系统级 LLM 工具。

与 ``loop/chat.py`` 的 ``_collect_llm_ext`` 保持同源：系统工具先经过功能前置开关
判断 ready（策略见 ``app.agent.tools.policy``），再由 ``system_tools_enabled``
决定用户是否单独启用。

## 本模块只负责

「把内置系统工具**收集起来并标注状态**」供 WebUI 展示（``list_system_tools``）。
真正决定注入哪些工具的是 ``tools.policy.is_system_tool_enabled``——主循环用它，
不依赖本模块，因此本模块可以自由 import 各工具实现而不制造环。
"""

from __future__ import annotations

from typing import Any

from app.agent.tools.policy import cfg_get, prereq_state
from app.agent.tools.spec import ToolSpec


def _spec_meta(spec: ToolSpec, ready: bool, prerequisite: str, enabled: bool = True) -> dict:
    return {
        "name": spec.name,
        "description": spec.description,
        "parameters": spec.parameters,
        "category": spec.category or "系统工具",
        "source": spec.source,
        "enabled": bool(enabled),
        "ready": bool(ready),
        "effective": bool(enabled and ready),
        "prerequisite": prerequisite,
    }


def _build_specs(runtime: Any) -> list[ToolSpec]:
    """构造全部内置系统工具的 ToolSpec（仅用于元数据/列表展示）。"""
    specs: list[ToolSpec] = []

    from app.agent.tools.schedule import build_schedule_tool

    specs.append(build_schedule_tool(runtime, "group_0", False))

    from app.agent.tools.session_tools import build_session_tools

    specs.extend(build_session_tools(runtime, None))

    from app.plugins.llm_context.context_tools import build_context_tools

    specs.extend(build_context_tools(runtime, None))

    from app.agent.tools.web_search import build_tavily_tool

    specs.append(build_tavily_tool(runtime))

    if getattr(runtime, "memory", None) is not None:
        from app.plugins.llm_memory import build_memory_tools

        specs.extend(build_memory_tools(runtime, "group_0", "", False))

    if getattr(runtime, "knowledge", None) is not None:
        from app.plugins.llm_knowledge import build_knowledge_tools

        specs.extend(build_knowledge_tools(runtime))

    return specs


def list_system_tools(runtime: Any) -> list[dict]:
    """返回当前 Bot 的内置系统工具清单（含用户开关/前置开关状态）。"""
    enabled_map = cfg_get(runtime, "system_tools_enabled", {}) or {}
    result: list[dict] = []

    for spec in _build_specs(runtime):
        ready, prerequisite = prereq_state(runtime, spec.name)
        user_enabled = enabled_map.get(spec.name, True) if isinstance(enabled_map, dict) else True
        meta = _spec_meta(spec, ready, prerequisite)
        meta["enabled"] = bool(user_enabled)
        meta["effective"] = bool(user_enabled and ready)
        result.append(meta)

    return result
