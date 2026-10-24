"""WebSocket 连接管理与 /ws/logs 端点。"""

from __future__ import annotations

import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketDisconnect as _WSDisconnect

from app.core.logger import webui_logger


class ConnectionManager:
    """管理 WebUI 的 WebSocket 连接，支持广播。"""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []
        self._send_locks: dict[int, asyncio.Lock] = {}

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._send_locks[id(websocket)] = asyncio.Lock()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self._send_locks.pop(id(websocket), None)

    async def broadcast(self, message: str) -> None:
        disconnected = []
        for connection in list(self.active_connections):
            lock = self._send_locks.get(id(connection))
            try:
                if lock is not None:
                    async with lock:  # 同一连接串行发送，避免并发 send_text 竞态
                        await connection.send_text(message)
                else:
                    await connection.send_text(message)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)


manager = ConnectionManager()


def build_ws_router():
    """构造 WebSocket 路由（依赖注入服务）。"""
    from fastapi import APIRouter

    router = APIRouter()

    @router.websocket("/ws/logs")
    async def websocket_logs(websocket: WebSocket):
        from app.application.log_service import LogService
        from app.core.settings import Settings
        from app.infrastructure.config.config_service import ConfigService
        from app.webui.auth import SESSION_COOKIE, is_authenticated, query_token_from

        container = websocket.app.state.container
        settings = container.get(Settings)

        # 鉴权：HTTP 中间件对 websocket scope 不生效，须在端点内显式校验。
        # 浏览器登录后靠会话 cookie 通过（同源 WS 握手浏览器会自动带 cookie）；
        # 脚本/插件页仍可用 Bearer 头或 ?token= / ?accesstoken=，三条通道并存。
        token = (settings.webui_token or "").strip()
        if token and not is_authenticated(
            expected_token=token,
            authorization=websocket.headers.get("authorization", ""),
            query_token=query_token_from(websocket.query_params),
            session_cookie=websocket.cookies.get(SESSION_COOKIE),
        ):
            await websocket.close(code=4401, reason="未授权")
            return

        config_service = container.get(ConfigService)
        log_service = container.get(LogService)

        await manager.connect(websocket)
        try:
            while True:
                config = config_service.get_webui_config()
                levels = config.get("logs", {}).get("visible_levels", ["info", "warning", "error"])
                max_lines = config.get("logs", {}).get("max_lines", 50)
                mode = websocket.query_params.get("mode", "")
                if mode not in ("simple", "raw"):
                    mode = "raw" if config.get("logs", {}).get("show_raw_logs", False) else "simple"
                source = "user" if mode == "simple" else "debug"
                logs = log_service.get_recent_logs(max_lines, levels, source=source)
                await websocket.send_text(json.dumps(logs))
                await asyncio.sleep(1)
        except (WebSocketDisconnect, _WSDisconnect, asyncio.CancelledError):
            pass
        except Exception as e:
            webui_logger.error(f"[WebUI] WS 日志推送异常: {e}")
        finally:
            manager.disconnect(websocket)

    return router
