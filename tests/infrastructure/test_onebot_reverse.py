"""反向 WebSocket 监听器测试（真实 socket，不打桩传输层）。

反向 WS 的价值就是「框架监听、OneBot 实现连进来」，鉴权与路径匹配错一位用户
就只能看到「连不上」。因此这里用真 websockets 客户端跑完整握手：
路径/token 正确才接管，错误则明确拒绝（关闭码可查），停止监听后不再接受新连接。
"""

from __future__ import annotations

import asyncio
import socket
import time

import pytest
import websockets

from app.infrastructure.onebot.endpoint import EndpointError
from app.infrastructure.onebot.reverse import (
    CLOSE_NOT_FOUND,
    CLOSE_UNAUTHORIZED,
    ReverseWsServer,
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _NullLog:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, msg: str, *_a, **_k) -> None:
        self.warnings.append(str(msg))

    def info(self, msg: str, *_a, **_k) -> None:
        pass

    debug = info


async def _wait(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


def _url(port: int, path: str = "/onebot/v11/ws", token: str = "") -> str:
    url = f"ws://127.0.0.1:{port}{path}"
    return f"{url}?access_token={token}" if token else url


async def test_requires_explicit_port_at_construction():
    """反向地址缺端口 = 用户无从在 OneBot 实现里填目标，构造期就要报错。"""
    with pytest.raises(EndpointError):
        ReverseWsServer(url="ws://127.0.0.1/onebot/v11/ws", handler=lambda ws, c: None)


async def test_valid_client_is_handed_to_gateway_handler():
    port = _free_port()
    seen: list = []

    async def handler(ws, client):
        seen.append(client)
        # 保持连接直到对端关闭（真实网关在这里跑读循环）
        async for _ in ws:
            pass

    server = ReverseWsServer(url=_url(port, token="s3cret"), handler=handler, log=_NullLog())
    await server.start()
    assert server.listening is True
    try:
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/onebot/v11/ws?access_token=s3cret",
            additional_headers={"X-Self-ID": "10001", "X-Client-Role": "Universal"},
        ) as ws:
            assert await _wait(lambda: bool(seen))
            assert seen[0].self_id == "10001"
            assert seen[0].role == "Universal"
            assert seen[0].universal is True
            assert seen[0].token == "s3cret"
            await ws.send("ping")
    finally:
        await server.stop()
    assert server.listening is False


async def test_bearer_header_token_also_accepted():
    """OneBot 实现的标准写法是 Authorization: Bearer，不能只认 query。"""
    port = _free_port()
    seen: list = []

    async def handler(ws, client):
        seen.append(client)
        async for _ in ws:
            pass

    server = ReverseWsServer(url=_url(port, token="tok"), handler=handler, log=_NullLog())
    await server.start()
    try:
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/onebot/v11/ws",
            additional_headers={"Authorization": "Bearer tok"},
        ) as ws:
            assert await _wait(lambda: bool(seen))
            await ws.send("ping")
    finally:
        await server.stop()


async def test_wrong_token_is_rejected():
    port = _free_port()
    seen: list = []

    async def handler(ws, client):  # pragma: no cover —— 不该被调用
        seen.append(client)

    server = ReverseWsServer(url=_url(port, token="right"), handler=handler, log=_NullLog())
    await server.start()
    try:
        with pytest.raises(websockets.exceptions.ConnectionClosed) as exc:
            async with websockets.connect(f"ws://127.0.0.1:{port}/onebot/v11/ws?access_token=wrong") as ws:
                await ws.recv()
        assert exc.value.rcvd is not None and exc.value.rcvd.code == CLOSE_UNAUTHORIZED
    finally:
        await server.stop()
    assert not seen, "token 不匹配时绝不能把连接交给网关"
    assert server.last_error and "access_token" in server.last_error


async def test_wrong_path_is_rejected():
    port = _free_port()
    seen: list = []

    async def handler(ws, client):  # pragma: no cover
        seen.append(client)

    server = ReverseWsServer(url=_url(port), handler=handler, log=_NullLog())
    await server.start()
    try:
        with pytest.raises(websockets.exceptions.ConnectionClosed) as exc:
            async with websockets.connect(f"ws://127.0.0.1:{port}/other/path") as ws:
                await ws.recv()
        assert exc.value.rcvd is not None and exc.value.rcvd.code == CLOSE_NOT_FOUND
    finally:
        await server.stop()
    assert not seen


async def test_trailing_slash_on_path_is_tolerated():
    """NapCat 里填 ``/onebot/v11/ws/`` 与 ``/onebot/v11/ws`` 是同一条路。"""
    port = _free_port()
    seen: list = []

    async def handler(ws, client):
        seen.append(client)
        async for _ in ws:
            pass

    server = ReverseWsServer(url=_url(port), handler=handler, log=_NullLog())
    await server.start()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/onebot/v11/ws/") as ws:
            assert await _wait(lambda: bool(seen))
            await ws.send("ping")
    finally:
        await server.stop()


async def test_no_token_configured_accepts_any_client():
    """账号未设 token 时不鉴权（与正向行为一致：地址即凭据）。"""
    port = _free_port()
    seen: list = []

    async def handler(ws, client):
        seen.append(client)
        async for _ in ws:
            pass

    server = ReverseWsServer(url=_url(port), handler=handler, log=_NullLog())
    await server.start()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/onebot/v11/ws?access_token=whatever") as ws:
            assert await _wait(lambda: bool(seen))
            await ws.send("ping")
    finally:
        await server.stop()


async def test_stop_releases_port():
    """停止监听 = 端口释放（换地址重连时不能因为旧监听没关而报占用）。"""
    port = _free_port()

    async def handler(ws, client):  # pragma: no cover
        async for _ in ws:
            pass

    server = ReverseWsServer(url=_url(port), handler=handler, log=_NullLog())
    await server.start()
    await server.stop()

    again = ReverseWsServer(url=_url(port), handler=handler, log=_NullLog())
    await again.start()   # 端口已释放 → 可以重新绑定
    await again.stop()


async def test_separated_role_connection_is_accepted_with_warning():
    """Event/Api 分离模式的连接照样接受，但要留一条明确告警（否则只有超时无解释）。"""
    port = _free_port()
    seen: list = []
    log = _NullLog()

    async def handler(ws, client):
        seen.append(client)
        async for _ in ws:
            pass

    server = ReverseWsServer(url=_url(port), handler=handler, log=log)
    await server.start()
    try:
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/onebot/v11/ws",
            additional_headers={"X-Client-Role": "Event"},
        ) as ws:
            assert await _wait(lambda: bool(seen))
            await ws.send("ping")
    finally:
        await server.stop()
    assert seen[0].universal is False
    assert any("通用" in w for w in log.warnings)
