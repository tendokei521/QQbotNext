"""网关的连接方向分流测试：正向连出 / 反向监听。

覆盖用户实际会遇到的三件事：
1. 反向账号「连接」= 起监听（不是去连某个地址），状态显示等待接入；
2. OneBot 实现接进来后，与正向走同一条消息循环（登录信息、事件派发都能跑）；
3. 对端断开/用户断开时，监听与状态各自收敛正确（不能留下占端口或永远 connected）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import time

import pytest
import websockets

from app.infrastructure.cache import Cache
from app.infrastructure.onebot.gateway import OneBotGateway


class _Settings:
    ws_ping_interval = 30
    ws_ping_timeout = 10
    ws_connect_timeout = 3


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _wait(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


def _gateway() -> OneBotGateway:
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    # 登录信息走假响应，避免依赖真实 OneBot 实现
    async def _login(conn):
        conn.bot_id = 10001
        conn.login_info = {"user_id": 10001, "nickname": "小明"}
        return True

    async def _groups(conn):
        return True

    gw._get_login_info = _login
    gw._get_login_groups = _groups
    return gw


class _FakeOneBot:
    """最小 OneBot 实现：连上反向 WS 后冒充 API 服务端回一次 echo。"""

    def __init__(self, url: str, token: str = "", self_id: str = "10001") -> None:
        self.url = url
        self.token = token
        self.self_id = self_id

    async def run(self, *, send_message: bool = False, stop: asyncio.Event | None = None) -> None:
        headers = {"X-Self-ID": self.self_id, "X-Client-Role": "Universal"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        async with websockets.connect(self.url, additional_headers=headers) as ws:
            if send_message:
                await ws.send(json.dumps({
                    "post_type": "message", "message_type": "private",
                    "user_id": 100, "self_id": int(self.self_id), "time": int(time.time()),
                    "message": [{"type": "text", "data": {"text": "hi"}}],
                }))
            while stop is not None and not stop.is_set():
                try:
                    await asyncio.wait_for(ws.recv(), timeout=0.05)
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    return


async def test_reverse_connect_starts_listener_not_outbound():
    """反向账号「连接」= 起监听：状态是 connecting（等待接入），不是 error。"""
    port = _free_port()
    gw = _gateway()
    await gw.add_bot(
        f"ws://127.0.0.1:{port}/onebot/v11/ws", None, auto_connect=False,
        account_id="acc0", conn_mode="reverse",
    )
    assert await gw.connect_bot("acc0") is True
    try:
        info = gw.get_bots_info()[0]
        assert info["conn_mode"] == "reverse"
        assert info["listening"] is True
        assert info["status"] == "connecting"
        assert info["last_error"] is None
        # 监听地址正确落在配置的端口上（能被主动连接 → 说明真的在监听）
        async with websockets.connect(f"ws://127.0.0.1:{port}/onebot/v11/ws") as ws:
            await ws.send("ping")
    finally:
        await gw.shutdown()


async def test_reverse_client_attaches_and_dispatches_message():
    """OneBot 实现接入后：状态 connected，事件走与正向相同的派发链。"""
    port = _free_port()
    gw = _gateway()
    seen: list = []

    async def handler(event):
        seen.append(event)

    gw.dispatch_handler = handler
    await gw.add_bot(f"ws://127.0.0.1:{port}/onebot/v11/ws", None, account_id="acc0", conn_mode="reverse")
    assert await gw.connect_bot("acc0") is True

    client = _FakeOneBot(f"ws://127.0.0.1:{port}/onebot/v11/ws")
    task = asyncio.create_task(client.run(send_message=True))
    try:
        assert await _wait(lambda: seen), "反向连接接入后必须能派发事件"
        assert await _wait(lambda: gw.connections["acc0"].status == "connected")
        info = gw.get_bots_info()[0]
        assert info["status"] == "connected"
        assert info["bot_id"] == 10001, "接入后应主动拉一次登录信息"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await gw.shutdown()


async def test_reverse_api_request_reaches_client_and_response_returns():
    """反向连接同样是「双向」的：框架发出的 API 请求走同一条 socket 并拿到响应。"""
    port = _free_port()
    gw = _gateway()
    await gw.add_bot(f"ws://127.0.0.1:{port}/onebot/v11/ws", None, account_id="acc0", conn_mode="reverse")
    assert await gw.connect_bot("acc0") is True

    received: list[dict] = []

    async def serve_api():
        headers = {"X-Self-ID": "10001", "X-Client-Role": "Universal"}
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/onebot/v11/ws", additional_headers=headers
        ) as ws:
            async for raw in ws:
                payload = json.loads(raw)
                received.append(payload)
                await ws.send(json.dumps({
                    "status": "ok", "retcode": 0, "echo": payload.get("echo"),
                    "data": {"message_id": 42},
                }))

    api_task = asyncio.create_task(serve_api())
    try:
        conn = gw.connections["acc0"]
        assert await _wait(lambda: conn.websocket is not None)
        resp = await conn.send_private_msg(100, "hi")
        assert resp and resp.get("status") == "ok"
        assert resp["data"]["message_id"] == 42
        assert any(p.get("action") == "send_private_msg" for p in received)
    finally:
        api_task.cancel()
        await gw.shutdown()


async def test_reverse_disconnect_stops_listener():
    """用户断开反向账号 → 监听停止（端口释放），状态 disconnected。"""
    port = _free_port()
    gw = _gateway()
    await gw.add_bot(f"ws://127.0.0.1:{port}/onebot/v11/ws", None, account_id="acc0", conn_mode="reverse")
    assert await gw.connect_bot("acc0") is True
    await gw.disconnect_bot("acc0", manual=True)
    info = gw.get_bots_info()[0]
    assert info["listening"] is False
    assert info["status"] == "disconnected"
    # 端口已释放：同地址可以再次监听
    assert await gw.connect_bot("acc0") is True
    assert gw.get_bots_info()[0]["listening"] is True
    await gw.shutdown()


async def test_invalid_reverse_address_surfaces_error():
    """反向地址缺端口 → 立刻报错（不让用户对着"没有监听"干等）。"""
    gw = _gateway()
    await gw.add_bot("ws://127.0.0.1/onebot/v11/ws", None, account_id="acc0", conn_mode="reverse")
    assert await gw.connect_bot("acc0") is False
    info = gw.get_bots_info()[0]
    assert info["status"] == "error"
    assert "反向监听失败" in (info["last_error"] or "")
    await gw.shutdown()


async def test_reconcile_switches_between_forward_and_reverse():
    """配置里改连接方向 → 网关按新模式重建（旧监听必须停掉）。"""
    port = _free_port()
    gw = _gateway()
    await gw.add_bot(f"ws://127.0.0.1:{port}/onebot/v11/ws", None, account_id="acc0", conn_mode="reverse")
    assert await gw.connect_bot("acc0") is True
    assert gw.get_bots_info()[0]["listening"] is True

    # 切到正向：监听器应被停掉、状态回到断开（等待连出）
    await gw._reconcile([{
        "id": "acc0",
        "name": "主号",
        "ws_url": f"ws://127.0.0.1:{port}/onebot/v11/ws",
        "auto_connect": False,
        "conn_mode": "forward",
    }])
    info = gw.get_bots_info()[0]
    assert info["conn_mode"] == "forward"
    assert info["listening"] is False
    await gw.shutdown()


async def test_peer_close_keeps_listener_and_waits_again():
    """对端掉线（NapCat 重启）：监听不关，状态回「等待接入」而不是红色错误。"""

    port = _free_port()
    gw = _gateway()
    await gw.add_bot(f"ws://127.0.0.1:{port}/onebot/v11/ws", None, account_id="acc0", conn_mode="reverse")
    assert await gw.connect_bot("acc0") is True

    stop = asyncio.Event()
    client = _FakeOneBot(f"ws://127.0.0.1:{port}/onebot/v11/ws")
    task = asyncio.create_task(client.run(stop=stop))
    try:
        assert await _wait(lambda: gw.connections["acc0"].status == "connected")
        stop.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await _wait(lambda: gw.connections["acc0"].status == "connecting"), \
            "对端断开后监听仍在 → 状态应回到等待接入"
        info = gw.get_bots_info()[0]
        assert info["listening"] is True
    finally:
        await gw.shutdown()


async def test_start_all_listens_for_reverse_auto_connect_accounts():
    """开机自启：auto_connect 的反向账号由 start_all 直接起监听，不必人工点连接。"""
    port = _free_port()
    gw = _gateway()
    await gw.start_all([{
        "id": "acc0",
        "name": "主号",
        "ws_url": f"ws://127.0.0.1:{port}/onebot/v11/ws",
        "auto_connect": True,
        "conn_mode": "reverse",
    }])
    try:
        assert await _wait(lambda: gw.get_bots_info()[0]["listening"] is True)
        assert gw.get_bots_info()[0]["status"] == "connecting"
    finally:
        await gw.shutdown()
    assert gw.get_bots_info()[0]["listening"] is False, "shutdown 必须释放监听端口"


async def test_forward_account_never_opens_listener():
    """正向账号不该顺手起监听：模式分流不能把两种连接混在一起。"""
    gw = _gateway()
    await gw.add_bot("ws://127.0.0.1:1/", None, account_id="acc0", conn_mode="forward")
    try:
        # 连不上（端口 1 无人监听）→ 走正向失败路径，不产生监听器
        assert await gw.connect_bot("acc0") is False
        info = gw.get_bots_info()[0]
        assert info["conn_mode"] == "forward"
        assert info["listening"] is False
        assert info["status"] == "error"
        assert gw.reverse_servers == {}
    finally:
        await gw.shutdown()


async def test_second_client_replaces_first_without_duplicate_dispatch():
    """对端重启时旧连接可能还没超时：新接入必须接管，事件不能派发两次。

    回归点：两条读循环并存时，群里每条消息都会被处理两遍（重复回复）；且旧循环
    收尾时若拿 ``conn.websocket`` 当自己，会把刚接上的新连接关掉。
    """
    port = _free_port()
    gw = _gateway()
    dispatched: list = []

    async def handler(event):
        dispatched.append(event)

    gw.dispatch_handler = handler
    await gw.add_bot(f"ws://127.0.0.1:{port}/onebot/v11/ws", None, account_id="acc0", conn_mode="reverse")
    assert await gw.connect_bot("acc0") is True

    def _msg(text: str) -> str:
        return json.dumps({
            "post_type": "message", "message_type": "private",
            "user_id": 100, "self_id": 10001, "time": int(time.time()),
            "message": [{"type": "text", "data": {"text": text}}],
        })

    first_stop = asyncio.Event()
    first = asyncio.create_task(_FakeOneBot(
        f"ws://127.0.0.1:{port}/onebot/v11/ws").run(stop=first_stop))
    try:
        assert await _wait(lambda: gw.connections["acc0"].status == "connected")
        old_ws = gw.connections["acc0"].websocket

        # 第二条连接（模拟 NapCat 重启时的短暂双连）
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/onebot/v11/ws",
            additional_headers={"X-Self-ID": "10001", "X-Client-Role": "Universal"},
        ) as second:
            assert await _wait(lambda: gw.connections["acc0"].websocket not in (None, old_ws)), \
                "新接入必须接管该账号的连接"
            # 旧连接已被关掉：它的读循环随之结束，不会与新连接并存
            assert await _wait(lambda: old_ws.close_code is not None)

            await second.send(_msg("once"))
            assert await _wait(lambda: len(dispatched) == 1)
            await asyncio.sleep(0.2)
            assert len(dispatched) == 1, f"同一条消息只能派发一次，实际 {len(dispatched)}"
            assert gw.connections["acc0"].status == "connected"
            assert gw.get_bots_info()[0]["listening"] is True
    finally:
        first_stop.set()
        first.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await first
        await gw.shutdown()
