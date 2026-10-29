"""llm_tools_extra 的服务层：OneBot 工具与 MCP 作为**业务**接到 `tool_sources`。

框架只提供 `runtime.tool_sources` 这一个基础接口；本模块注册两个贡献者：
- `OneBotToolSource`：把 OneBot API 暴露成模型可调工具（权限/黑名单/冷却在工具内部）
- `MCPToolSource`：stdio MCP server 提供的工具
"""

from __future__ import annotations

from app.core.logger import account_label

from typing import Any


def _tool_ctx(module: Any, scope: Any) -> Any:
    """复用本轮 ToolContext（`extra` 队列共享）；无请求上下文时自建。"""
    existing = getattr(scope, "tool_ctx", None)
    if existing is not None:
        return existing
    from app.agent.tools.spec import ToolContext

    runtime = getattr(scope, "runtime", None)
    return ToolContext(
        module=module,
        bot=getattr(runtime, "bot", None),
        session_id=getattr(scope, "session_id", ""),
        event=getattr(scope, "event", None),
        runtime=runtime,
        user_id=getattr(scope, "user_id", None),
        group_id=getattr(scope, "group_id", None),
    )


class OneBotToolSource:
    """工具源：OneBot 通用工具。"""

    def __init__(self, module: Any) -> None:
        self.module = module
        self.name = "onebot"
        self.order = 60
        self.owner = module

    async def collect(self, scope: Any) -> list:
        runtime = getattr(scope, "runtime", None)
        if runtime is None:
            return []
        from app.agent.policy import policy_get

        if not bool(policy_get(runtime, "tools", "onebot_tools_enable", False)):
            return []
        from .onebot import build_onebot_tools

        return list(build_onebot_tools(runtime, _tool_ctx(self.module, scope)) or [])


class MCPToolSource:
    """工具源：MCP server 工具。"""

    def __init__(self, module: Any) -> None:
        self.module = module
        self.name = "mcp"
        self.order = 65
        self.owner = module

    async def collect(self, scope: Any) -> list:
        runtime = getattr(scope, "runtime", None)
        manager = getattr(self.module, "_mcp", None)
        if runtime is None or manager is None or not manager.enabled():
            return []
        try:
            await manager.ensure_ready()
            return list(manager.build_tools() or [])
        except Exception as e:  # noqa: BLE001 —— MCP 连不上不该毁掉整轮
            from app.core.logger import logger

            logger.add_info(account_label(getattr(runtime, 'bot_id', '?'))).warning(
                f"[llm_tools_extra] MCP 工具不可用（已忽略）: {e}"
            )
            return []
