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
from urllib.parse import unquote

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
    """把 ``/onebot/v11/ws?access_token=x`` 拆成 (路径, query 字典)。

    **必须 URL 解码**：OneBot 实现的 query 会按 URL 编码传输（中文 token 变成
    ``%E4%B8%AD%E6%96%87``），裸切会把百分号编码原样当 token → 永远校验失败。
    路径同样解码，才能与配置里的路径做同口径比较。
    """
    raw, _, query = (path or "").partition("?")
    params: dict[str, str] = {}
    for item in query.split("&"):
        if not item or "=" not in item:
            continue
        key, _, value = item.partition("=")
        params[unquote(key)] = unquote(value)
    return unquote(raw) or "/", params


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


def _server_loop(server: Any) -> Any:
    """取 websockets Server 所属的事件循环。

    websockets 16 的 ``Server`` 用 ``get_loop()``（实例属性 ``.loop`` 并非公开
    接口），12/13 则只有 ``.loop``。这里优先用 ``get_loop()``，取不到再退回属性。
    """
    get_loop = getattr(server, "get_loop", None)
    if callable(get_loop):
        try:
            return get_loop()
        except Exception as e:  # noqa: BLE001 —— 取不到就按「无法判断」处理（走正常关闭路径）
            logger.debug(f"[OneBot] 读取反向监听器事件循环失败（按未关闭处理）: {e}")
            return None
    return getattr(server, "loop", None)


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
        """监听是否**真的**在服务中（而不是「曾经 start 过」）。

        websockets 16 的 ``Server`` 没有 ``closed`` 属性（只有 ``is_serving()`` /
        ``closed_waiter``），旧写法 ``getattr(server, "closed", None)`` 恒为 None →
        ``listening`` 永远 True：监听意外停止后不会重建，诊断台/监督循环也会误判。
        旧版（12/13）没有 ``is_serving``，退回读 ``closed``。
        """
        server = self._server
        if server is None:
            return False
        is_serving = getattr(server, "is_serving", None)
        if callable(is_serving):
            try:
                return bool(is_serving())
            except Exception as e:  # noqa: BLE001 —— 探测失败按「未监听」处理（会触发重建）
                self.log.debug(f"[OneBot] 反向监听状态探测失败（按未监听处理）: {e}")
                return False
        return not bool(getattr(server, "closed", None))

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
        """停止监听并断开已接入的连接。

        **必须**在监听器创建时的事件循环上调用。跨循环调用（测试里每个 TestClient
        请求各自一个事件循环、热重载残留对象）时不再显式 ``close()``：websockets 16
        的 ``Server.close()`` 内部要 ``create_task(self._close(...))``，在已关闭的
        循环上会抛 RuntimeError 并留下未 await 的 ``_close`` 协程（pytest 里的
        ``coroutine 'Server._close' was never awaited`` RuntimeWarning）。此时传输
        已随循环销毁，直接丢弃引用即可。
        """
        server, self._server = self._server, None
        loop = _server_loop(server) if server is not None else None
        loop_alive = loop is None or not loop.is_closed()
        if server is not None and loop_alive:
            try:
                server.close()
            except Exception as e:  # noqa: BLE001 —— 关停失败不阻断后续清理，但必须留痕
                self.log.debug(f"[OneBot] 关闭反向监听器失败（继续清理接入连接）: {e}")
            try:
                await server.wait_closed()
            except Exception as e:  # noqa: BLE001
                self.log.debug(f"[OneBot] 等待反向监听器关闭失败（继续清理接入连接）: {e}")
        elif server is not None:
            self.log.debug("[OneBot] 反向监听器所属事件循环已关闭，跳过显式关闭（随循环销毁）")
        if loop_alive:
            for ws in list(self._active):
                try:
                    await ws.close(code=CLOSE_REPLACED, reason="监听已停止")
                except Exception as e:  # noqa: BLE001 —— 连接可能已被对端关闭
                    self.log.debug(f"[OneBot] 关闭反向接入连接失败（已忽略）: {e}")
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
