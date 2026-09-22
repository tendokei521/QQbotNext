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
from app.llm.enhance import install_framework_hooks
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
        install_framework_hooks(self)
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

    def detach_bot(self) -> None:
        """解绑连接（换号路径用）：运行时保留但不再持有旧连接对象。

        否则运行时会把回复/主动消息发送到「已被另一个账号占据」的连接上
        （表现为跨号发送或发到已断开的 socket 上静默丢弃）。
        """
        self._bot = None
        self.ctx.bot = None

    @property
    def bot(self):
        return self._bot

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
        except Exception as e:
            # 归档整理是后台增强，失败不应影响会话主流程，但需留痕以便排查
            logger.add_info(f"#{self.bot_id}").debug(f"[Agent] 归档整理调度失败（已忽略）: {e}")

    def stop(self, close_session: bool = True) -> None:
        """停止定时任务与主动消息计时器（任务数据保留，重启恢复）。

        各组件按 best-effort 逐个关闭：单个组件关闭失败不应阻断其余组件的关闭，
        因此逐个 try/except 并记录 debug 日志（而非静默 pass，便于定位关闭卡滞原因）。

        ``close_session=False`` 用于「回收单个运行时」的软停止（如连接换号触发的
        运行时淘汰）：此时不能关会话管理器——``SessionManager`` 是按 bot_id 的进程级
        单例、被同一账号的所有运行时共享，关掉会把其它仍存活者（以及随后登录重建的
        运行时）的历史库连接一起关死，导致「Cannot operate on a closed database」。
        只有真正退出（AgentManager.shutdown）才连会话一起关。
        """
        components = [
            ("scheduler", self.scheduler.stop),
            ("proactive", self.proactive.stop),
            ("llm_pipeline", self.llm_pipeline.shutdown),
            ("memory", self.memory.stop),
            ("knowledge", self.knowledge.stop),
            ("mcp_manager", self.mcp_manager.close),
        ]
        if close_session:
            components.append(("session_mgr", self.session_mgr.close))
        for name, stop in components:
            try:
                stop()
            except Exception as e:
                logger.add_info(f"#{self.bot_id}").debug(f"[Agent] 关闭 {name} 失败（已忽略）: {e}")
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

    def bind_account(self, bot_id: Any, bot=None) -> AgentRuntime | None:
        """登录/换号入口：把账号的运行时与「当前真正持有该账号的连接」绑好。

        与 ``ensure_runtime`` 的区别是它会做两件自愈动作，因为连接是 index 级对象、
        可以被换号复用，而运行时是按账号 keyed 的长生命周期对象：

        1. **归位**：若该账号的运行时之前绑在别的连接上（账号在 index 之间漂移），
           重绑到当前连接——否则回复/主动消息会继续走旧连接（跨号发送或发到死 socket）。
        2. **回收**：把「仍然绑在当前这条连接上、但账号已不是它」的其它运行时停掉并移除。
           这种"幽灵运行时"会继续跑定时任务/主动消息，并按旧人设通过已被换号的连接发言。
        """
        if bot_id is None:
            return None
        runtime = self.ensure_runtime(bot_id, bot=bot)
        if runtime is None or bot is None:
            return runtime
        if getattr(runtime, "_bot", None) is not bot:
            runtime.set_bot(bot)
        for key, other in list(self._runtimes.items()):
            if other is runtime or str(key) == str(bot_id):
                continue
            if getattr(other, "_bot", None) is bot:
                self._evict_runtime(other, f"连接 #{getattr(bot, 'index', '?')} 已换成账号 {bot_id}")
        return runtime

    def _evict_runtime(self, runtime: AgentRuntime, reason: str) -> None:
        """停掉并移除一个运行时（best-effort：单个失败不影响其余清理）。"""
        bot_id = getattr(runtime, "bot_id", None)
        for key, value in list(self._runtimes.items()):
            if value is runtime:
                self._runtimes.pop(key, None)
        try:
            # 软停止：不关会话管理器（按 bot_id 的进程级单例，仍可能被其他运行时/后续
            # 重建的运行时使用），只停本运行时自己的定时器与后台任务。
            runtime.stop(close_session=False)
        except Exception as e:
            logger.add_info(f"#{bot_id}").warning(f"[Agent] 回收运行时时停止失败（已忽略）: {e}")
        logger.add_info(f"#{bot_id}").info(f"[Agent] 运行时已回收：{reason}")

    def get_runtime(self, bot_id: Any) -> AgentRuntime | None:
        return self._runtimes.get(bot_id)

    def runtimes(self) -> dict[Any, AgentRuntime]:
        return dict(self._runtimes)

    def shutdown(self) -> None:
        for runtime in self._runtimes.values():
            try:
                runtime.stop()
            except Exception as e:
                # 单个运行时停止失败不应阻断其余运行时的关闭（best-effort）
                logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(
                    f"[Agent] 停止运行时失败（已忽略）: {e}"
                )
        self._runtimes.clear()
