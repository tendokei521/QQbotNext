"""WebUI 应用装配：FastAPI + 路由挂载 + 静态资源 + 配置变更广播。

仅做组装；业务逻辑在 api/ 路由与 services/ 中。

## 前端只有一个

这里只服务 `app/webui/dashboard/dist`（Vue3 + Vuetify 构建产物）。历史上还并存一套手写的
`static/main.js` + `templates/index.html`（约 4,800 行），同一张 Agent 配置表单实现了
两遍，**schema 每加一个字段要改两处、第二处极易漏且没有测试会红**。P6 已删除旧版，
`/legacy` 与 `/static` 一并移除。
"""

from __future__ import annotations

import json
from html import escape as html_escape
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.core.event_bus import BotAccountUpdatedEvent, BotLifecycleEvent, event_bus
from app.core.logger import webui_logger
from app.webui.api import agent as agent_router
from app.webui.api import bots as bots_router
from app.webui.api import bundles as bundles_router
from app.webui.api import framework as framework_router
from app.webui.api import groups as groups_router
from app.webui.api import logs as logs_router
from app.webui.api import media as media_router
from app.webui.api import modules as modules_router
from app.webui.api import policy as policy_router
from app.webui.api import protocol as protocol_router
from app.webui.api import prompt_dump as prompt_dump_router
from app.webui.api import provider_presets as provider_presets_router
from app.webui.api import readiness as readiness_router
from app.webui.api import responder as responder_router
from app.webui.api import resources as resources_router
from app.webui.api import role_presets as role_presets_router
from app.webui.api import sessions as sessions_router
from app.webui.api import setup as setup_router
from app.webui.api import stats as stats_router
from app.webui.api import webui_cfg as webui_router
from app.webui.auth import (
    QUERY_TOKEN_KEYS,
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    is_authenticated,
    issue_session,
    login_rate_limiter,
    query_token_from,
    safe_next_path,
    tokens_match,
)
from app.webui.ws import build_ws_router, manager

_BASE_DIR = Path(__file__).resolve().parent
# 前端构建产物目录：app/webui/dashboard/dist
_DASHBOARD_DIST = _BASE_DIR / "dashboard" / "dist"
# 独立登录页模板（后端直出，不需要前端构建）
_LOGIN_TEMPLATE = _BASE_DIR / "templates" / "login.html"

