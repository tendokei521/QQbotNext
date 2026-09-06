"""系统工具统一目录：集中收集/展示框架内置的系统级 LLM 工具。

与 ``chat._collect_llm_ext`` 保持同源：系统工具先经过功能前置开关（如
memory_enable / knowledge_enable / schedule_enable）判断 ready，再由
``system_tools_enabled`` 决定用户是否单独启用。
"""

from __future__ import annotations

from typing import Any

from app.llm.tool import ToolSpec

_SYSTEM_TOOL_PREREQUISITES: dict[str, tuple[str, str]] = {
    "schedule_task": ("schedule_enable", "定时任务未启用"),
    "get_current_session": ("", "始终可用"),
    "get_session_history": ("", "始终可用"),
    "tavily_search": ("tavily_enable", "Tavily 联网搜索未启用"),
    "memory_save": ("memory_enable", "长期记忆未启用"),
    "memory_recall": ("memory_enable", "长期记忆未启用"),
    "memory_delete": ("memory_enable", "长期记忆未启用"),
    "memory_correct": ("memory_enable", "长期记忆未启用"),
    "memory_deny": ("memory_enable", "长期记忆未启用"),
    "knowledge_add": ("knowledge_enable", "知识库未启用"),
    "knowledge_search": ("knowledge_enable", "知识库未启用"),
    "knowledge_delete": ("knowledge_enable", "知识库未启用"),
}


def _cfg_get(runtime: Any, key: str, default: Any = None) -> Any:
    try:
        config = getattr(runtime, "config", None)
        return config.get(key, default) if config is not None else default
    except Exception:
        return default


def _prereq_state(runtime: Any, name: str) -> tuple[bool, str]:
    key, label = _SYSTEM_TOOL_PREREQUISITES.get(name, ("", ""))
    if not key:
        return True, ""
    if name.startswith("memory_"):
        memory = getattr(runtime, "memory", None)
        if memory is not None and hasattr(memory, "enabled"):
            return bool(memory.enabled()), label
    return bool(_cfg_get(runtime, key, False)), label


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

    from app.llm.scheduler import build_schedule_tool

    specs.append(build_schedule_tool(runtime, "group_0", False))

    from app.llm.session_tools import build_session_tools

    specs.extend(build_session_tools(runtime, None))

    from app.llm.tavily_search import build_tavily_tool

    specs.append(build_tavily_tool(runtime))

    if getattr(runtime, "memory", None) is not None:
        from app.llm.memory import build_memory_tools

        specs.extend(build_memory_tools(runtime, "group_0", "", False))

    if getattr(runtime, "knowledge", None) is not None:
        from app.llm.knowledge import build_knowledge_tools

        specs.extend(build_knowledge_tools(runtime))

    return specs


def list_system_tools(runtime: Any) -> list[dict]:
    """返回当前 Bot 的内置系统工具清单（含用户开关/前置开关状态）。"""
    enabled_map = _cfg_get(runtime, "system_tools_enabled", {}) or {}
    result: list[dict] = []

    for spec in _build_specs(runtime):
        ready, prerequisite = _prereq_state(runtime, spec.name)
        user_enabled = enabled_map.get(spec.name, True) if isinstance(enabled_map, dict) else True
        meta = _spec_meta(spec, ready, prerequisite)
        meta["enabled"] = bool(user_enabled)
        meta["effective"] = bool(user_enabled and ready)
        result.append(meta)

    return result


def is_system_tool_enabled(runtime: Any, spec: ToolSpec) -> bool:
    """系统工具是否允许注入：用户开关 + 前置开关。"""
    enabled_map = _cfg_get(runtime, "system_tools_enabled", {}) or {}
    if isinstance(enabled_map, dict) and not enabled_map.get(spec.name, True):
        return False
    ready, _ = _prereq_state(runtime, spec.name)
    return ready
