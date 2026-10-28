"""WebSocket 连接管理与 /ws/logs 端点。

## 为什么每连接一条发送队列

广播原先按连接顺序 `await send_text`：任何一个慢客户端（浏览器标签页挂起、
手机切后台）都会把 `broadcast()` 卡住，而它的调用方是 `gateway._notify_status`、
配置保存接口等——一个慢客户端就能拖慢整条状态推送链路。

现在：每连接一个容量有限的发送队列 + 独立 sender 任务，`broadcast()` 只
`put_nowait` 后立刻返回。队列满（客户端确实消费不动）或单条发送超时
（``SEND_TIMEOUT_SECONDS``）→ 断开该连接并记一条 warning，绝不让它拖累别人。
"""

from __future__ import annotations

import asyncio
import contextlib
import json

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketDisconnect as _WSDisconnect

from app.core.logger import webui_logger

#: 单条消息发送超时（秒）：超过就认为该客户端已失联，断开而不是无限等
SEND_TIMEOUT_SECONDS = 2.0
#: 每连接积压上限：慢客户端最多积压这么多条，超过即断开（宁可断线也不拖累广播）
CLIENT_QUEUE_SIZE = 64


class _ClientSender:
    """单个 WS 连接的发送队列与 sender 任务（慢客户端只堵自己）。"""

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=CLIENT_QUEUE_SIZE)
        self.task: asyncio.Task | None = None


class ConnectionManager:
    """管理 WebUI 的 WebSocket 连接，支持非阻塞广播。"""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []
        self._senders: dict[int, _ClientSender] = {}
        #: 因过慢 / 发送失败被断开的连接数（诊断用）
        self.dropped = 0

    # ==================== 连接生命周期 ====================

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        sender = _ClientSender(websocket)
        sender.task = asyncio.create_task(
            self._sender_loop(sender), name=f"ws-sender:{id(websocket)}"
        )
        self._senders[id(websocket)] = sender
        self.active_connections.append(websocket)

    def _remove(self, websocket: WebSocket) -> None:
        self._senders.pop(id(websocket), None)
        with contextlib.suppress(ValueError):
            self.active_connections.remove(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        """从广播表摘掉连接并停止它的 sender 任务（幂等）。"""
        sender = self._senders.get(id(websocket))
        self._remove(websocket)
        # sender 自己在 finally 里会调 `_remove`（不能 cancel 自己）
        if sender is not None and sender.task is not None:
            if not sender.task.done() and sender.task is not asyncio.current_task():
                sender.task.cancel()

    # ==================== 发送 ====================

    async def _sender_loop(self, sender: _ClientSender) -> None:
        """把该连接队列里的消息串行发出去；超时/失败只影响这一条连接。"""
        websocket = sender.websocket
        try:
            while True:
                message = await sender.queue.get()
                await asyncio.wait_for(
                    websocket.send_text(message), timeout=SEND_TIMEOUT_SECONDS
                )
        except asyncio.TimeoutError:
            self.dropped += 1
            webui_logger.warning(
                f"[WebUI] WS 客户端发送超时（{SEND_TIMEOUT_SECONDS}s），已断开"
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 客户端断开/写失败都只影响自己
            self.dropped += 1
            webui_logger.debug(f"[WebUI] WS 发送失败，已断开: {e}")
        finally:
            self._remove(websocket)
            with contextlib.suppress(Exception):
                await websocket.close()

    def _enqueue(self, sender: _ClientSender, message: str) -> bool:
        try:
            sender.queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            return False

    def _drop_slow(self, websocket: WebSocket, reason: str) -> None:
        """断开消费不动队列的客户端：宁可它掉线，也不能拖慢广播。"""
        self.dropped += 1
        webui_logger.warning(f"[WebUI] WS 客户端过慢（{reason}），已断开并停止向其推送")
        self.disconnect(websocket)

    async def broadcast(self, message: str) -> None:
        """广播给所有连接（只入队，不等待任何客户端）。"""
        for connection in list(self.active_connections):
            sender = self._senders.get(id(connection))
            if sender is None:
                continue
            if not self._enqueue(sender, message):
                self._drop_slow(connection, "发送队列已满")

    async def send(self, websocket: WebSocket, message: str) -> bool:
        """往单个连接投递一条消息；连接已不在管理表时返回 False（调用方应退出）。"""
        sender = self._senders.get(id(websocket))
        if sender is None:
            return False
        if not self._enqueue(sender, message):
            self._drop_slow(websocket, "日志推送队列已满")
            return False
        return True


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
                # 文件 IO 放线程池：原来每秒一次同步 open/read 直接堵事件循环
                logs = await log_service.get_recent_logs_async(max_lines, levels, source=source)
                # 统一走连接队列（不再直接 send_text）：与广播共用一套慢客户端保护
                if not await manager.send(websocket, json.dumps(logs)):
                    break
                await asyncio.sleep(1)
        except (WebSocketDisconnect, _WSDisconnect, asyncio.CancelledError):
            pass
        except Exception as e:
            webui_logger.error(f"[WebUI] WS 日志推送异常: {e}")
        finally:
            manager.disconnect(websocket)

    return router
