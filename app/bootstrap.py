"""应用装配根：构建容器、初始化、启动网关/调度器/WebUI。

依赖规则：此处是唯一「知道所有组件」的装配点；
组件之间只依赖各自的抽象，循环依赖在此暴露。
"""

from __future__ import annotations

import asyncio

from app.agent.client import ModuleLLMClient
from app.agent.providers.runtime_manager import ProviderRuntimeManager
from app.agent.runtime import AgentManager
from app.application.bot_service import BotService
from app.application.cron_service import SchedulerService
from app.application.log_service import LogService
from app.application.module_install_service import ModuleInstallService
from app.application.provider_model_service import ProviderModelService
from app.application.provider_preset_service import ProviderPresetService
from app.application.provider_service import ProviderRegistry
from app.application.resource_registry import ResourceRegistry
from app.application.role_preset_service import RolePresetService
from app.core.container import Container, get_active, set_active
from app.core.logger import logger, set_console_mode, setup_logging
from app.core.settings import Settings, load_settings
from app.core.task_manager import TaskManager
from app.infrastructure.cache import Cache
from app.infrastructure.config.config_service import ConfigService
from app.infrastructure.onebot.gateway import OneBotGateway
from app.agent.usage.recorder import StatRecorder, set_recorder
from app.infrastructure.persistence.database import Database
from app.plugin.base import ServiceAccess
from app.plugin.dispatcher import ModuleDispatcher
from app.plugin.features import FeatureRegistry
from app.plugin.features.builtin import register_builtin_controllers
from app.plugin.hooks import (
    ApiHookRegistry,
    BeforeSendHookRegistry,
    EventCompletedHookRegistry,
    LifecycleHookRegistry,
    SendHookRegistry,
)
from app.plugin.registry import ModuleRegistry

_container: Container | None = None


def _silence_websocket_logs() -> None:
    """抑制 WebSocket 连接级噪音日志，保留启动横幅与错误。

    来源（均落到 uvicorn.error）：
    - uvicorn 自身：握手时输出 `WebSocket /ws/logs [accepted]`（INFO）；
    - websockets 库：uvicorn 构造协议时把 `uvicorn.error` 传给了 websockets
      （venv 中 uvicorn websockets_impl.py:108 `logger=logging.getLogger("uvicorn.error")`），
      因此连接开闭的 `connection open/closed`（INFO）也打在 uvicorn.error 上。
    处理：对 uvicorn.error 加 Filter，仅滤掉 INFO 级的连接噪音关键词；
    WARNING+（真实错误）与启动横幅（Started/Waiting/Uvicorn running/…）不受影响。
    另对 websockets.* 独立 logger 降级，防御其他路径。
    """
    import logging

    _NOISE = ("connection open", "connection closed", "connection rejected", "WebSocket")

    class _WebSocketNoiseFilter(logging.Filter):
        """过滤 uvicorn.error 中 WebSocket 连接级 INFO 消息。"""

        def filter(self, record: logging.LogRecord) -> bool:
            if record.levelno >= logging.WARNING:
                return True
            msg = record.getMessage()
            return not any(k in msg for k in _NOISE)

    logging.getLogger("uvicorn.error").addFilter(_WebSocketNoiseFilter())
    for name in ("websockets.server", "websockets.client", "websockets.protocol"):
        logging.getLogger(name).setLevel(logging.WARNING)


