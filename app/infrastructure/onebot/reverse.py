"""反向 WebSocket 监听器（OneBot v11 反向 WS / "框架当服务端"）。

## 它解决什么

正向 WS 是「框架连出去」：用户在 NapCat 里开一个 ws 服务端，把地址填进账号配置。
反向 WS 相反：**框架监听**，OneBot 实现主动连进来。很多部署场景只有这条路可用
（NapCat 在容器/另一台机器里、只允许出站连接），所以两种方向都要支持。

## 协议要点（OneBot v11）

接入方（NapCat / Lagrange / LLOneBot …）握手时会带：

- 路径：用户在自己实现里配置的目标地址路径，默认约定 ``/onebot/v11/ws``；
- ``Authorization: Bearer <access_token>`` 或 ``?access_token=<token>``（鉴权）；
- ``X-Self-ID``：接入的 QQ 号（仅用于日志/排查，账号归属由「哪个监听器」决定）；
- ``X-Client-Role``：``Universal`` / ``Event`` / ``Api``。

本模块只做四件事：**监听 → 校验路径与 token → 交给网关 → 等连接结束**。
消息收发（API 请求/响应配对、事件解码、派发）全部复用网关既有的
``_bot_server`` 循环——反向连接与正向连接在消息语义上完全一样，只是 TCP 方向相反。

## 兼容 websockets 两个大版本

本仓库允许 ``websockets>=12``：12/13 是 legacy 实现（handler 收 ``(ws, path)``、
头部在 ``ws.request_headers``），14+ 换成 asyncio 实现（handler 收 ``(ws)``、
请求对象在 ``ws.request``）。这里的三个小工具函数把差异收在一处，避免调用方
到处写 ``getattr``。
"""

from __future__ import annotations

import contextlib
import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import websockets

from app.core.logger import logger
from app.infrastructure.onebot.endpoint import Endpoint, parse_endpoint

#: 鉴权失败 / 路径不匹配时的关闭码（4401 与 WebUI 的未授权码保持一致，便于排查）
CLOSE_UNAUTHORIZED = 4401
CLOSE_NOT_FOUND = 4404
CLOSE_REPLACED = 4000

#: 反向连接回调：收到一个已完成校验的接入连接后由网关接管，返回值在连接结束时才返回
ReverseHandler = Callable[[Any, "ReverseClient"], Awaitable[None]]


@dataclass
class ReverseClient:
    """一条已通过校验的反向接入连接（握手信息，供日志与排查使用）。"""

    path: str
    token: str
    self_id: str = ""
    role: str = ""
    peer: str = ""

    @property
    def universal(self) -> bool:
        """是否通用模式（同一连接既上报事件又承接 API 请求）。"""
        return self.role.strip().lower() in ("", "universal")


def _request_of(ws: Any) -> Any:
    """取请求对象（14+ 在 ``ws.request``，12/13 直接是 ``ws`` 自带 header 属性）。"""
    return getattr(ws, "request", None)


def _headers_of(ws: Any) -> Any:
    """取握手头部；两个版本的属性名不同，统一成「有 .get 的对象」。"""
    request = _request_of(ws)
    if request is not None and hasattr(request, "headers"):
        return request.headers
    return getattr(ws, "request_headers", {}) or {}


def _path_of(ws: Any) -> str:
    """取握手路径（含 query）。"""
    request = _request_of(ws)
    path = getattr(request, "path", None) if request is not None else None
    if path is None:
        path = getattr(ws, "path", "") or ""
    return str(path)


def _header(headers: Any, name: str) -> str:
    try:
        value = headers.get(name)
    except Exception:  # noqa: BLE001 —— 头部对象形态有版本差异，取不到就当没有
        return ""
    return str(value or "")


def _split_path_and_query(path: str) -> tuple[str, dict[str, str]]:
    """把 ``/onebot/v11/ws?access_token=x`` 拆成 (路径, query 字典)。"""
    raw, _, query = (path or "").partition("?")
    params: dict[str, str] = {}
    for item in query.split("&"):
        if not item or "=" not in item:
            continue
        key, _, value = item.partition("=")
        params[key] = value
    return raw or "/", params


def _normalize_path(path: str) -> str:
    """路径比较口径：忽略结尾斜杠（``/ws`` 与 ``/ws/`` 视为同一路径）。"""
    cleaned = (path or "/").strip()
    if len(cleaned) > 1:
        cleaned = cleaned.rstrip("/")
    return cleaned or "/"


