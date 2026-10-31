"""模块工具源（tool_sources）必须出现在统一工具清单里，并可单独开关。

回归背景：`expand_*` / `memory_*` / `knowledge_*` / `schedule_task` 由插件在
`on_load` 里通过 `register_tool_source` 注入；此前「高级工具」只枚举
`llm_tools`（@tool）/ 系统工具 / OneBot / MCP，这些工具在界面上不可见、
也无法单独关闭（只能关整个模块）。
"""

from __future__ import annotations

import types

from app.agent.tools.spec import ToolSpec
from app.agent.tools.state import (
    REASON_USER_DISABLED,
    resolve_tool_source_states,
)


class _Config:
    def __init__(self, data: dict) -> None:
        self._data = dict(data)

    def get(self, key, default=None):
        return self._data.get(key, default)


class _Memory:
    def enabled(self) -> bool:
        return True


class _Source:
    def __init__(self, name: str, specs: list[ToolSpec], owner) -> None:
        self.name = name
        self.order = 10
        self.owner = owner
        self._specs = specs

    async def collect(self, scope):
        return list(self._specs)


def _spec(name: str, description: str = "", category: str = "") -> ToolSpec:
    async def _handler(ctx, args):
        return "ok"

    return ToolSpec(
        name=name,
        description=description or f"{name} 的说明",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        source="system",
        category=category,
    )


def _runtime(config: dict, sources: list) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        config=_Config(config),
        tool_sources=sources,
        memory_service=_Memory(),
        bot=None,
    )


async def test_tool_source_tools_are_listed_with_owner_and_description():
    owner = types.SimpleNamespace(module_name="llm_memory", name="长期记忆")
    source = _Source("memory_tools", [_spec("memory_save", "保存长期记忆", "长期记忆")], owner)
    runtime = _runtime({"tools_disabled": [], "memory_enable": True}, [source])

    states = await resolve_tool_source_states(runtime)

    assert [s.name for s in states] == ["memory_save"]
    state = states[0]
    assert state.owner == "llm_memory"
    assert state.extra["module_label"] == "长期记忆"
    data = state.to_dict()
    assert data["description"] == "保存长期记忆"
    assert data["category"] == "长期记忆"


async def test_tool_source_tools_respect_the_user_disabled_list():
    owner = types.SimpleNamespace(module_name="llm_context", name="上下文增强")
    source = _Source("context_expand", [_spec("expand_message")], owner)
    runtime = _runtime(
        {"tools_disabled": ["expand_message"], "context_expand_enable": True},
        [source],
    )

    states = await resolve_tool_source_states(runtime)

    assert len(states) == 1
    assert states[0].available is False
    assert states[0].reason == REASON_USER_DISABLED
