"""工具注入策略：这个 Bot 此刻允许把哪些工具交给模型。

## 本模块只负责

判定「某个工具该不该出现在这一轮的 tools 列表里」——用户开关 + 功能前置开关
（如 `memory_enable` / `knowledge_enable` / `schedule_enable`）。纯读配置，无 IO。

## 为什么单独拆出来

这段判定被两处用：`system.py`（给 WebUI 列清单并标注 enabled/ready）与
`loop/chat.py`（真正决定注入哪些工具）。它原本和「工具目录聚合」写在
`tools/system.py` 里，导致 `chat → tools/system → proactivity/scheduler →
loop/chat` 形成环（4 模块 / 3,481 行）。

把**策略**下沉到本模块后：`chat → policy`（无环），`system → policy` 也成立；
`system.py` 保留「收集与展示」，不再被主循环依赖。

## 前置开关默认值必须与功能真实默认一致

`context_expand_enable` 这类「默认开启」的功能，若在配置里读不到该键却被当作
False，会出现「spec 已加入但被系统过滤掉」的静默消失——所以下表的第三项
（缺省默认值）不能省。
"""

from __future__ import annotations

from typing import Any

from app.agent.tools.spec import ToolSpec

# 前置开关：(config 键, 未启用时的说明, 键缺失时的默认值)
_SYSTEM_TOOL_PREREQUISITES: dict[str, tuple] = {
    "schedule_task": ("schedule_enable", "定时任务未启用"),
    "get_current_session": ("", "始终可用"),
    "get_chat_history": ("", "始终可用"),
    "expand_recent": ("context_expand_enable", "上下文按需展开未启用", True),
    "expand_message": ("context_expand_enable", "上下文按需展开未启用", True),
    "expand_user": ("context_expand_enable", "上下文按需展开未启用", True),
    "tavily_search": ("tavily_enable", "Tavily 联网搜索未启用"),
    # P5：spill 取回工具（关掉落盘就没有可读的东西了）
    "recall_tool_result": ("tool_spill_enable", "超大结果落盘未启用"),
    "memory_save": ("memory_enable", "长期记忆未启用"),
    "memory_recall": ("memory_enable", "长期记忆未启用"),
    "memory_delete": ("memory_enable", "长期记忆未启用"),
    "memory_correct": ("memory_enable", "长期记忆未启用"),
    "memory_deny": ("memory_enable", "长期记忆未启用"),
    "knowledge_add": ("knowledge_enable", "知识库未启用"),
    "knowledge_search": ("knowledge_enable", "知识库未启用"),
    "knowledge_delete": ("knowledge_enable", "知识库未启用"),
}


def cfg_get(runtime: Any, key: str, default: Any = None) -> Any:
    """安全读取 runtime.config 的一个键（runtime 或 config 缺失时回落到默认值）。"""
    try:
        config = getattr(runtime, "config", None)
        return config.get(key, default) if config is not None else default
    except Exception:
        return default


def prereq_state(runtime: Any, name: str) -> tuple[bool, str]:
    """该工具的前置功能是否就绪，以及不可用时的说明文案。

    能力声明优先（P6）：`context_expand_*` 这类"由某个模块提供"的能力，
    先问 ``runtime.capabilities`` 有没有人声明；没人声明再回退到配置键，
    保证没装模块/旧配置时行为与改造前一致。
    """
    entry = _SYSTEM_TOOL_PREREQUISITES.get(name, ("", ""))
    key, label = entry[0], entry[1]
    default = entry[2] if len(entry) > 2 else False
    if not key:
        return True, ""
    if name.startswith("memory_"):
        # 记忆归 `llm_memory` 模块：它把管理器挂在 `runtime.memory_service`（框架已无 memory 字段）
        memory = getattr(runtime, "memory_service", None)
        if memory is not None and hasattr(memory, "enabled"):
            return bool(memory.enabled()), label
    if key == "context_expand_enable":
        from app.agent.capabilities import CAP_CONTEXT_EXPAND, has_tool_capability

        if has_tool_capability(runtime, CAP_CONTEXT_EXPAND, config_key=key):
            return True, ""
        return False, label
    return bool(cfg_get(runtime, key, default)), label


def is_system_tool_enabled(runtime: Any, spec: ToolSpec) -> bool:
    """系统工具是否允许注入：用户开关 + 前置开关。"""
    enabled_map = cfg_get(runtime, "system_tools_enabled", {}) or {}
    if isinstance(enabled_map, dict) and not enabled_map.get(spec.name, True):
        return False
    ready, _ = prereq_state(runtime, spec.name)
    return ready
