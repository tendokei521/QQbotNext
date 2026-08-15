"""WebUI 应用装配：FastAPI + 路由挂载 + 静态资源 + 配置变更广播。

仅做组装；业务逻辑在 api/ 路由与 services/ 中。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.logger import webui_logger
from app.core.event_bus import BotLifecycleEvent, ConfigChangedEvent, event_bus
from app.webui.api import agent as agent_router
from app.webui.api import bots as bots_router
from app.webui.api import logs as logs_router
from app.webui.api import modules as modules_router
from app.webui.api import webui_cfg as webui_router
from app.webui.ws import build_ws_router, manager

_BASE_DIR = Path(__file__).resolve().parent


def create_app(container) -> FastAPI:
    """根据容器装配 FastAPI 应用。"""
    app = FastAPI(title="QQBot Next 管理后台", version="2.0.0")
    app.state.container = container

    # 静态资源 / 模板
    app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "static")), name="static")
    templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))

    # 路由
    app.include_router(bots_router.router, prefix="/api")
    app.include_router(modules_router.router, prefix="/api")
    app.include_router(agent_router.router, prefix="/api")
    app.include_router(logs_router.router, prefix="/api")
    app.include_router(webui_router.router, prefix="/api")
    app.include_router(build_ws_router())

    # 可选鉴权（WEBUI_TOKEN 非空时生效）
    _install_auth_middleware(app, container)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        from app.infrastructure.config.config_service import ConfigService
        from app.services.bot_service import BotService
        from app.services.log_service import LogService

        bot_service = container.get(BotService)
        config_service = container.get(ConfigService)
        log_service = container.get(LogService)

        webui_cfg = config_service.get_webui_config()
        logs = log_service.get_recent_logs(
            webui_cfg.get("logs", {}).get("max_lines", 50),
            webui_cfg.get("logs", {}).get("visible_levels", ["info", "warning", "error"]),
        )
        # 注入访问令牌：GET / 本身不鉴权，前端凭此 token 调用受保护的 /api 与 /ws/logs
        from app.core.settings import Settings

        webui_token = container.get(Settings).webui_token or ""
        return templates.TemplateResponse(
            request, "index.html", {
                "request": request,
                "bots": bot_service.get_bots_data(),
                "modules": bot_service.get_modules_data(),
                "logs": logs,
                "webui_config": webui_cfg,
                "webui_token": webui_token,
            }
        )

    # 配置变更 → WS 广播（单点广播源，前端按 type 处理）
    _install_config_listener(container)
    # Bot 连接状态变化 → WS 广播（前端实时刷新顶栏/弹窗状态）
    _install_bot_lifecycle_listener(container)

    return app


_bot_lifecycle_subscribed = False


def _install_bot_lifecycle_listener(container) -> None:
    """订阅 BotLifecycleEvent：连接状态变化实时推送给前端。

    - state=connected（登录成功，带真实 bot_id）→ 顺带广播 modules_reloaded，
      前端收到后刷新模块数据（登录时模块刚装配完）；
    - 同一状态经 gateway._notify_status 去重，不会刷屏。
    """
    global _bot_lifecycle_subscribed
    if _bot_lifecycle_subscribed:
        return
    _bot_lifecycle_subscribed = True

    from app.infrastructure.onebot.gateway import OneBotGateway

    gateway = container.get(OneBotGateway)

    async def on_bot_lifecycle(event: BotLifecycleEvent) -> None:
        info = gateway.get_bot_info_by_index(event.bot_index) or {}
        await manager.broadcast(json.dumps({
            "type": "bot_status_updated",
            "bot": {
                "index": event.bot_index,
                "bot_id": event.bot_id,
                "status": event.state,
                "last_error": event.detail or None,
                "login_info": info.get("login_info"),
            },
        }))
        if event.state == "connected" and event.bot_id:
            await manager.broadcast(json.dumps({"type": "modules_reloaded", "bot_id": event.bot_id}))

    event_bus.subscribe(BotLifecycleEvent, on_bot_lifecycle)
    webui_logger.debug("[WebUI] Bot 生命周期监听已注册")


def _install_config_listener(container) -> None:
    from app.core.logger import logger
    from app.infrastructure.config.config_service import ConfigService

    config_service = container.get(ConfigService)

    async def on_config_change(scope: str, payload):
        if scope == "webui":
            cfg = payload
            await manager.broadcast(json.dumps({"type": "webui_config_updated", "config": cfg}))
            await manager.broadcast(json.dumps({
                "type": "single_service_updated",
                "single_service": cfg.get("single_service", {}),
            }))
            await manager.broadcast(json.dumps({
                "type": "multi_group_updated",
                "multi_group": cfg.get("multi_group", {"show_all": False, "groups": {}}),
            }))
        elif scope == "module_config":
            await manager.broadcast(json.dumps({
                "type": "module_config_updated",
                "module": payload["module"],
                "bot_id": payload["bot_id"],
                "config": payload["config"],
            }))
        elif scope == "authority":
            auth = payload.get("authority", {})
            await manager.broadcast(json.dumps({
                "type": "module_authority_updated",
                "module": payload.get("module"),
                "bot_id": payload.get("bot_id"),
                "enabled": auth.get("enabled", True),
                "permission": {
                    "group_mode": auth.get("group_mode", "blacklist"),
                    "group_list": auth.get("group_list", []),
                    "user_mode": auth.get("user_mode", "blacklist"),
                    "user_list": auth.get("user_list", []),
                },
            }))

    config_service.on_change(on_config_change)
    logger.debug("[WebUI] 配置变更监听已注册")


def _install_auth_middleware(app: FastAPI, container) -> None:
    from app.core.settings import Settings
    from starlette.middleware.base import BaseHTTPMiddleware

    settings = container.get(Settings)
    token = (settings.webui_token or "").strip()
    if not token:
        return
    webui_logger.info("[WebUI] 已启用访问令牌鉴权")

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if request.url.path.startswith("/api") or request.url.path == "/ws/logs":
            auth = request.headers.get("authorization", "")
            query_token = request.query_params.get("token", "")
            provided = auth[7:] if auth.lower().startswith("bearer ") else query_token
            if provided != token:
                return JSONResponse(status_code=401, content={"status": "error", "message": "未授权"})
        return await call_next(request)
