"""应用装配根：构建容器、初始化、启动网关/调度器/WebUI。

依赖规则：此处是唯一「知道所有组件」的装配点；
组件之间只依赖各自的抽象，循环依赖在此暴露。
"""

from __future__ import annotations

import asyncio
import contextlib

from app.core.container import Container
from app.core.logger import logger, set_console_mode, setup_logging
from app.core.settings import Settings, load_settings
from app.core.task_manager import TaskManager
from app.infrastructure.cache import Cache
from app.infrastructure.config.config_service import ConfigService
from app.infrastructure.onebot.gateway import OneBotGateway
from app.infrastructure.persistence.database import Database
from app.llm.manager import AgentManager
from app.modules.base import ServiceAccess
from app.modules.dispatcher import ModuleDispatcher
from app.modules.hooks import (
    ApiHookRegistry,
    BeforeSendHookRegistry,
    EventCompletedHookRegistry,
    LifecycleHookRegistry,
    SendHookRegistry,
)
from app.modules.registry import ModuleRegistry
from app.services.bot_service import BotService
from app.llm.providers.runtime_manager import ProviderRuntimeManager
from app.services.config_profile_service import ConfigProfileService
from app.services.log_service import LogService
from app.services.provider_model_service import ProviderModelService
from app.services.provider_preset_service import ProviderPresetService
from app.services.provider_service import ProviderRegistry
from app.services.scheduler import SchedulerService

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
    config_profile_service = ConfigProfileService(config_service)
    container.register_factory(ConfigProfileService, lambda: config_profile_service)

    # 模块可访问的服务集合
    providers = ProviderRegistry()
    container.register_factory(ProviderRegistry, lambda: providers)
    scheduler = SchedulerService()
    container.register_factory(SchedulerService, lambda: scheduler)
    # 框架级 LLM Agent 运行时管理（配置/会话/定时/主动/工具，随 Bot 登录装配）
    agent_manager = AgentManager(config_service=config_service, task_manager=task_manager)
    container.register_factory(AgentManager, lambda: agent_manager)
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
    services = ServiceAccess(
        cache=cache, config_service=config_service, task_manager=task_manager,
        settings=settings, providers=providers, scheduler=scheduler,
        agent_manager=agent_manager, send_hooks=send_hook_registry,
        before_send_hooks=before_send_hook_registry, api_hooks=api_hook_registry,
        lifecycle_hooks=lifecycle_hook_registry, event_completed_hooks=event_completed_hook_registry,
    )
    container.register_factory(ServiceAccess, lambda: services)

    # 模块系统
    registry = ModuleRegistry(modules_dir=settings.modules_dir, config_service=config_service, services=services)
    container.register_factory(ModuleRegistry, lambda: registry)

    # OneBot 网关
    gateway = OneBotGateway(settings=settings, cache=cache, logger_=logger)
    gateway.send_hook_registry = send_hook_registry
    gateway.before_send_hook_registry = before_send_hook_registry
    gateway.api_hook_registry = api_hook_registry
    gateway.lifecycle_hook_registry = lifecycle_hook_registry
    container.register_factory(OneBotGateway, lambda: gateway)

    # 节点注册表：内置入站链（路由 → 权限 → 派发 → Agent 兜底），框架/模块可插入/替换
    from app.modules.nodes import AgentNode, ModuleInvokeNode, ModulePermissionNode, ModuleRouterNode
    from app.nodes import NodeRegistry
    from app.nodes.outbound import OutboundPipeline, SendNode

    node_registry = NodeRegistry()
    node_registry.register(AgentNode(agent_manager, config_service, gateway, logger))
    node_registry.register(ModuleRouterNode(registry, logger))
    node_registry.register(ModulePermissionNode(config_service, gateway, logger))
    node_registry.register(ModuleInvokeNode(logger))
    container.register_factory(NodeRegistry, lambda: node_registry)

    # 出站链：默认仅终端 SendNode（发送前可插节点拦截/改写）
    outbound_pipeline = OutboundPipeline([SendNode()])
    gateway.outbound_hook_factory = lambda conn: (
        lambda action, params: outbound_pipeline.run(conn, action, params)
    )

    # 事件分发（走节点链）
    dispatcher = ModuleDispatcher(
        registry=registry, config_service=config_service, gateway=gateway, node_registry=node_registry,
        event_completed_hooks=event_completed_hook_registry,
    )
    container.register_factory(ModuleDispatcher, lambda: dispatcher)

    # 应用服务
    bot_service = BotService(
        gateway=gateway, registry=registry, config_service=config_service,
        dispatcher=dispatcher, agent_manager=agent_manager,
        lifecycle_hooks=lifecycle_hook_registry,
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
    _container = build_container(settings)
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
    from app.llm import migrate_legacy_data

    migrate_legacy_data()

    # 加载全局模块实例：WebUI 无需等待 Bot 连接即可渲染模块列表/配置表单
    registry = _container.get(ModuleRegistry)
    await registry.load_all(bot_id=None)

    bot_service = _container.get(BotService)
    scheduler = _container.get(SchedulerService)

    await bot_service.start()
    # 定时任务在模块加载时自动注册并启动，无需手动 start

    # 启动 WebUI
    from app.webui.app import create_app
    import uvicorn

    _silence_websocket_logs()

    app = create_app(_container)
    host, port = settings.webui_host, settings.webui_port
    logger.info(f"WebUI 启动中: http://{host}:{port}" + (f"（token: {'已设置' if settings.webui_token else '未设置'}）"))
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
        await db.close()
        logger.info("已关闭")


def main() -> None:
    """同步入口（console script / python -m）。"""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("已退出")


def get_container() -> Container:
    if _container is None:
        raise RuntimeError("容器尚未初始化，请先调用 app.bootstrap.run()")
    return _container


if __name__ == "__main__":
    main()