def build_container(settings: Settings | None = None) -> Container:
    """构建依赖容器（对象已装配好，等待 run() 做异步初始化）。"""
    settings = settings or load_settings()
    container = Container()

    container.register(Settings, settings)

    # 核心单例
    cache = Cache()
    container.register_factory(Cache, lambda: cache)
    db = Database(settings.db_path)
    container.register_factory(Database, lambda: db)
    task_manager = TaskManager()
    container.register_factory(TaskManager, lambda: task_manager)

    # 数据统计记录器（攒批写入；查询走 /api/stats/*）
    stat_recorder = StatRecorder(db)
    # 后端主动推送：落库后广播 stats_updated，前端即时重查（不再轮询）
    from app.webui.stats_notify import notify_stats_updated

    stat_recorder.on_written = notify_stats_updated
    set_recorder(stat_recorder)
    container.register_factory(StatRecorder, lambda: stat_recorder)

    # 配置中心
    config_service = ConfigService(db, settings.project_root)
    container.register_factory(ConfigService, lambda: config_service)
    # Provider 预设服务（LLM 连接配置独立管理）
    provider_preset_service = ProviderPresetService(config_service)
    container.register_factory(ProviderPresetService, lambda: provider_preset_service)
    # Provider 运行时/模型/全局设置服务
    provider_runtime_manager = ProviderRuntimeManager(config_service)
    container.register_factory(ProviderRuntimeManager, lambda: provider_runtime_manager)
    provider_model_service = ProviderModelService(config_service, provider_runtime_manager)
    container.register_factory(ProviderModelService, lambda: provider_model_service)
    # 配置档案 / 路由服务
    # 角色预设服务
    role_preset_service = RolePresetService(config_service)
    container.register_factory(RolePresetService, lambda: role_preset_service)

    # 模块可访问的服务集合
    providers = ProviderRegistry()
    container.register_factory(ProviderRegistry, lambda: providers)
    scheduler = SchedulerService()
    container.register_factory(SchedulerService, lambda: scheduler)
    # 框架级 LLM Agent 运行时管理（配置/会话/定时/主动/工具，随 Bot 登录装配）
    agent_manager = AgentManager(
        config_service=config_service,
        task_manager=task_manager,
        provider_runtime_manager=provider_runtime_manager,
    )
    container.register_factory(AgentManager, lambda: agent_manager)
    # 全局功能/能力注册表：内置能力清单只有一处真相（app/plugin/features/builtin.py）
    feature_registry = FeatureRegistry()
    register_builtin_controllers(feature_registry, agent_manager)
    container.register_factory(FeatureRegistry, lambda: feature_registry)
    # 插件钩子注册表（模块按 bot 注册）
    send_hook_registry = SendHookRegistry()
    container.register_factory(SendHookRegistry, lambda: send_hook_registry)
    before_send_hook_registry = BeforeSendHookRegistry()
    container.register_factory(BeforeSendHookRegistry, lambda: before_send_hook_registry)
    api_hook_registry = ApiHookRegistry()
    container.register_factory(ApiHookRegistry, lambda: api_hook_registry)
    lifecycle_hook_registry = LifecycleHookRegistry()
    container.register_factory(LifecycleHookRegistry, lambda: lifecycle_hook_registry)
    event_completed_hook_registry = EventCompletedHookRegistry()
    container.register_factory(EventCompletedHookRegistry, lambda: event_completed_hook_registry)
    # 系统资源注册表：模块配置里的 resource 字段（下拉选预设）与 /api/resources 都用它
    resource_registry = ResourceRegistry()
    resource_registry.register(
        "role_preset", lambda q: role_preset_service.list_presets(),
        label="角色预设", desc="可复用的角色设定（system_prompt / 预设对话 / 工具 / 技能）",
    )
    resource_registry.register(
        "provider_preset", lambda q: provider_preset_service.list_presets(),
        label="Provider 预设", desc="模型连接配置（api_base / 密钥由框架保管）",
    )

    def _model_resources(q):
        """Provider 模型候选：label 带上模型名与能力类型，便于下拉区分。"""
        rows = provider_model_service.list_models(q.filters.get("preset_id") or None)
        wanted = str(q.filters.get("provider_type") or "").strip()
        out = []
        for row in rows or []:
            kind = str(row.get("provider_type") or "chat")
            if wanted and kind != wanted:
                continue
            name = str(row.get("name") or row.get("model") or row.get("id") or "")
            out.append({"id": row.get("id"), "name": f"{name}（{kind}）"})
        return out

    resource_registry.register(
        "provider_model", _model_resources,
        label="Provider 模型", desc="具体模型实例；可用 provider_type 过滤（如 embedding）",
    )

    def _bot_resources(q):
        """Bot 账号候选：只列「上次登录过」的连接。

        账号来自 `get_bot_accounts()` 的登录快照（`{"user_id","nickname"}`），
        不是连接配置里的字段（配置里没有 bot_id，早期版本这里读错键会永远返回空）。
        """
        accounts = config_service.get_bot_accounts() or {}
        out = []
        for index, info in sorted(accounts.items(), key=lambda kv: str(kv[0])):
            bot_id = str((info or {}).get("user_id") or "").strip()
            if not bot_id:
                continue
            nickname = str((info or {}).get("nickname") or "")
            out.append({"id": bot_id, "name": f"#{index} {nickname or bot_id}"})
        return out

    resource_registry.register("bot", _bot_resources, label="Bot 账号", desc="已配置的连接及其登录账号")
    container.register_factory(ResourceRegistry, lambda: resource_registry)

    # 模块用 LLM 客户端：让模块用「系统的 provider」而不必自备密钥
    module_llm = ModuleLLMClient(
        agent_manager=agent_manager,
        runtime_manager=provider_runtime_manager,
        model_service=provider_model_service,
    )
    container.register_factory(ModuleLLMClient, lambda: module_llm)

    def _capabilities() -> dict:
        """框架能力清单（延迟计算：services 自身此刻还在构造中）。"""
        from app.application.capabilities import collect_capabilities

        return collect_capabilities(
            services=services, features=feature_registry, resources=resource_registry
        )

    def _sessions(bot_id):
        """模块侧的会话历史门面：按 bot_id 拿进程级单例（键统一成字符串）。"""
        from app.agent.session.manager import SessionManager

        return SessionManager(str(bot_id)) if bot_id is not None else None

    def _runtime(bot_id):
        """模块侧的运行时只读访问（替代 `get_active().get(AgentManager)` 偷容器）。"""
        if agent_manager is None or bot_id is None:
            return None
        return agent_manager.get_runtime(bot_id)

    async def _tools(bot_id, **kwargs):
        """模块侧的框架工具集视图（(specs, ToolContext)）；延迟 import 避免装配期依赖。"""
        runtime = _runtime(bot_id)
        if runtime is None:
            return [], None
        from app.agent.tools.catalog import framework_tools

        return await framework_tools(runtime, **kwargs)

    services = ServiceAccess(
        cache=cache, config_service=config_service, task_manager=task_manager,
        settings=settings, providers=providers, data_sources=providers, scheduler=scheduler,
        agent_manager=agent_manager, llm=module_llm,
        provider_presets=provider_preset_service, provider_models=provider_model_service,
        send_hooks=send_hook_registry,
        before_send_hooks=before_send_hook_registry, api_hooks=api_hook_registry,
        lifecycle_hooks=lifecycle_hook_registry, event_completed_hooks=event_completed_hook_registry,
        features=feature_registry,
        role_presets=role_preset_service,
        resources=resource_registry, capabilities=_capabilities,
        sessions=_sessions, runtime=_runtime, tools=_tools,
    )
    container.register_factory(ServiceAccess, lambda: services)

    # 插件软卸载状态服务（唯一真相：module/uninstalled_modules.json）
    install_service = ModuleInstallService(settings.uninstalled_modules_file)
    container.register_factory(ModuleInstallService, lambda: install_service)

    # 插件系统
    registry = ModuleRegistry(
        plugins_dir=settings.plugins_dir,  # app/plugins（随仓库走）
        zip_plugins_dir=settings.zip_plugins_dir,  # module/plugins（用户 zip 安装）
        config_service=config_service,
        services=services,
        install_service=install_service,
    )
    container.register_factory(ModuleRegistry, lambda: registry)

    def _module_resources(q):
        """已加载模块候选（value=模块名，label=模块显示名）。"""
        out = []
        for name, instances in registry.loaded_map().items():
            first = next(iter(instances.values()), None)
            out.append({"id": name, "name": str(getattr(first, "name", "") or name)})
        return out

    resource_registry.register("module", _module_resources, label="模块", desc="已加载的模块名")

    # OneBot 网关
    gateway = OneBotGateway(settings=settings, cache=cache, logger_=logger)

    def _bot_of(bot_id):
        """按 bot_id 反查当前持有该账号的连接（IBot）；未登录/已换号时返回 None。"""
        target = str(bot_id or "").strip()
        if not target:
            return None
        for index, info in (config_service.get_bot_accounts() or {}).items():
            if str((info or {}).get("user_id") or "") == target:
                return gateway.connections.get(index)
        return None

    async def _group_resources(q):
        """群候选：按账号取（需要 bot_id），账号不在线时返回空而不是报错。

        与 `type: "list"` + `endpoint: "groups"` 同源（都走 `bot.get_group_list()`），
        区别只是这里适配「下拉选一个群」的资源引用字段。
        """
        bot = _bot_of(q.bot_id)
        if bot is None:
            return []
        resp = await bot.get_group_list()
        return [
            {
                "group_id": str(row.get("group_id", "")),
                "group_name": str(row.get("group_name") or row.get("group_id") or ""),
            }
            for row in ((resp or {}).get("data") or [])
            if row.get("group_id")
        ]

    resource_registry.register(
        "group", _group_resources, label="群", needs_bot=True,
        value_field="group_id", label_field="group_name",
        desc="该账号当前所在的群（需要 bot_id；与 list 的 groups 数据源同源）",
    )
    gateway.send_hook_registry = send_hook_registry
    gateway.before_send_hook_registry = before_send_hook_registry
    gateway.api_hook_registry = api_hook_registry
    gateway.lifecycle_hook_registry = lifecycle_hook_registry
    container.register_factory(OneBotGateway, lambda: gateway)

    # 节点注册表：内置入站链（路由 → 权限 → 派发 → Agent 兜底），框架/模块可插入/替换
    from app.plugin.nodes import (
        AgentNode,
        ModuleCommandNode,
        ModuleInvokeNode,
        ModulePermissionNode,
        ModuleRouterNode,
        NodeRegistry,
        OutboundPipeline,
    )

    node_registry = NodeRegistry()
    node_registry.register(AgentNode(agent_manager, config_service, gateway, logger))
    node_registry.register(ModuleRouterNode(registry, logger))
    node_registry.register(ModulePermissionNode(config_service, gateway, logger, agent_manager))
    node_registry.register(ModuleCommandNode(logger))
    node_registry.register(ModuleInvokeNode(logger))
    container.register_factory(NodeRegistry, lambda: node_registry)

    # 出站链：终端 SendNode，其余节点来自 NodeRegistry（插件注册即生效，用 provider 现取）
    outbound_pipeline = OutboundPipeline(provider=node_registry.outbound_nodes)
    gateway.outbound_hook_factory = lambda conn: (
        lambda action, params, timeout=None: outbound_pipeline.run(conn, action, params, timeout)
    )
    container.register_factory(OutboundPipeline, lambda: outbound_pipeline)
    # 节点注册表与出站管道回填到 ServiceAccess（它们在本文件更晚才创建，故此处赋值）
    services.node_registry = node_registry
    services.outbound = outbound_pipeline

    # 事件分发（走节点链）
    dispatcher = ModuleDispatcher(
        registry=registry, config_service=config_service, gateway=gateway, node_registry=node_registry,
        event_completed_hooks=event_completed_hook_registry, agent_manager=agent_manager,
    )
    container.register_factory(ModuleDispatcher, lambda: dispatcher)

    # 应用服务
    bot_service = BotService(
        gateway=gateway, registry=registry, config_service=config_service,
        dispatcher=dispatcher, agent_manager=agent_manager,
        lifecycle_hooks=lifecycle_hook_registry,
        resource_registry=resource_registry,
    )
    container.register_factory(BotService, lambda: bot_service)
    log_service = LogService(settings.log_dir)
    container.register_factory(LogService, lambda: log_service)

    return container


