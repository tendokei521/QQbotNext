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
from app.webui.api import config_profiles as config_profiles_router
from app.webui.api import logs as logs_router
from app.webui.api import modules as modules_router
from app.webui.api import provider_presets as provider_presets_router
from app.webui.api import webui_cfg as webui_router
from app.webui.ws import build_ws_router, manager

_BASE_DIR = Path(__file__).resolve().parent
# 新版 Dashboard（Vue3+Vuetify）构建产物目录：dashboard/dist
_DASHBOARD_DIST = _BASE_DIR.parent.parent / "dashboard" / "dist"


def _render_dashboard(request: Request, webui_token: str) -> HTMLResponse | None:
    """渲染新版 Dashboard（存在构建产物时使用）。

    - 读取 dashboard/dist/index.html，注入 window.WEBUI_TOKEN（鉴权引导）；
    - 不存在产物时返回 None，由调用方回退旧版模板。
    """
    index = _DASHBOARD_DIST / "index.html"
    if not index.exists():
        return None
    html = index.read_text(encoding="utf-8")
    token_script = f"<script>window.WEBUI_TOKEN={json.dumps(webui_token)};</script>"
    if "<head>" in html:
        html = html.replace("<head>", f"<head>{token_script}", 1)
    else:
        html = token_script + html
    return HTMLResponse(html)


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
    app.include_router(provider_presets_router.router, prefix="/api")
    app.include_router(provider_presets_router.models_router, prefix="/api")
    app.include_router(provider_presets_router.settings_router, prefix="/api")
    app.include_router(config_profiles_router.router, prefix="/api")
    app.include_router(webui_router.router, prefix="/api")
    app.include_router(build_ws_router())

    # 可选鉴权（WEBUI_TOKEN 非空时生效）
    _install_auth_middleware(app, container)

    # 新版 Dashboard 首页（构建产物存在时优先；JS 资源经根挂载提供）
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        from app.core.settings import Settings

        webui_token = container.get(Settings).webui_token or ""
        dashboard = _render_dashboard(request, webui_token)
        if dashboard is not None:
            return dashboard

        # ---- 旧版 UI 回退路径（dashboard/dist 不存在时） ----
        from app.infrastructure.config.config_service import ConfigService
        from app.services.bot_service import BotService
        from app.services.log_service import LogService

        bot_service = container.get(BotService)
        config_service = container.get(ConfigService)
        log_service = container.get(LogService)

        webui_cfg = config_service.get_webui_config()
        source = "user" if not webui_cfg.get("logs", {}).get("show_raw_logs", False) else "debug"
        logs = log_service.get_recent_logs(
            webui_cfg.get("logs", {}).get("max_lines", 50),
            webui_cfg.get("logs", {}).get("visible_levels", ["info", "warning", "error"]),
            source=source,
        )
        # 注入访问令牌：GET / 本身不鉴权，前端凭此 token 调用受保护的 /api 与 /ws/logs
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

    # 旧版 UI 保留路径：/legacy（无论新版是否存在都可访问，作回退）
    @app.get("/legacy", response_class=HTMLResponse)
    async def legacy_index(request: Request):
        from app.infrastructure.config.config_service import ConfigService
        from app.services.bot_service import BotService
        from app.services.log_service import LogService

        bot_service = container.get(BotService)
        config_service = container.get(ConfigService)
        log_service = container.get(LogService)

        webui_cfg = config_service.get_webui_config()
        source = "user" if not webui_cfg.get("logs", {}).get("show_raw_logs", False) else "debug"
        logs = log_service.get_recent_logs(
            webui_cfg.get("logs", {}).get("max_lines", 50),
            webui_cfg.get("logs", {}).get("visible_levels", ["info", "warning", "error"]),
            source=source,
        )
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

    # 新版 Dashboard 静态资源根挂载（放在所有路由之后，仅兜底未匹配路径：
    # /assets/* 等由这里提供；/api、/static、/legacy 等已在上方注册，优先级更高）
    if _DASHBOARD_DIST.exists():
        app.mount("/", StaticFiles(directory=str(_DASHBOARD_DIST), html=True), name="dashboard")

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
    from app.core.logger import logger, set_console_mode
    from app.infrastructure.config.config_service import ConfigService

    config_service = container.get(ConfigService)

    async def on_config_change(scope: str, payload):
        if scope == "webui":
            cfg = payload
            set_console_mode(cfg.get("logs", {}).get("show_raw_logs", False))
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
        elif scope == "provider_presets":
            await manager.broadcast(json.dumps({
                "type": "provider_presets_updated",
                "presets": payload,
            }))
        elif scope == "provider_models":
            await manager.broadcast(json.dumps({
                "type": "provider_models_updated",
                "models": payload,
            }))
        elif scope == "provider_settings":
            await manager.broadcast(json.dumps({
                "type": "provider_settings_updated",
                "settings": payload,
            }))
        elif scope == "config_profiles":
            await manager.broadcast(json.dumps({
                "type": "config_profiles_updated",
                "profiles": payload,
            }))
        elif scope == "config_routes":
            await manager.broadcast(json.dumps({
                "type": "config_routes_updated",
                "routes": payload,
            }))

    config_service.on_change(on_config_change)
    logger.debug("[WebUI] 配置变更监听已注册")


def _install_auth_middleware(app: FastAPI, container) -> None:
    import base64

    from app.core.settings import Settings
    from starlette.middleware.base import BaseHTTPMiddleware

    settings = container.get(Settings)
    token = (settings.webui_token or "").strip()
    if not token:
        return
    webui_logger.info("[WebUI] 已启用访问令牌鉴权")

    def _extract_token(request: Request) -> str:
        """从 Authorization(Bearer/Basic) 或 query token 中提取令牌。"""
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        if auth.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(auth[6:]).decode("utf-8", errors="ignore")
                # Basic 格式为 username:password，这里把 password 当作 WebUI token
                return decoded.split(":", 1)[1] if ":" in decoded else ""
            except Exception:
                return ""
        return request.query_params.get("token", "").strip()

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        # 所有 HTTP 路由（含首页/旧版 UI/模块自定义页/静态资源）都要求鉴权，
        # 避免 token 被未授权访问者从页面源码中取走。
        if _extract_token(request) != token:
            if request.url.path.startswith("/api"):
                return JSONResponse(status_code=401, content={"status": "error", "message": "未授权"})
            # 非 API 页面返回 Basic 质询，浏览器会弹出登录框；用户以 token 作为密码即可。
            return JSONResponse(
                status_code=401,
                content={"status": "error", "message": "未授权"},
                headers={"WWW-Authenticate": 'Basic realm="QQBotNext WebUI"'},
            )
        return await call_next(request)
