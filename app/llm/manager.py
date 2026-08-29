"""Agent 运行时管理（框架级装配）。

AgentRuntime 是单个 Bot 的 LLM Agent 运行时：配置门面 + 会话 + 定时任务 + 主动消息 + 工具。
由 AgentManager 按 bot_id 持有，随 Bot 登录装配、随框架关闭停止——与模块生命周期解耦，
模块热重载不再中断定时任务/主动消息。

AgentRuntime 暴露与旧模块一致的接口（.config / .ctx / .bot_id / .scheduler / .proactive），
因此 app.llm.chat.handle(runtime, event) 可直接复用。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.llm import logger
from app.llm.config import AgentConfig
from app.llm.hooks import LlmHookRegistry, ToolCallHookRegistry
from app.llm.knowledge import KnowledgeManager
from app.llm.mcp import MCPManager
from app.llm.memory import MemoryManager
from app.llm.pipeline import LlmPipeline
from app.llm.proactive import ProactiveManager
from app.llm.scheduler import TaskScheduler
from app.llm.session import SessionManager
from app.llm.skills import SkillRegistry
from app.llm.telemetry import TelemetryRecorder
from app.llm.tool import ModuleToolRegistry


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

    def __init__(self, bot_id: Any, config_service, task_manager, bot=None, provider_runtime_manager=None) -> None:
        self.bot_id = bot_id
        self.config_service = config_service
        self.config = AgentConfig(config_service, bot_id)
        self.config.migrate_from_legacy()  # 首启从 llm_chat_v2 迁移配置与权限
        self.task_manager = task_manager
        self._bot = bot
        self.provider_runtime_manager = provider_runtime_manager
        self.ctx = _Ctx(bot, task_manager)

        self.session_mgr = SessionManager(str(bot_id))
        self.scheduler = TaskScheduler(self)
        self.proactive = ProactiveManager(self)
        self.memory = MemoryManager(self)
        self.knowledge = KnowledgeManager(self)
        self.mcp_manager = MCPManager(self)
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self.session_mgr.on_archive = self._on_session_archive

        # LLM 流水线：模块可在任意阶段注册钩子
        self.llm_hooks = LlmHookRegistry()
        self.llm_tool_call_hooks = ToolCallHookRegistry(logger)
        self.llm_pipeline = LlmPipeline(self, task_manager=task_manager)
        # 模块扩展：@tool 工具 + 技能
        self.llm_tools = ModuleToolRegistry(logger)
        self.skills = SkillRegistry(logger)
        # LLM 可观测性：调用/工具/钩子耗时
        self.telemetry = TelemetryRecorder()

    def _config_for_model_id(self, model_id: str) -> dict | None:
        """解析指定模型实例的完整 provider 配置。"""
        model = self.config_service.get_provider_model(model_id)
        preset = self.config_service.get_provider_preset(model["preset_id"]) if model else None
        if not model or not preset:
            return None
        config = dict(self.config._base_raw_config())
        config["provider"] = preset.get("provider", "openai")
        config["provider_type"] = model.get("provider_type", "chat")
        config.update(preset.get("config", {}) or {})
        config.update(model.get("config", {}) or {})
        config["model"] = model.get("model", "")
        config["provider_preset_id"] = preset.get("id", "")
        config["provider_model_id"] = model.get("id", "")
        return config

    def provider_config(self) -> dict:
        """返回主 provider 的完整配置（用于兼容旧调用路径）。"""
        chain = self.provider_chain()
        return chain[0] if chain else dict(self.config.raw_config)

    def provider_chain(self) -> list[dict]:
        """按顺序返回可尝试的 provider 配置链。

        优先使用 provider_model_pool（有序模型池）；为空时兼容旧结构：
        provider_model_id 作为首个 + fallback_model_ids 依次追加。
        """
        config = dict(self.config.raw_config)
        chain: list[dict] = []

        pool_ids = [str(i) for i in (config.get("provider_model_pool", []) or []) if str(i)]
        if pool_ids:
            for model_id in pool_ids:
                cfg = self._config_for_model_id(model_id)
                if cfg:
                    chain.append(cfg)
            return chain

        primary_id = str(config.get("provider_model_id", "") or "")
        if primary_id:
            primary = self._config_for_model_id(primary_id)
            if primary:
                chain.append(primary)
        else:
            # 兼容旧结构：provider_preset_id + model
            preset_id = str(config.get("provider_preset_id", "") or "")
            if preset_id:
                preset = self.config_service.get_provider_preset(preset_id)
                if preset:
                    cfg = dict(self.config._base_raw_config())
                    cfg["provider"] = preset.get("provider", "openai")
                    cfg.update(preset.get("config", {}) or {})
                    chain.append(cfg)

        fallback_ids = [str(i) for i in (config.get("fallback_model_ids", []) or []) if str(i)]
        for fb_id in fallback_ids:
            if fb_id == primary_id:
                continue
            fb = self._config_for_model_id(fb_id)
            if fb:
                chain.append(fb)
        return chain

    def set_bot(self, bot) -> None:
        self._bot = bot
        self.ctx.bot = bot

    def _on_session_archive(self, session) -> None:
        """会话过期/结束时触发长期记忆归档蒸馏（异步提交到主事件循环）。"""
        memory = getattr(self, "memory", None)
        if memory is None or self._loop is None or self._loop.is_closed():
            return
        try:
            history = list(getattr(getattr(session, "data", None), "history", None) or [])
            coro = memory.consolidate_archived(
                session.id,
                getattr(session, "type", "private") == "group",
                history,
                source="archive",
            )
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        except Exception:
            pass

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
            self.session_mgr.close()
        except Exception:
            pass
        try:
            self.llm_pipeline.shutdown()
        except Exception:
            pass
        try:
            self.memory.stop()
        except Exception:
            pass
        try:
            self.knowledge.stop()
        except Exception:
            pass
        try:
            self.mcp_manager.close()
        except Exception:
            pass
        logger.add_info(f"#{self.bot_id}").info("[Agent] 运行时已停止")


class AgentManager:
    """按 bot_id 管理 Agent 运行时（bootstrap 单例）。"""

    def __init__(self, config_service, task_manager, provider_runtime_manager=None) -> None:
        self.config_service = config_service
        self.task_manager = task_manager
        self.provider_runtime_manager = provider_runtime_manager
        self._runtimes: dict[Any, AgentRuntime] = {}

    def ensure_runtime(self, bot_id: Any, bot=None) -> AgentRuntime | None:
        """获取或创建该 Bot 的运行时。bot_id 为空（全局实例）不创建。"""
        if bot_id is None:
            return None
        runtime = self._runtimes.get(bot_id)
        if runtime is None:
            runtime = AgentRuntime(
                bot_id,
                self.config_service,
                self.task_manager,
                bot=bot,
                provider_runtime_manager=self.provider_runtime_manager,
            )
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
