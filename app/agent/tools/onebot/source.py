"""OneBot 工具源：把 OneBot API 作为**框架内置**工具贡献给模型。

## 合并历史

这段逻辑原本在 ``app/plugins/llm_tools_extra`` 模块的 ``on_load`` 里通过
``register_tool_source`` 注册。2026-11-03 起插件并入框架：由
``AgentRuntime.__init__`` 直接注册本工具源，开关变成 Agent 配置里的一个键
（``onebot_tools_enable``，「高级选项」页）。
"""

from __future__ import annotations

from typing import Any


class OneBotToolSource:
    """工具源：OneBot 通用工具（框架内置，随运行时装配）。"""

    name = "onebot"
    order = 60
    #: 框架自有贡献者不参与模块级 ``unregister_owner``
    owner = None

    async def collect(self, scope: Any) -> list:
        runtime = getattr(scope, "runtime", None)
        if runtime is None:
            return []
        from .security import tools_enabled

        if not tools_enabled(runtime):
            return []
        from .executor import build_onebot_tools

        return list(build_onebot_tools(runtime, _tool_ctx(scope)) or [])


def _tool_ctx(scope: Any) -> Any:
    """复用本轮 ToolContext（``extra`` 队列共享）；无请求上下文时自建。"""
    existing = getattr(scope, "tool_ctx", None)
    if existing is not None:
        return existing
    from app.agent.tools.spec import ToolContext

    runtime = getattr(scope, "runtime", None)
    return ToolContext(
        module=None,
        bot=getattr(runtime, "bot", None),
        session_id=getattr(scope, "session_id", ""),
        event=getattr(scope, "event", None),
        runtime=runtime,
        user_id=getattr(scope, "user_id", None),
        group_id=getattr(scope, "group_id", None),
    )
