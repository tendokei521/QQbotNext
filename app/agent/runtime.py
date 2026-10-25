"""Agent 运行时管理（框架级装配）。

AgentRuntime 是单个 Bot 的 LLM Agent 运行时：配置门面 + 会话 + 定时任务 + 主动消息 + 工具。
由 AgentManager 按 bot_id 持有，随 Bot 登录装配、随框架关闭停止——与模块生命周期解耦，
模块热重载不再中断定时任务/主动消息。

AgentRuntime 暴露与旧模块一致的接口（.config / .ctx / .bot_id / .scheduler / .proactive），
因此 app.agent.loop.chat.handle(runtime, event) 可直接复用。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.agent import logger
from app.agent.assembly.assembler import PromptAssembler
from app.agent.assembly.interrupt import interrupt_config_hook
from app.agent.config import DEFAULT_LLM_CONFIG, AgentConfig
from app.agent.hooks import LlmHookRegistry, ToolCallHookRegistry
from app.agent.loop.pipeline import LlmPipeline
from app.agent.persona.skills import SkillRegistry
from app.agent.providers.invoker import ProviderInvoker
from app.agent.providers.policy import TRACKED_KEYS, merge_config_layers
from app.agent.session.manager import SessionManager
from app.agent.telemetry import TelemetryRecorder
from app.agent.tools.spec import ModuleToolRegistry
from app.core.logger import account_label


def _origin_of(*layers: tuple[str, dict]) -> str:
    """返回最先提供 ``max_tokens`` 的那一层名字（高优先级在前）。

    保留这个薄封装只为兼容既有调用与用例：真正的合并+溯源已经统一到
    ``request_policy.merge_config_layers``（它会为 ``TRACKED_KEYS`` 逐键记来源）。
    """
    for name, cfg in layers:
        if isinstance(cfg, dict) and cfg.get("max_tokens") not in (None, ""):
            return name
    return "agent 配置"


def _apply_max_tokens_plan(effective) -> dict:
    """把 ``plan_max_tokens`` 的结论落到链上配置，并把来源一并交给下游。

    这一层是"请求真正被发出去"前的最后一站，所以思考下限与窗口封顶在这里生效：
    模型页与写侧校验是第一道（用户在页面上看到的就是生效值），这里是第二道——
    老配置（页面上没重存过）也可能低于思考下限，而"预算全花在 reasoning 里"的
    空回复不该留到运行时才发现。

    落盘形状：``max_tokens`` = 生效值，``_max_tokens_origin`` = **纯来源名**（如
    ``provider 模型+思考下限``），``_max_tokens_notes`` = 抬升/封顶的补充说明。

    ``_max_tokens_origin`` 刻意不存 ``plan.describe()`` 的成品句子：下游日志全都要
    自己拼 ``max_tokens={值}（来自{来源}）``，存成品会被再套一层——线上因此出现过
    ``max_tokens=1024（来自max_tokens=1024（来自provider 模型））``。
    """
    from app.agent.providers.policy import plan_max_tokens

    cfg = dict(effective.config)
    try:
        plan = plan_max_tokens(cfg)
    except Exception as e:  # noqa: BLE001 —— 计划失败就用合并值，不能因此不发请求
        logger.add_info("LLM").warning(f"[max_tokens] 规划失败，沿用合并值: {e}")
        return cfg
    if plan.value and plan.value != cfg.get("max_tokens"):
        cfg["max_tokens"] = plan.value
    cfg["_max_tokens_origin"] = plan.origin
    if plan.notes:
        cfg["_max_tokens_notes"] = "；".join(plan.notes)
    return cfg


class _Services:
    def __init__(self, task_manager) -> None:
        self.task_manager = task_manager

class _Ctx:
    def __init__(self, bot, task_manager) -> None:
        self.bot = bot
        self.services = _Services(task_manager)


class AgentRuntime:
    """单个 Bot 的 Agent 运行时（模块兼容接口）。

    ## 在线状态（``online``）与「装配 ≠ 可跑」

    运行时的**存在**只表示「这个账号的配置有归属」（账号在创建时就有 id，不必等连接），
    所以启动/建号时就会装配它——但**装配不等于可以跑任务**：

    - ``online=False``（默认）：只能读写配置、列出会话；定时任务、主动消息、消息收发
      一律不启动，模块里的计时器也不会武装；
    - ``online=True``：由 ``BotService.on_bot_login`` 在真正登录成功后置位，并逐个
      调用该账号模块的 ``on_online()``（见 `BaseModule`），此时才允许跑任务；
    - 断线（``disconnected`` / ``error``）立即翻回 False，并调用模块 ``on_offline()``，
      把计时器停掉——否则断线后定时任务还会对着死连接硬发（历史遗留缺陷）。
    """

    name = "LLM Agent"
    module_name = "llm_chat_v2"

    def __init__(self, bot_id: Any, config_service, task_manager, bot=None, provider_runtime_manager=None) -> None:
        self.bot_id = bot_id
        #: 是否已连上 QQ（只有连上才允许跑定时任务/主动消息/收发）
        self.online = False
        self.config_service = config_service
        self.config = AgentConfig(config_service, bot_id)
        self.config.migrate_from_legacy()  # 首启从 llm_chat_v2 迁移配置与权限
        self.task_manager = task_manager
        self._bot = bot
        self.provider_runtime_manager = provider_runtime_manager
        self.ctx = _Ctx(bot, task_manager)

        self.session_mgr = SessionManager(str(bot_id))
        # 主动消息与定时任务是**业务**：由 `llm_proactive` / `llm_schedule` 模块在
        # `on_load` 时把各自的实现装进来（框架只提供 `send_to`/`llm_turn`/`spawn` 等原料）。
        # 未装模块时这里保持 None，流水线侧的调用点全部做了 getattr 保护。
        self.scheduler: Any = None
        self.proactive: Any = None
        # 记忆 / 知识库 / MCP / OneBot 工具都是**业务**：实现已搬进对应模块，
        # 框架不再持有它们的实例（模块通过 tool_sources 接入）。
        # 策略配置视图（模块接管时挂载；None = 回退 agent 配置）
        self.assembly_options: Any = None
        self.meta_options: Any = None
        self.context_options: Any = None
        self.memory_options: Any = None
        self.knowledge_options: Any = None
        self.tools_options: Any = None
        self.proactive_options: Any = None
        self.schedule_options: Any = None
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self.session_mgr.on_archive = self._on_session_archive

        # LLM 流水线：模块可在任意阶段注册钩子
        self.llm_hooks = LlmHookRegistry()
        # 框架只装**本体行为**的钩子（回复打断开关来自「对话行为」页）；
        # 消息元信息渲染等业务由 `llm_message_meta` 模块自己注册。
        self.llm_hooks.register(
            stage="pre_request", event_type="*", order=25,
            handler=interrupt_config_hook, module=None,
        )
        self.llm_tool_call_hooks = ToolCallHookRegistry(logger)
        self.llm_pipeline = LlmPipeline(self, task_manager=task_manager)
        # 消息组装器：插件可声明 supersedes=("assembly",) 整体替换（FeatureRegistry 自动还原）
        self.assembler = PromptAssembler()
        # provider 调用策略：插件可声明 supersedes=("provider_invoker",) 整体替换
        self.invoker = ProviderInvoker()
        # 能力声明表（P6）：模块在 on_load 里声明"我提供什么能力"，框架只问不猜
        from app.agent.capabilities import CapabilityRegistry

        self.capabilities = CapabilityRegistry()
        # 高危工具审批通道（P6）：插件/业务提供 ``async ask(runtime, *, tool, args, ctx)``。
        # None = 没有通道 → 需要审批的调用一律拒绝（fail-closed，见 tools/approval.py）。
        self.approval_channel = None
        # 输出策略：None = 按配置自动选择（stream_output）。
        # 插件可声明 supersedes=("output",) 整体替换；置 None 即回到框架默认。
        self.output = None
        # 上下文贡献者与工具源（注册制、可组合；模块 on_load 里 register）
        self.context_providers: list = []
        self.tool_sources: list = []
        # 模块扩展：@tool 工具 + 技能
        self.llm_tools = ModuleToolRegistry(logger)
        self.skills = SkillRegistry(logger)
        # LLM 可观测性：调用/工具/钩子耗时
        self.telemetry = TelemetryRecorder()

    def _config_for_model_id(self, model_id: str) -> dict | None:
        """解析指定模型实例的完整 provider 配置。

        合并走 ``request_policy.merge_config_layers``（**唯一实现**）——此前
        ``ProviderRuntimeManager.resolve_provider_config`` 里还躺着另一份等价逻辑，
        两份会漂移：改了一处、另一处仍按旧口径合并。
        """
        model = self.config_service.get_provider_model(model_id)
        preset = self.config_service.get_provider_preset(model["preset_id"]) if model else None
        if not model or not preset:
            return None
        effective = merge_config_layers(
            base=self.config._base_raw_config(),
            # 只要"用户显式动过"的键：base 铺满了默认值，拿它做溯源等于没溯源
            agent_layer=self.config.explicit_values(TRACKED_KEYS),
            preset_layer=preset.get("config", {}) or {},
            model_layer=model.get("config", {}) or {},
            defaults=DEFAULT_LLM_CONFIG,
            meta={
                "provider": preset.get("provider", "openai"),
                "provider_type": model.get("provider_type", "chat"),
                "model": model.get("model", ""),
                "provider_preset_id": preset.get("id", ""),
                "provider_model_id": model.get("id", ""),
            },
        )
        return _apply_max_tokens_plan(effective)

    def provider_config(self) -> dict:
        """返回主 provider 的完整配置（用于兼容旧调用路径）。"""
        chain = self.provider_chain()
        return chain[0] if chain else dict(self.config.raw_config)

    def _global_provider_settings(self) -> dict:
        """「Provider 全局设置」（默认模型 / fallback / pool）。读不到一律当空。"""
        getter = getattr(self.config_service, "get_provider_settings", None)
        if not callable(getter):
            return {}
        try:
            return getter() or {}
        except Exception as e:  # noqa: BLE001 —— 设置读失败不该拖垮整条链
            logger.add_info(account_label(self.bot_id)).warning(
                f"[Provider] 读取全局设置失败（已忽略）: {e}"
            )
            return {}

    def _chain_from_global_settings(self) -> list[dict]:
        """Agent 页没有指定模型时，用「Provider 全局设置」兜底。

        **此前这三个字段有完整 UI、独立 API、独立数据表，运行时却一次都没被读过** ——
        用户在唯一写着「默认模型」的地方做的选择永远不生效；新 bot 更糟：配了全局
        默认模型仍然一句话不回，且没有任何日志指向"没有可用模型"。

        语义（保守）：只在本 bot 的 Agent 配置**没给出任何模型**时接管，避免悄悄
        改变已有 bot 的模型选择；``provider_pool`` 是允许清单，``*`` 或不填表示全允许。
        """
        settings = self._global_provider_settings()
        ids: list[str] = []
        primary = str(settings.get("default_model_id", "") or "").strip()
        if primary:
            ids.append(primary)
        for raw in settings.get("fallback_model_ids", []) or []:
            mid = str(raw or "").strip()
            if mid and mid not in ids:
                ids.append(mid)
        if not ids:
            return []

        pool = [
            str(p or "").strip()
            for p in (settings.get("provider_pool", []) or [])
            if str(p or "").strip()
        ]
        allow = None if (not pool or "*" in pool) else set(pool)

        chain: list[dict] = []
        for mid in ids:
            if allow is not None and mid not in allow:
                logger.add_info(account_label(self.bot_id)).warning(
                    f"[Provider] 全局设置里的模型 {mid} 不在 provider_pool 允许清单内，已跳过"
                )
                continue
            cfg = self._config_for_model_id(mid)
            if cfg:
                chain.append(cfg)
            else:
                logger.add_info(account_label(self.bot_id)).warning(
                    f"[Provider] 全局设置引用的模型不存在（可能已被删除）: {mid}"
                )
        if chain:
            names = [str(c.get("provider_model_id") or c.get("model") or "") for c in chain]
            logger.add_info(account_label(self.bot_id)).info(
                f"[Provider] Agent 页未指定模型，改用「Provider 全局设置」的模型链: {names}"
            )
        return chain

    def provider_chain(self) -> list[dict]:
        """按顺序返回可尝试的 provider 配置链。

        优先使用 provider_model_pool（有序模型池）；为空时兼容旧结构：
        provider_model_id 作为首个 + fallback_model_ids 依次追加；**这两层都没有时**
        回落到「Provider 全局设置」的默认模型 / fallback（见 _chain_from_global_settings）。
        """
        config = dict(self.config.raw_config)
        chain: list[dict] = []

        pool_ids = [str(i) for i in (config.get("provider_model_pool", []) or []) if str(i)]
        if pool_ids:
            for model_id in pool_ids:
                cfg = self._config_for_model_id(model_id)
                if cfg:
                    chain.append(cfg)
            if chain:
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
                    # 走同一个合并实现：这条旧路径此前手写 update，溯源与校验都会漏掉
                    chain.append(
                        _apply_max_tokens_plan(
                            merge_config_layers(
                                base=self.config._base_raw_config(),
                                agent_layer=self.config.explicit_values(TRACKED_KEYS),
                                preset_layer=preset.get("config", {}) or {},
                                defaults=DEFAULT_LLM_CONFIG,
                                meta={
                                    "provider": preset.get("provider", "openai"),
                                    "provider_preset_id": preset.get("id", ""),
                                    "model": str(config.get("model", "") or ""),
                                },
                            )
                        )
                    )

        fallback_ids = [str(i) for i in (config.get("fallback_model_ids", []) or []) if str(i)]
        for fb_id in fallback_ids:
            if fb_id == primary_id:
                continue
            fb = self._config_for_model_id(fb_id)
            if fb:
                chain.append(fb)

        if not chain:
            chain = self._chain_from_global_settings()
        if not chain:
            # 这条日志此前不存在：表现为 bot 一句话不回、日志里毫无线索
            logger.add_info(account_label(self.bot_id)).error(
                "[Provider] 没有可用模型：Agent 页未选择模型，且「Provider 全局设置」里也没有"
                "默认模型——请在 Agent 页选择模型，或在 Provider 预设页设置全局默认模型"
            )
        return chain

    def set_bot(self, bot) -> None:
        self._bot = bot
        self.ctx.bot = bot

    def set_online(self, online: bool) -> bool:
        """翻转「已连接」标志，返回是否真的发生变化（幂等，重复调用无副作用）。"""
        online = bool(online)
        changed = online != self.online
        self.online = online
        return changed

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
            logger.add_info(account_label(self.bot_id)).debug(f"[Agent] 归档整理调度失败（已忽略）: {e}")

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
            # 主动消息/定时任务由模块提供，未装模块时为 None
            ("scheduler", getattr(self.scheduler, "stop", None)),
            ("proactive", getattr(self.proactive, "stop", None)),
            ("llm_pipeline", self.llm_pipeline.shutdown),
        ]
        if close_session:
            components.append(("session_mgr", self.session_mgr.close))
        for name, stop in components:
            if not callable(stop):
                continue
            try:
                stop()
            except Exception as e:
                logger.add_info(account_label(self.bot_id)).debug(f"[Agent] 关闭 {name} 失败（已忽略）: {e}")
        logger.add_info(account_label(self.bot_id)).info("[Agent] 运行时已停止")


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
            logger.add_info(account_label(bot_id)).info("[Agent] 运行时已装配")
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
            logger.add_info(account_label(bot_id)).warning(f"[Agent] 回收运行时时停止失败（已忽略）: {e}")
        logger.add_info(account_label(bot_id)).info(f"[Agent] 运行时已回收：{reason}")

    def get_runtime(self, bot_id: Any) -> AgentRuntime | None:
        return self._runtimes.get(bot_id)

    def drop_runtime(self, bot_id: Any, reason: str = "") -> None:
        """停掉并移除某个账号的运行时（账号被删除 / 换号回收）。

        没有这一步时，删掉的账号会留下"幽灵运行时"：会话管理器、telemetry、工具注册表
        都还挂在内存里，且它的计时器仍可能继续跑。
        """
        if bot_id is None:
            return
        runtime = self._runtimes.pop(bot_id, None)
        if runtime is None:
            return
        runtime.set_online(False)
        self._evict_runtime(runtime, reason or f"账号 {bot_id} 已移除")

    def sync_accounts(self, account_ids) -> None:
        """按「当前配置里真实存在的账号」对齐运行时集合。

        存在的补齐（**不要求连接**：账号一创建就有 id，配置就该能编辑），
        已删除的回收。返回值为空——调用方通常紧接着按账号装配模块。
        """
        wanted = {str(a) for a in (account_ids or []) if a not in (None, "")}
        for account_id in wanted:
            self.ensure_runtime(account_id)
        for key in list(self._runtimes.keys()):
            if str(key) not in wanted:
                self.drop_runtime(key, f"账号 {key} 已不在配置中")

    def runtimes(self) -> dict[Any, AgentRuntime]:
        return dict(self._runtimes)

    def shutdown(self) -> None:
        for runtime in self._runtimes.values():
            try:
                runtime.stop()
            except Exception as e:
                # 单个运行时停止失败不应阻断其余运行时的关闭（best-effort）
                logger.add_info(account_label(getattr(runtime, 'bot_id', '?'))).debug(
                    f"[Agent] 停止运行时失败（已忽略）: {e}"
                )
        self._runtimes.clear()
