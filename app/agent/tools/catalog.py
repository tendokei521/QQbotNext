"""框架工具集只读视图：让模块拿到"框架手里有哪些工具"。

模块能 `@tool` **加**工具，但此前拿不到框架自己的工具
（`expand_message` / `get_chat_history` / OneBot 系列 …），也无法在自己的
agent loop 里跑它们 —— 只能 `from app.agent.tools.spec import make_executor`
这种内部引用。

本模块提供唯一入口，内部**复用** `loop/tool_source._collect_llm_ext`（不复制那 8 段
开关判断），因此与主流水线的工具集永远一致。

```python
specs, tool_ctx = await framework_tools(runtime, session_id="group_123", is_private=False)
tools = build_tools(specs)
executor = make_executor(specs, tool_ctx)
resp = await self.llm_chat(msgs, tools=tools, tool_executor=executor)
```
"""

from __future__ import annotations

from typing import Any


async def framework_tools(
    runtime: Any,
    *,
    session_id: str,
    is_private: bool,
    event: Any = None,
    bot: Any = None,
    user_id: Any = None,
    group_id: Any = None,
    schedule_enable: bool = False,
) -> tuple[list[Any], Any]:
    """返回本次可用的框架工具 ``(specs, ToolContext)``。

    Args:
        runtime: ``AgentRuntime``。
        session_id: 会话 id（工具按它会话作用域工作）。
        is_private: 是否私聊。
        event: 可选触发事件（有则工具能读到触发消息）。
        bot / user_id / group_id: 无事件场景显式给出会话目标。
        schedule_enable: 是否带上"创建定时任务"工具（主动/定时路径应传 False）。

    Returns:
        ``(specs, tool_ctx)``；runtime 不可用时返回 ``([], None)``。
    """
    from app.agent.loop.tool_source import _collect_llm_ext

    if runtime is None:
        return [], None
    specs, _skill_blocks, tool_ctx = await _collect_llm_ext(
        runtime,
        event,
        session_id,
        is_private,
        schedule_enable,
        bot=bot,
        user_id=user_id,
        group_id=group_id,
    )
    return list(specs or []), tool_ctx
