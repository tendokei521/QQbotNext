"""Agent 运行时管理（框架级装配）。

AgentRuntime 是单个 Bot 的 LLM Agent 运行时：配置门面 + 会话 + 定时任务 + 主动消息 + 工具。
由 AgentManager 按 bot_id 持有，随 Bot 登录装配、随框架关闭停止——与模块生命周期解耦，
模块热重载不再中断定时任务/主动消息。

AgentRuntime 暴露与旧模块一致的接口（.config / .ctx / .bot_id / .scheduler / .proactive），
因此 app.llm.chat.handle(runtime, event) 可直接复用。
"""

from __future__ import annotations

from typing import Any

from app.llm import logger
from app.llm.config import AgentConfig
from app.llm.hooks import LlmHookRegistry
from app.llm.pipeline import LlmPipeline
from app.llm.proactive import ProactiveManager
from app.llm.scheduler import TaskScheduler
from app.llm.session import SessionManager


class _Services:
    def __init__(self, task_manager) -> None:
        self.task_manager = task_manager


class _Ctx:
    def __init__(self, bot, task_manager) -> None:
        self.bot = bot
        self.services = _Services(task_manager)


class AgentRuntime:
    """单个 Bot 的 Agent 运行时（模块兼容接口）。"""

    name = "LLM Agent"
    module_name = "llm_chat_v2"

    def __init__(self, bot_id: Any, config_service, task_manager, bot=None) -> None:
        self.bot_id = bot_id
        self.config = AgentConfig(config_service, bot_id)
        self.config.migrate_from_legacy()  # 首启从 llm_chat_v2 迁移配置与权限
        self.task_manager = task_manager
        self._bot = bot
        self.ctx = _Ctx(bot, task_manager)

        self.session_mgr = SessionManager(str(bot_id))
        self.scheduler = TaskScheduler(self)
        self.proactive = ProactiveManager(self)

        # LLM 流水线：模块可在任意阶段注册钩子
        self.llm_hooks = LlmHookRegistry()
        self.llm_pipeline = LlmPipeline(self, task_manager=task_manager)

    def set_bot(self, bot) -> None:
        self._bot = bot
        self.ctx.bot = bot

    def stop(self) -> None:
        """停止定时任务与主动消息计时器（任务数据保留，重启恢复）。"""
        try:
            self.scheduler.stop()
        except Exception:
            pass
        try:
            self.proactive.stop()
        except Exception:
            pass
        try:
            self.session_mgr.stop_cleanup()
        except Exception:
            pass
        try:
            self.llm_pipeline.shutdown()
        except Exception:
            pass
        logger.add_info(f"#{self.bot_id}").info("[Agent] 运行时已停止")


class AgentManager:
    """按 bot_id 管理 Agent 运行时（bootstrap 单例）。"""

    def __init__(self, config_service, task_manager) -> None:
        self.config_service = config_service
        self.task_manager = task_manager
        self._runtimes: dict[Any, AgentRuntime] = {}

    def ensure_runtime(self, bot_id: Any, bot=None) -> AgentRuntime | None:
        """获取或创建该 Bot 的运行时。bot_id 为空（全局实例）不创建。"""
        if bot_id is None:
            return None
        runtime = self._runtimes.get(bot_id)
        if runtime is None:
            runtime = AgentRuntime(bot_id, self.config_service, self.task_manager, bot=bot)
            self._runtimes[bot_id] = runtime
            logger.add_info(f"#{bot_id}").info("[Agent] 运行时已装配")
        elif bot is not None:
            runtime.set_bot(bot)
        return runtime

    def get_runtime(self, bot_id: Any) -> AgentRuntime | None:
        return self._runtimes.get(bot_id)

    def runtimes(self) -> dict[Any, AgentRuntime]:
        return dict(self._runtimes)

    def shutdown(self) -> None:
        for runtime in self._runtimes.values():
            try:
                runtime.stop()
            except Exception:
                pass
        self._runtimes.clear()
