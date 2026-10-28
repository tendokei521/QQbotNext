"""反向账号「连接中点断开」竞态回归测试。

真实缺陷：``connect_bot`` 的正向分支有 epoch 校验（握手期间断开 → 结果作废），
反向分支没有：起监听可能很慢，期间用户点「断开」，监听随后照样起来、状态被改回
``connecting``，而 ``disconnect_bot`` 已经收尾结束——表现为「点了断开却还在监听」。
修复：反向分支同样记 epoch，``_listen_reverse`` 返回后校验 epoch / 账号仍存在，
不一致则停掉监听并恢复 disconnected，返回 False。
"""

from __future__ import annotations

import asyncio
import socket

from app.infrastructure.cache import Cache
from app.infrastructure.onebot.gateway import CONNECT_CONCURRENCY, OneBotGateway


class _Settings:
    ws_ping_interval = 30
    ws_ping_timeout = 10
    ws_connect_timeout = 3


class _NullLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, msg: str, *_a, **_k) -> None:
        self.warnings.append(str(msg))

    info = warning
    debug = warning
    error = warning
    exception = warning

    def prefix(self, *_a, **_k):
        return self

    def add_info(self, *_a, **_k):
        return self


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _gateway() -> OneBotGateway:
    gw = OneBotGateway(settings=_Settings(), cache=Cache(), logger_=_NullLogger())

    async def _login(conn):
        conn.bot_id = 10001
        conn.login_info = {"user_id": 10001, "nickname": "小明"}
        return True

    async def _groups(conn):
        return True

    gw._get_login_info = _login
    gw._get_login_groups = _groups
    return gw


async def test_disconnect_during_reverse_listen_start_is_cancelled():
    """慢启动监听期间点断开：最终不得留下监听，状态必须是 disconnected。"""
    port = _free_port()
    url = f"ws://127.0.0.1:{port}/onebot/v11/ws"
    gw = _gateway()
    await gw.add_bot(url, None, account_id="acc0", conn_mode="reverse")

    started = asyncio.Event()
    release = asyncio.Event()
    real_listen = gw.reverse_link.ensure_listening

    async def slow_listen(account_id: str, conn) -> bool:
        started.set()
        await release.wait()
        return await real_listen(account_id, conn)

    gw._listen_reverse = slow_listen  # 模拟「绑定端口/解析地址很慢」
    connect_task = asyncio.create_task(gw.connect_bot("acc0"))
    try:
        await asyncio.wait_for(started.wait(), 2)
        await gw.disconnect_bot("acc0", manual=True)  # 慢启动期间用户点断开
        release.set()

        assert await asyncio.wait_for(connect_task, 3) is False, "被断开作废的连接必须返回 False"
        info = gw.get_bots_info()[0]
        assert info["listening"] is False, "断开后监听不得继续存在"
        assert gw.connections["acc0"].status == "disconnected", "状态不得被慢启动改回 connecting"
        # 端口确实释放：同地址可以再次监听
        assert await gw.connect_bot("acc0") is True
        assert gw.get_bots_info()[0]["listening"] is True
    finally:
        release.set()
        connect_task.cancel()
        try:
            await connect_task
        except asyncio.CancelledError:
            pass
        await gw.shutdown()


async def test_disconnect_while_waiting_for_connect_semaphore_skips_listen():
    """连监听都还没开始时就被断开（在并发闸门排队）：不得再起监听。"""
    port = _free_port()
    url = f"ws://127.0.0.1:{port}/onebot/v11/ws"
    gw = _gateway()
    await gw.add_bot(url, None, account_id="acc0", conn_mode="reverse")

    release = asyncio.Event()

    async def slow_listen(account_id: str, conn) -> bool:  # pragma: no cover —— 不该被调用
        release.set()
        raise AssertionError("被作废的连接不得开始监听")

    gw._listen_reverse = slow_listen
    # 占满并发闸门（CONNECT_CONCURRENCY 个许可），让 connect_bot 卡在排队
    for _ in range(CONNECT_CONCURRENCY):
        await gw._connect_semaphore.acquire()
    task = asyncio.create_task(gw.connect_bot("acc0"))
    try:
        await asyncio.sleep(0.02)
        await gw.disconnect_bot("acc0", manual=True)
        for _ in range(CONNECT_CONCURRENCY):
            gw._connect_semaphore.release()
        assert await asyncio.wait_for(task, 2) is False
        assert gw.get_bots_info()[0]["listening"] is False
        assert gw.connections["acc0"].status == "disconnected"
    finally:
        release.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await gw.shutdown()