#: 构建产物缺失时的提示页（不用模板引擎，避免为一个提示页多养一套模板依赖）
_MISSING_DIST_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>前端未构建</title></head>
<body style="font-family:system-ui;max-width:44em;margin:4em auto;line-height:1.7">
<h1>前端尚未构建</h1>
<p>后端已启动，但没有找到 <code>app/webui/dashboard/dist/index.html</code>。</p>
<p>请在仓库根执行：</p>
<pre style="background:#f4f4f4;padding:1em;border-radius:6px">cd app/webui/dashboard
pnpm install
pnpm build</pre>
<p>构建完成后刷新本页即可。</p>
</body></html>"""

#: 配置变更 scope → 受影响的资源种类（`resources_updated` 广播用；空元组 = 不影响任何资源）
_CONFIG_SCOPE_RESOURCES: dict[str, tuple[str, ...]] = {
    "role_presets": ("role_preset",),
    "provider_presets": ("provider_preset",),
    "provider_models": ("provider_model",),
    "provider_settings": ("provider_model",),
}


def _render_dashboard(request: Request) -> HTMLResponse:
    """渲染 Dashboard 首页：读取 app/webui/dashboard/dist/index.html。

    首页**不再注入令牌**：鉴权改由 HttpOnly 会话 cookie 承担，令牌对页面 JS 不可见
    （从前注入 `window.WEBUI_TOKEN` 等于把令牌暴露给任何 XSS）。
    插件自定义页（`/module/{name}/page`）仍注入，那是既有的第三方页面契约。
    """
    index = _DASHBOARD_DIST / "index.html"
    if not index.exists():
        return HTMLResponse(_MISSING_DIST_HTML, status_code=503)
    response = HTMLResponse(index.read_text(encoding="utf-8"))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


#: 登录页错误提示的内联图标（不放外部字体：登录页零依赖，未认证时也不加载任何资源）
_LOGIN_ALERT_ICON = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" aria-hidden="true">'
    '<circle cx="12" cy="12" r="9"/>'
    '<path d="M12 7.6v5.4"/>'
    '<circle cx="12" cy="16.6" r="0.95" fill="currentColor" stroke="none"/>'
    "</svg>"
)


def _render_login_page(next_path: str, error: str = "") -> HTMLResponse:
    """渲染独立登录页（后端直出，不依赖 dashboard 构建产物）。"""
    error_block = ""
    if error:
        error_block = (
            '<div class="alert" role="alert">'
            f"{_LOGIN_ALERT_ICON}<span>{html_escape(error)}</span>"
            "</div>"
        )
    page = (
        _LOGIN_TEMPLATE.read_text(encoding="utf-8")
        .replace("{error_block}", error_block)
        .replace("{next_path}", html_escape(next_path))
    )
    response = HTMLResponse(page)
    # 登录页本身绝不能被缓存：否则登出后点「后退」还能看到
    response.headers["Cache-Control"] = "no-store"
    return response


def _is_secure_request(request: Request) -> bool:
    """HTTPS（含反向代理转发的 https）才给 cookie 加 Secure 标记。"""
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def _client_address(request: Request) -> str:
    """登录限流用的来源标识；反代后取 X-Forwarded-For 首个地址。"""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _set_session_cookie(response, token: str, request: Request):
    """下发 HttpOnly 会话 cookie（登录成功与「URL 带令牌」两条路径共用）。"""
    response.set_cookie(
        SESSION_COOKIE,
        issue_session(token),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,          # JS 读不到，XSS 偷不走
        samesite="lax",         # 挡住跨站 POST 携带（CSRF 基本面）
        secure=_is_secure_request(request),
        path="/",
    )
    return response


def _url_without_token_query(request: Request) -> str:
    """去掉查询串里的令牌键，其余参数原样保留（`?token=x&mode=raw` → `?mode=raw`）。"""
    kept = [(k, v) for k, v in request.query_params.multi_items() if k not in QUERY_TOKEN_KEYS]
    path = request.url.path or "/"
    return f"{path}?{urlencode(kept)}" if kept else path


def _wants_html(request: Request) -> bool:
    """浏览器地址栏/页面导航（而非 XHR、图片、脚本）的请求。"""
    return "text/html" in request.headers.get("accept", "")


def create_app(container) -> FastAPI:
    """根据容器装配 FastAPI 应用。"""
    app = FastAPI(title="QQBot Next 管理后台", version="2.0.0")
    app.state.container = container

    # 路由
    app.include_router(bots_router.router, prefix="/api")
    app.include_router(bundles_router.router, prefix="/api")
    app.include_router(groups_router.router, prefix="/api")
    app.include_router(policy_router.router, prefix="/api")
    app.include_router(protocol_router.router, prefix="/api")
    app.include_router(readiness_router.router, prefix="/api")
    app.include_router(setup_router.router, prefix="/api")
    app.include_router(modules_router.router, prefix="/api")
    app.include_router(agent_router.router, prefix="/api")
    app.include_router(logs_router.router, prefix="/api")
    app.include_router(provider_presets_router.router, prefix="/api")
    app.include_router(provider_presets_router.models_router, prefix="/api")
    app.include_router(provider_presets_router.settings_router, prefix="/api")
    app.include_router(sessions_router.router, prefix="/api")
    app.include_router(stats_router.router, prefix="/api")
    app.include_router(media_router.router, prefix="/api")
    app.include_router(role_presets_router.router, prefix="/api")
    app.include_router(prompt_dump_router.router, prefix="/api")
    app.include_router(framework_router.router, prefix="/api")
    app.include_router(resources_router.router, prefix="/api")
    app.include_router(responder_router.router, prefix="/api")
    app.include_router(webui_router.router, prefix="/api")
    app.include_router(build_ws_router())

    # 可选鉴权（WEBUI_TOKEN 非空时生效）
    _install_auth_middleware(app, container)

    # Dashboard 首页（构建产物缺失时给出「请先 pnpm build」的提示页）
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return _render_dashboard(request)

    _install_login_routes(app, container)

    # Dashboard 静态资源根挂载（放在所有路由之后，仅兜底未匹配路径：
    # /assets/* 等由这里提供；/api 与 /ws 已在上方注册，优先级更高）
    if _DASHBOARD_DIST.exists():
        app.mount("/", StaticFiles(directory=str(_DASHBOARD_DIST), html=True), name="dashboard")

    # 配置变更 → WS 广播（单点广播源，前端按 type 处理）
    _install_config_listener(container)
    # Bot 连接状态变化 → WS 广播（前端实时刷新顶栏/弹窗状态）
    _install_bot_lifecycle_listener(container)

    return app


_bot_lifecycle_subscribed = False


def _install_login_routes(app: FastAPI, container) -> None:
    """注册独立登录页与登出（`/login` GET/POST、`/logout`）。

    登录成功后下发 HttpOnly 会话 cookie，之后浏览器自动携带，页面 JS 不再接触令牌。
    """
    from app.core.settings import Settings

    def _expected_token() -> str:
        return (container.get(Settings).webui_token or "").strip()

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, next: str = "/"):
        token = _expected_token()
        target = safe_next_path(next)
        # 未启用鉴权时登录页没有意义，直接放行到首页
        if not token:
            return RedirectResponse(target, status_code=302)

        # /login?accesstoken=<令牌> 同样支持一键登录（登录页是公开路径，
        # 中间件不会替它换 cookie，所以这里显式处理）
        if tokens_match(query_token_from(request.query_params), token):
            return _set_session_cookie(RedirectResponse(target, status_code=303), token, request)

        # 已登录用户访问 /login 直接送回目标页，避免重复登录
        if is_authenticated(
            expected_token=token,
            session_cookie=request.cookies.get(SESSION_COOKIE),
        ):
            return RedirectResponse(target, status_code=302)
        return _render_login_page(target)

    @app.post("/login")
    async def login_submit(request: Request):
        token = _expected_token()
        if not token:
            return RedirectResponse("/", status_code=302)

        source = _client_address(request)
        if login_rate_limiter.is_blocked(source):
            retry = login_rate_limiter.retry_after(source)
            response = _render_login_page("/", error=f"尝试次数过多，请 {retry} 秒后再试。")
            response.status_code = 429
            response.headers["Retry-After"] = str(retry)
            return response

        form = await request.form()
        submitted = str(form.get("token", "") or "").strip()
        target = safe_next_path(str(form.get("next", "/") or "/"))

        if not tokens_match(submitted, token):
            login_rate_limiter.record_failure(source)
            webui_logger.warning(f"[WebUI] 登录失败，来源 {source}")
            response = _render_login_page(target, error="令牌不正确，请检查后重试。")
            response.status_code = 401
            return response

        login_rate_limiter.reset(source)
        webui_logger.info(f"[WebUI] 登录成功，来源 {source}")
        return _set_session_cookie(RedirectResponse(target, status_code=303), token, request)

    @app.api_route("/logout", methods=["GET", "POST"])
    async def logout(request: Request):
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/api/auth/session")
    async def auth_session(request: Request):
        """前端启动时探测登录态（返回是否已鉴权，不返回令牌本身）。"""
        token = _expected_token()
        return JSONResponse(content={
            "authenticated": is_authenticated(
                expected_token=token,
                authorization=request.headers.get("authorization", ""),
                query_token=query_token_from(request.query_params),
                session_cookie=request.cookies.get(SESSION_COOKIE),
            ),
            "auth_enabled": bool(token),
        })


def _install_bot_lifecycle_listener(container) -> None:
    """订阅 BotLifecycleEvent / BotAccountUpdatedEvent：连接状态与账号变化实时推前端。

    - state=connected（登录成功，带真实 bot_id）→ 顺带广播 modules_reloaded，
      前端收到后刷新模块数据（登录时模块刚装配完）；
    - 同一状态经 gateway._notify_status 去重，不会刷屏；
    - 账号变化（换了账号登录）单独事件：即使状态没变也要让前端换掉「上次连接账号」。
    """
    global _bot_lifecycle_subscribed
    if _bot_lifecycle_subscribed:
        return
    _bot_lifecycle_subscribed = True

    from app.infrastructure.onebot.gateway import OneBotGateway

    gateway = container.get(OneBotGateway)

    def _status_payload(account_id: str | None, **overrides) -> dict:
        """组装某账号的最新账号/连接信息（含 last_account / account 回退字段）。"""
        stat = {}
        for s in gateway.get_bots_info():
            if s["id"] == account_id:
                stat = s
                break
        return {
            "id": account_id,
            "name": stat.get("name") or account_id or "",
            "bot_id": stat.get("bot_id"),
            "status": stat.get("status"),
            "last_error": stat.get("last_error"),
            "login_info": stat.get("login_info"),
            "last_account": stat.get("last_account"),
            "account": stat.get("account"),
            "reconnect_attempts": stat.get("reconnect_attempts"),
            "ws_url": stat.get("ws_url"),
            # 连接方向与反向监听状态：前端据此显示「正向地址 / 反向监听地址 / 监听中」
            "conn_mode": stat.get("conn_mode"),
            "listening": stat.get("listening"),
            # v3：账号级 owner QQ 已取消，拥有者身份改由「权限管理 → 拥有者名册」提供
            "owner_id": None,
            "auto_connect": stat.get("auto_connect"),
            **overrides,
        }

    async def on_bot_lifecycle(event: BotLifecycleEvent) -> None:
        await manager.broadcast(json.dumps({
            "type": "bot_status_updated",
            # 状态是权威值：连接中/断开由事件给，避免读到竞态中的旧状态
            "bot": _status_payload(
                event.account_id,
                bot_id=event.bot_id,
                status=event.state,
                last_error=event.detail or None,
            ),
        }))
        if event.state == "connected" and event.account_id:
            await manager.broadcast(json.dumps({
                "type": "modules_reloaded",
                "account_id": event.account_id,
                "bot_id": event.bot_id,
            }))

    event_bus.subscribe(BotLifecycleEvent, on_bot_lifecycle)
    webui_logger.debug("[WebUI] Bot 生命周期监听已注册")

    async def on_bot_account_updated(event: BotAccountUpdatedEvent) -> None:
        # 账号换了 QQ 但状态可能没变 → 单独推一次账号信息
        await manager.broadcast(json.dumps({
            "type": "bot_status_updated",
            "bot": _status_payload(event.account_id, bot_id=event.user_id),
        }))

    event_bus.subscribe(BotAccountUpdatedEvent, on_bot_account_updated)
    webui_logger.debug("[WebUI] Bot 账号变更监听已注册")


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
                "account_id": payload.get("account_id", payload.get("bot_id")),
                "config": payload["config"],
            }))
        elif scope == "authority":
            auth = payload.get("authority", {})
            await manager.broadcast(json.dumps({
                "type": "module_authority_updated",
                "module": payload.get("module"),
                "account_id": payload.get("account_id", payload.get("bot_id")),
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
        elif scope == "role_presets":
            await manager.broadcast(json.dumps({
                "type": "role_presets_updated",
                "presets": payload,
            }))

        # 资源导向的通用事件：前端资源选择器据此**只刷新对应种类的候选**，
        # 不必自己把上面这些 scope 映射成资源种类（映射只在这一处维护）
        kinds = _CONFIG_SCOPE_RESOURCES.get(scope)
        if kinds:
            await manager.broadcast(json.dumps({
                "type": "resources_updated",
                "kinds": list(kinds),
            }))

    config_service.on_change(on_config_change)
    logger.debug("[WebUI] 配置变更监听已注册")


#: 无需鉴权即可访问的路径（登录页本身、登录提交、登出、静态资源探测）
_PUBLIC_PATHS = ("/login", "/logout", "/favicon.ico")


def _install_auth_middleware(app: FastAPI, container) -> None:
    """安装鉴权：会话 cookie 优先，静态令牌（Bearer/Basic/query）并存。

    未认证时的行为按路径分流，而不是一律 401：

    - `/api/*` → 401 JSON（前端 axios 需要明确的错误码，不能用 302 骗它）
    - 页面请求（Accept 含 text/html）→ 302 到 `/login?next=<原路径>`，这就是「独立登录页」
    - 其它（静态资源等）→ 401，避免把 JS/CSS 请求重定向成 HTML 让浏览器报 MIME 错
    """
    from app.core.settings import Settings

    settings = container.get(Settings)
    if not (settings.webui_token or "").strip():
        # 令牌为空 = 不启用鉴权（测试与纯本机调试走这条路，保持历史行为）
        return
    webui_logger.info("[WebUI] 已启用访问令牌鉴权（登录页 + Bearer/Basic/query 并存）")

    def _expected_token() -> str:
        # 每次请求现取：令牌可能被 ensure_webui_token 在运行期写入
        return (container.get(Settings).webui_token or "").strip()

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith("/api/auth/"):
            return await call_next(request)

        token = _expected_token()
        if not token:
            return await call_next(request)

        query_token = query_token_from(request.query_params)
        authenticated = is_authenticated(
            expected_token=token,
            authorization=request.headers.get("authorization", ""),
            query_token=query_token,
            session_cookie=request.cookies.get(SESSION_COOKIE),
        )
        if authenticated:
            # 「一键登录」：页面导航带了 ?accesstoken=/?token= 且校验通过时，
            # 换成会话 cookie 并 303 到去掉令牌的干净 URL。
            # 这样令牌不会留在地址栏、浏览历史与后续 Referer 里。
            # 仅限 GET + HTML 文档请求：XHR、<img>、脚本仍走各自的原通道，不能被重定向。
            if query_token and request.method == "GET" and _wants_html(request):
                clean = _url_without_token_query(request)
                if clean != f"{request.url.path}?{request.url.query}":
                    response = RedirectResponse(clean, status_code=303)
                    # 只有「查询令牌本身有效」才刷新会话；已登录用户带着一个
                    # 过期/写错的令牌访问页面时，只把令牌从地址栏摘掉即可。
                    if tokens_match(query_token, token):
                        _set_session_cookie(response, token, request)
                    return response
            return await call_next(request)

        if path.startswith("/api"):
            return JSONResponse(status_code=401, content={"status": "error", "message": "未授权"})

        if _wants_html(request) or path == "/":
            # 带上原目标（已剔除令牌键，避免把错误令牌塞进 next 参数），登录后直接回去
            target = _url_without_token_query(request)
            return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=302)

        return JSONResponse(status_code=401, content={"status": "error", "message": "未授权"})
