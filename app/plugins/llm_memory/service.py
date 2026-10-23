"""llm_memory 的服务层：把记忆能力接到框架的三个基础接口上。

模块**不接管任何东西**，只做三件事：

1. `context_providers` —— 每轮贡献「记忆召回块」；
2. `tool_sources` —— 注册 `memory_save` / `recall` / `delete` / `correct` / `deny` 工具；
3. `@llm_hook` —— `pre_request` 里做确定性兜底写入（用户说"记住…"），
   `turn_completed` 里做蒸馏（原框架 `_memory_consolidate` 的活）。
"""

from __future__ import annotations

from typing import Any


def memory_manager(module: Any):
    """惰性构造 MemoryManager（首次用到时才连库，避免加载期 IO）。"""
    mgr = getattr(module, "_manager", None)
    if mgr is None:
        from app.plugins.llm_memory.manager import MemoryManager

        runtime = module._runtime()
        mgr = MemoryManager(runtime) if runtime is not None else None
        module._manager = mgr
    return mgr


class MemoryProvider:
    """上下文贡献者：产出「长期记忆」块。"""

    def __init__(self, module: Any) -> None:
        self.module = module
        self.name = "memory"
        self.order = 30
        self.owner = module

    async def collect(self, scope: Any) -> str | None:
        mgr = memory_manager(self.module)
        if mgr is None or not mgr.enabled():
            return None
        runtime = getattr(scope, "runtime", None)
        try:
            return await mgr.recall_block_async(
                scope.session_id,
                scope.user_id if getattr(scope, "is_private", False) else "",
                scope.user_text or "",
                bot=getattr(runtime, "bot", None),
            )
        except Exception as e:  # noqa: BLE001 —— 记忆召回失败不该毁掉整轮
            from app.core.logger import logger

            logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(
                f"[llm_memory] 召回失败（已忽略）: {e}"
            )
            return None


class MemoryToolSource:
    """工具源：记忆类原生工具。"""

    def __init__(self, module: Any) -> None:
        self.module = module
        self.name = "memory_tools"
        self.order = 30
        self.owner = module

    async def collect(self, scope: Any) -> list:
        mgr = memory_manager(self.module)
        if mgr is None or not mgr.enabled():
            return []
        from app.agent.tools.spec import ToolContext
        from app.plugins.llm_memory.tool import build_memory_tools

        runtime = getattr(scope, "runtime", None)
        tool_ctx = getattr(scope, "tool_ctx", None) or ToolContext(
            module=self.module,
            bot=getattr(runtime, "bot", None),
            session_id=getattr(scope, "session_id", ""),
            event=getattr(scope, "event", None),
            runtime=runtime,
            user_id=getattr(scope, "user_id", None),
            group_id=getattr(scope, "group_id", None),
        )
        return list(
            build_memory_tools(
                runtime,
                scope.session_id,
                str(getattr(scope, "user_id", "") or ""),
                bool(getattr(scope, "is_private", False)),
            )
            or []
        ) + [None][:0]


class MemoryLifecycle:
    """LLM 钩子：确定性兜底写入 + 回合结束蒸馏。"""

    def __init__(self, module: Any) -> None:
        self.module = module

    async def on_pre_request(self, ctx: Any) -> None:
        mgr = memory_manager(self.module)
        if mgr is None or not mgr.enabled():
            return
        event = getattr(ctx, "event", None)
        if event is None:
            return
        try:
            from app.domain.trigger import raw_user_text

            text = raw_user_text(event)
            user_id = str(getattr(event, "user_id", "") or "")
            if text and user_id:
                mgr.autosave(ctx.session_id, user_id, text)
        except Exception as e:  # noqa: BLE001
            from app.core.logger import logger

            logger.add_info(f"#{getattr(getattr(ctx, 'runtime', None), 'bot_id', '?')}").debug(
                f"[llm_memory] 兜底写入失败（已忽略）: {e}"
            )

    async def on_turn_completed(self, ctx: Any) -> None:
        mgr = memory_manager(self.module)
        if mgr is None or not mgr.enabled():
            return
        runtime = getattr(ctx, "runtime", None)
        try:
            from app.agent.session.manager import SessionManager

            session_mgr = SessionManager(str(getattr(runtime, "bot_id", "")))
            event = getattr(ctx, "event", None)
            is_private = getattr(event, "message_type", "") == "private"
            # 注意：`maybe_consolidate` 要的是**消息列表**，不是会话表。
            # 传 `session_mgr.sessions`（dict）会让它遍历到字符串键 →
            # `'str' object has no attribute 'get'`（历史 bug）。
            messages = session_mgr.history_snapshot(ctx.session_id)
            if messages:
                mgr.maybe_consolidate(ctx.session_id, not is_private, messages, source="chat")
        except Exception as e:  # noqa: BLE001
            from app.core.logger import logger

            logger.add_info(f"#{getattr(getattr(ctx, 'runtime', None), 'bot_id', '?')}").debug(
                f"[llm_memory] 蒸馏触发失败（已忽略）: {e}"
            )