def _bearer_token(headers: Any) -> str:
    """从 ``Authorization: Bearer <token>`` 里取 token（OneBot 实现的标准写法）。"""
    auth = _header(headers, "authorization").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _token_matches(expected: str, given: str) -> bool:
    """常量时间比较，避免按字符差异泄露（token 是唯一的口令）。"""
    return hmac.compare_digest(expected.encode(), given.encode())


class ReverseWsServer:
    """单个账号的反向 WS 监听器。

    ``handler`` 由网关提供：拿到 websocket 后负责这条连接的**完整生命周期**
    （启动读循环并等待结束）。监听器在 handler 返回后才释放握手协程——否则
    websockets 会在 handler 返回瞬间关闭连接，网关的读循环就再也收不到消息。
    """

    def __init__(
        self,
        *,
        url: str,
        handler: ReverseHandler,
        log: Any = None,
        ping_interval: float | None = None,
        ping_timeout: float | None = None,
    ) -> None:
        self.url = url
        # parse_endpoint 在构造期就校验地址：配置错误要在「点连接」时立刻报出来，
        # 而不是等到用户去 OneBot 实现里点连接才发现没人监听。
        self.endpoint: Endpoint = parse_endpoint(url, require_port=True)
        self.handler = handler
        self.log = log or logger
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self._server: Any = None
        self._active: set[Any] = set()
        self.last_error: str | None = None
        self.client: ReverseClient | None = None

    # ==================== 生命周期 ====================
    @property
    def listening(self) -> bool:
        server = self._server
        if server is None:
            return False
        closed = getattr(server, "closed", None)
        return not bool(closed)

    @property
    def listen_url(self) -> str:
        """便于日志/前端提示的可读地址（token 打码）。"""
        host, port = self.endpoint.bindable()
        path = self.endpoint.path
        return f"{self.endpoint.scheme}://{host}:{port}{path}"

    async def start(self) -> None:
        """开始监听；端口占用等错误原样抛出（由网关转成 last_error 展示）。"""
        if self.listening:
            return
        host, port = self.endpoint.bindable()
        kwargs: dict[str, Any] = {"ping_interval": self.ping_interval, "ping_timeout": self.ping_timeout}
        try:
            self._server = await websockets.serve(self._handle, host, port, **kwargs)
        except TypeError:
            # 老版本 serve 不接受 None 的 ping 参数 → 退回默认心跳
            kwargs = {k: v for k, v in kwargs.items() if v is not None}
            self._server = await websockets.serve(self._handle, host, port, **kwargs)
        self.last_error = None

    async def stop(self) -> None:
        """停止监听并断开已接入的连接。"""
        server, self._server = self._server, None
        if server is not None:
            with contextlib.suppress(Exception):
                server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()
        for ws in list(self._active):
            with contextlib.suppress(Exception):
                await ws.close(code=CLOSE_REPLACED, reason="监听已停止")
        self._active.clear()
        self.client = None

    # ==================== 握手处理 ====================
    async def _handle(self, *args: Any) -> None:
        """websockets 回调：12/13 传 (ws, path)，14+ 只传 (ws)。"""
        ws = args[0] if args else None
        if ws is None:
            return
        path, query = _split_path_and_query(_path_of(ws))
        headers = _headers_of(ws)
        client = ReverseClient(
            path=_normalize_path(path),
            token=_bearer_token(headers) or query.get("access_token", ""),
            self_id=_header(headers, "x-self-id"),
            role=_header(headers, "x-client-role"),
            peer=str(getattr(ws, "remote_address", "") or ""),
        )

        if _normalize_path(self.endpoint.path) != client.path:
            await self._reject(ws, CLOSE_NOT_FOUND, f"路径不匹配（应为 {self.endpoint.path}）", client)
            return
        expected = self.endpoint.token
        if expected and not _token_matches(expected, client.token):
            await self._reject(ws, CLOSE_UNAUTHORIZED, "access_token 校验失败", client)
            return

        self.client = client
        self.last_error = None
        self._active.add(ws)
        if not client.universal:
            self.log.warning(
                f"[OneBot] 反向连接声明为 {client.role or '?'} 角色："
                "本框架按通用连接收发 API，分离模式可能收不到 API 响应"
            )
        try:
            await self.handler(ws, client)
        finally:
            self._active.discard(ws)
            if self.client is client:
                self.client = None

    async def _reject(self, ws: Any, code: int, reason: str, client: ReverseClient) -> None:
        self.last_error = reason
        self.log.warning(
            f"[OneBot] 反向连接被拒绝（{reason}）: 来自 {client.peer or '?'} "
            f"path={client.path} self_id={client.self_id or '?'}"
        )
        with contextlib.suppress(Exception):
            await ws.close(code=code, reason=reason)
