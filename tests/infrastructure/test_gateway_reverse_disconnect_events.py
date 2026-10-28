"""反向账号「用户主动断开」的状态事件序列回归测试。

真实故障形态：``disconnect_bot`` 先停监听器 → 监听器关闭接入连接 → 读循环收尾
判定「反向监听已不在」→ 广播 ``error``；随后 ``disconnect_bot`` 再广播
``disconnected``。管理端会看到「已连接 → 红色错误 → 已断开」的错误闪烁，
连接日志里也多出一条并不存在的故障。
修复：主动断开期间置 ``conn._disconnecting``，读循环收尾/僵尸扫描不再广播 error，
状态收敛由 ``disconnect_bot`` 统一负责。
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time

import websockets

from app.core.event_bus import BotLifecycleEvent, event_bus
from app.infrastructure.cache import Cache
from app.infrastructure.onebot.client import BotConnection
from app.infrastructure.onebot.gateway import OneBotGateway


class _Settings:
    ws_ping_interval = 30
    ws_ping_timeout = 10
    ws_connect_timeout = 3


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _gateway() -> OneBotGateway:
    gw = OneBotGateway(settings=_Settings(), cache=Cache())

    async def _login(conn):
        conn.bot_id = 10001
        conn.login_info = {"user_id": 10001, "nickname": "小明"}
        return True

    async def _groups(conn):
        return True

    gw._get_login_info = _login
    gw._get_login_groups = _groups
    return gw


async def _wait(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


async def test_reverse_manual_disconnect_never_broadcasts_error():
    """主动断开的事件序列只能是 connected → disconnected，不得夹一个 error。"""
    port = _free_port()
    url = f"ws://127.0.0.1:{port}/onebot/v11/ws"
    gw = _gateway()
    states: list[tuple[str, str]] = []

    async def on_event(event):
        if event.account_id == "acc0":
            states.append((event.state, event.detail))

    event_bus.subscribe(BotLifecycleEvent, on_event)
    client_task = None
    try:
        await gw.add_bot(url, None, account_id="acc0", conn_mode="reverse")
        assert await gw.connect_bot("acc0") is True

        async def _hold_open():
            headers = {"X-Self-ID": "10001", "X-Client-Role": "Universal"}
            async with websockets.connect(url, additional_headers=headers) as ws:
                while True:
                    await ws.recv()

        client_task = asyncio.create_task(_hold_open())
        assert await _wait(lambda: gw.connections["acc0"].status == "connected"), "对端应已接入"
        assert await _wait(lambda: any(state == "connected" for state, _ in states)), \
            f"接入后应有 connected 事件，实际 {states}"

        await gw.disconnect_bot("acc0", manual=True)
    finally:
        if client_task is not None:
            client_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await client_task
        event_bus.unsubscribe(BotLifecycleEvent, on_event)
        await gw.shutdown()

    kinds = [state for state, _ in states]
    assert "error" not in kinds, f"主动断开不得广播 error，实际序列 {states}"
    assert kinds[-1] == "disconnected", f"最后一个状态应是 disconnected，实际 {states}"
    assert kinds.index("connected") < kinds.index("disconnected"), f"事件顺序错误: {states}"


async def test_mark_offline_is_suppressed_while_disconnecting():
    """断开进行中：任何掉线判定都不得改状态/广播 error（交给 disconnect_bot）。"""
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    conn = BotConnection()
    conn.account_id = "acc0"
    conn.account_name = "主号"
    conn.status = "connected"
    conn._disconnecting = True
    gw.connections = {"acc0": conn}

    await gw._mark_offline(conn, "监听已停止")

    assert conn.status == "connected", "主动断开期间不得把状态改成 error"
    assert conn._disconnecting is True


async def test_peer_close_still_broadcasts_waiting_not_error():
    """反例保护：非主动断开（对端掉线）仍按原语义收敛为「等待接入」。"""
    port = _free_port()
    url = f"ws://127.0.0.1:{port}/onebot/v11/ws"
    gw = _gateway()
    await gw.add_bot(url, None, account_id="acc0", conn_mode="reverse")
    assert await gw.connect_bot("acc0") is True

    headers = {"X-Self-ID": "10001", "X-Client-Role": "Universal"}
    try:
        async with websockets.connect(url, additional_headers=headers):
            assert await _wait(lambda: gw.connections["acc0"].status == "connected")
        # 连接正常关闭（非用户主动断开）→ 监听仍在 → 等待接入
        assert await _wait(lambda: gw.connections["acc0"].status == "connecting"), \
            "对端掉线且监听仍应在 → 状态应回到 connecting"
        info = gw.get_bots_info()[0]
        assert info["listening"] is True
        assert "error" != (info["status"])
    finally:
        await gw.shutdown()