async def run(settings: Settings | None = None) -> None:
    """主入口：初始化并常驻运行。"""
    global _container
    settings = settings or load_settings()
    setup_logging(settings.log_dir)

    # 令牌为空则自动生成并写回 .env（只在此处调用：测试直接 create_app，不该被鉴权拦）。
    # 就地更新 settings，后续 build_container 注册的就是这个已带令牌的实例。
    from app.core.webui_token import ensure_webui_token, print_webui_token_banner

    token_state = ensure_webui_token(settings)

    _container = build_container(settings)
    set_active(_container)
    db = _container.get(Database)
    await db.connect()
    config_service = _container.get(ConfigService)
    await config_service.init()
    set_console_mode(config_service.get_webui_config().get("logs", {}).get("show_raw_logs", False))

    # 迁移旧的 Agent 内联 API 配置到 Provider 预设（幂等，启动时执行）
    provider_preset_service = _container.get(ProviderPresetService)
    await provider_preset_service.migrate_legacy_agent_configs()
    provider_model_service = _container.get(ProviderModelService)
    await provider_model_service.migrate_legacy_models()

    # 迁移旧 LLM 数据文件（历史模块目录 → data/llm），幂等，仅在启动时执行
    from app.agent import migrate_legacy_data

    migrate_legacy_data()

    # 加载全局模块实例：WebUI 无需等待 Bot 连接即可渲染模块列表/配置表单
    registry = _container.get(ModuleRegistry)
    await registry.load_all(bot_id=None)

    bot_service = _container.get(BotService)
    scheduler = _container.get(SchedulerService)

    await bot_service.start()
    # 定时任务在模块加载时自动注册并启动，无需手动 start

    # 启动 WebUI
    import uvicorn

    from app.webui.app import create_app

    _silence_websocket_logs()

    app = create_app(_container)
    host, port = settings.webui_host, settings.webui_port
    logger.info(f"WebUI 启动中: http://{host}:{port}（token: 已设置）")
    # 令牌明文只上控制台，刻意走 print 不落 logs/*.log
    print_webui_token_banner(token_state, host, port)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info", access_log=False))

    try:
        await server.serve()
    finally:
        logger.info("正在关闭…")
        await scheduler.shutdown()
        await bot_service.shutdown()
        # 先停止 Agent 运行时（定时/主动），再取消残留任务、关 DB
        _container.get(AgentManager).shutdown()
        task_manager = _container.get(TaskManager)
        task_manager.cancel_all()
        # 统计记录器：退出前强制落库（禁止 fire-and-forget，否则丢最后一批）
        try:
            await _container.get(StatRecorder).close()
        except Exception as e:  # noqa: BLE001 —— 统计收尾失败不该阻断进程退出
            logger.debug(f"[Stats] 关闭统计记录器失败（已忽略）: {e}")
        await db.close()
        logger.info("已关闭")


def main() -> None:
    """同步入口（console script / python -m）。"""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("已退出")


def get_container() -> Container:
    """兼容别名：真正的持有者在 `app.core.container.get_active()`。

    保留它是因为测试与少量调用方仍从 `app.bootstrap` 取；生产代码**不准** import
    `app.bootstrap`（`tests/test_architecture_boundaries.py` 有预算把关）。
    """
    return get_active()


if __name__ == "__main__":
    main()
