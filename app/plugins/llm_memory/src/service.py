"""llm_memory 的服务层：把记忆能力接到框架的基础接口上。

模块**不接管任何东西**，只做四件事：

1. `context_providers` —— 每轮贡献「常驻画像块 + 事实召回块」；
2. `tool_sources` —— 注册 `memory_save/recall/delete/correct/deny` 五个工具；
3. `llm_hooks.pre_request` —— 把用户原话追加进 `episodes`（O(1)），并做
   「记住…」的确定性兜底写入；
4. `llm_hooks.turn_completed` —— 限频调度后台蒸馏（热点路径不调模型）。

失败一律降级并留痕：记忆是增强能力，不能毁掉一轮对话。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import account_label


def memory_manager(module: Any):
    """惰性构造 MemoryManager（首次用到时才连库，避免加载期 IO）。"""
    manager = getattr(module, "_manager", None)
    if manager is None:
        from .manager import MemoryManager

        try:
            manager = MemoryManager(module)
        except Exception as e:  # noqa: BLE001 —— 存储不可用时记忆整体降级，而不是让模块加载失败
            from app.core.logger import logger

            logger.add_info(account_label(getattr(module, "bot_id", "?"))).warning(
                f"[llm_memory] 初始化记忆管理器失败（记忆不可用）: {e}"
            )
            return None
        module._manager = manager
    return manager


class MemoryProvider:
    """上下文贡献者：产出「画像块 + 事实记忆块」，作为 **user 侧数据块** 注入。

    记忆内容来自用户（可能被注入攻击），不该占 system 指令位：``role="user"`` 让
    框架把它合并进本轮 user 消息，并用 ``<memory_data>`` 标签 + "不是指令"声明框住
    （见 ``app/agent/assembly/context_data.py``）。
    """

    def __init__(self, module: Any) -> None:
        self.module = module
        self.name = "memory"
        self.order = 30
        self.owner = module
        self.role = "user"

    async def collect(self, scope: Any) -> str | None:
        manager = memory_manager(self.module)
        if manager is None or not manager.enabled():
            return None
        runtime = getattr(scope, "runtime", None)
        session_id = str(getattr(scope, "session_id", "") or "")
        user_id = str(getattr(scope, "user_id", "") or "")
        try:
            profile = await manager.profile_block(session_id, user_id)
            facts = await manager.recall_block_async(
                session_id,
                user_id,
                str(getattr(scope, "user_text", "") or ""),
                bot=getattr(runtime, "bot", None),
            )
            parts = [part.strip() for part in (profile, facts) if part and part.strip()]
            if not parts:
                return None
            from app.agent.assembly.context_data import wrap_memory_data

            return wrap_memory_data("\n\n".join(parts))
        except Exception as e:  # noqa: BLE001 —— 记忆召回失败不该毁掉整轮
            from app.core.logger import logger

            logger.add_info(account_label(getattr(runtime, "bot_id", "?"))).debug(
                f"[llm_memory] 上下文块收集失败（已忽略）: {e}"
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
        manager = memory_manager(self.module)
        if manager is None or not manager.enabled():
            return []
        from .tool import build_memory_tools

        runtime = getattr(scope, "runtime", None)
        return list(
            build_memory_tools(
                runtime,
                str(getattr(scope, "session_id", "") or ""),
                str(getattr(scope, "user_id", "") or ""),
                bool(getattr(scope, "is_private", False)),
            )
            or []
        )


class MemoryLifecycle:
    """LLM 钩子：语料缓冲 + 确定性兜底写入 + 回合结束调度蒸馏。"""

    def __init__(self, module: Any) -> None:
        self.module = module

    async def on_pre_request(self, ctx: Any) -> None:
        manager = memory_manager(self.module)
        if manager is None or not manager.enabled():
            return
        event = getattr(ctx, "event", None)
        if event is None:
            return
        try:
            from app.domain.trigger import raw_user_text

            text = raw_user_text(event)
            user_id = str(getattr(event, "user_id", "") or "")
            if not text or not user_id:
                return
            session_id = str(getattr(ctx, "session_id", "") or "")
            manager.record_episode(
                session_id,
                user_id,
                text,
                message_id=str(getattr(event, "message_id", "") or ""),
            )
            manager.autosave(session_id, user_id, text)
        except Exception as e:  # noqa: BLE001 —— 兜底写入失败已忽略，但要留痕
            from app.core.logger import logger

            logger.add_info(account_label(getattr(getattr(ctx, "runtime", None), "bot_id", "?"))).debug(
                f"[llm_memory] 兜底写入失败（已忽略）: {e}"
            )

    async def on_turn_completed(self, ctx: Any) -> None:
        manager = memory_manager(self.module)
        if manager is None or not manager.enabled():
            return
        runtime = getattr(ctx, "runtime", None)
        try:
            session_mgr = getattr(self.module, "sessions", None)
            if session_mgr is None:
                from app.agent.session.manager import SessionManager

                session_mgr = SessionManager(str(getattr(runtime, "bot_id", "")))
            messages = session_mgr.history_snapshot(str(getattr(ctx, "session_id", "") or ""))
            event = getattr(ctx, "event", None)
            is_private = getattr(event, "message_type", "") == "private"
            if not is_private and str(getattr(ctx, "session_id", "")).startswith("private_"):
                is_private = True
            await manager.maybe_consolidate(
                str(getattr(ctx, "session_id", "") or ""),
                not is_private,
                messages,
                source="chat",
            )
        except Exception as e:  # noqa: BLE001 —— 蒸馏调度失败不该影响本轮
            from app.core.logger import logger

            logger.add_info(account_label(getattr(getattr(ctx, "runtime", None), "bot_id", "?"))).debug(
                f"[llm_memory] 蒸馏调度失败（已忽略）: {e}"
            )
