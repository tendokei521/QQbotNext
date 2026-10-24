"""「服务端正常关闭（1000）不得变成僵尸连接」回归测试。

真实故障（远端 SnowLuma 日志与 app 日志逐秒对照，2026-09-27）：

- 23:48:26 SnowLuma 运行时重启，三个 session 同时 `session closed`，
  并以 **1000（正常关闭）** 关掉所有客户端连接；23:48:33/34 就全部
  `listening` 恢复（远端只停了约 7 秒）。
- ``websockets`` 的 ``Connection.__aiter__`` 会把 ``ConnectionClosedOK``
  直接吞掉、让 ``async for`` **正常退出**（不抛异常）；而 ``_bot_server``
  当时只有 ``except`` 分支处理掉线 →
  不记日志、不清 ``conn.websocket``、不改 ``status``、不放行在途请求。
- 结果：``status`` 永远停在 ``connected``，监督循环判定「在线」不重连，
  客户端僵死 6 小时以上（00:00 打卡 18 次全失败、06:00 仍在报
  ``received 1000 (OK) normal; then sent 1000 (OK) normal``），
  而同一时刻远端早已恢复监听；那条在途合并转发则干等满 60s 超时。

本文件用**真实 websockets 服务端**复现「服务端 1000 关闭」这一路
（现有测试用的假 ws 走 ``StopAsyncIteration``，恰好覆盖不到这个 bug）。
"""

import asyncio
import contextlib
import time

import websockets

from app.infrastructure.cache import Cache
from app.infrastructure.onebot.client import BotConnection
from app.infrastructure.onebot.gateway import OneBotGateway


class _Settings:
    ws_ping_interval = 30
    ws_ping_timeout = 10
    ws_connect_timeout = 3


async def _serve(handler):
    """起一个本地 ws 服务端，返回 (url, server)。"""
    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = next(iter(server.sockets)).getsockname()[1]
    return f"ws://127.0.0.1:{port}/", server


async def _close_server(server) -> None:
    server.close()
    await server.wait_closed()


def _gateway(url: str):
    """单账号网关：connection 0 + 同一份配置的 provider（auto_connect=True）。"""
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    conn = BotConnection(ws_url=url, auto_connect=True)
    conn.account_id = "main"
    conn.account_name = "主号"
    gw.connections = {"main": conn}
    gw.bots_provider = lambda: [
        {"id": "main", "name": "主号", "ws_url": url, "owner_id": None, "auto_connect": True},
    ]
    return gw, conn


def _fake_connect_recorder(gw, conn, calls: list):
    """替换 connect_bot：只记录被调用，不做真实握手。"""

    async def fake_connect(account_id: str) -> bool:
        calls.append(account_id)
        conn.status = "connected"
        return True

    gw.connect_bot = fake_connect
    return fake_connect


async def _supervise_ticks(gw, ticks: int, monkeypatch) -> None:
    """跑恰好 ticks 轮 _supervise 循环体（把 10s 间隔换成一次让步）。"""
    real_sleep = asyncio.sleep
    seen = 0

    async def fake_sleep(_delay):
        nonlocal seen
        seen += 1
        if seen > ticks:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    try:
        with contextlib.suppress(asyncio.CancelledError):
            await gw._supervise()
    finally:
        monkeypatch.setattr(asyncio, "sleep", real_sleep)


async def test_normal_close_marks_offline_and_reconnects(monkeypatch):
    """回归：服务端以 1000 关闭 → 必须转入掉线态，并被监督循环自动连回。"""
    released = asyncio.Event()

    async def handler(ws):
        await released.wait()
        await ws.close(1000, "session closed")

    url, server = await _serve(handler)
    try:
        gw, conn = _gateway(url)
        assert await gw.connect_bot("main") is True
        task = gw.bot_server_tasks["main"]  # 连接还活着 → 收尾逻辑必须留下可查痕迹

        released.set()
        for _ in range(100):
            if task.done():
                break
            await asyncio.sleep(0.05)
        assert task.done(), "服务端关闭后接收循环必须结束"

        # 旧实现：async for 被 ConnectionClosedOK 静默结束 → 状态仍 connected、
        # websocket 仍是已关闭对象、任务表已空但没人重连。
        assert conn.status == "error", "正常关闭后必须转入掉线态，否则监督循环永不重连"
        assert conn.websocket is None, "已关闭的 ws 必须清空，否则每次发送都抛 ConnectionClosed"
        assert "1000" in (conn.last_error or ""), f"last_error 应带 close_code: {conn.last_error!r}"
        assert not gw.bot_server_tasks

        calls: list[int] = []
        _fake_connect_recorder(gw, conn, calls)
        await _supervise_ticks(gw, 2, monkeypatch)
        assert calls == ["main"], f"掉线的 auto_connect 账号必须被自动重连，实际 {calls}"
    finally:
        released.set()
        await _close_server(server)


async def test_normal_close_fails_inflight_request_fast():
    """回归：连接收尾必须让在途请求立刻失败，而不是干等满 API 超时（真实案例 60s）。"""

    async def handler(ws):
        await ws.recv()  # 等一个 API 请求进来，然后不回复、直接 1000 关闭
        await ws.close(1000, "session closed")

    url, server = await _serve(handler)
    gw = None
    try:
        gw, conn = _gateway(url)
        assert await gw.connect_bot("main") is True

        t0 = time.perf_counter()
        resp = await asyncio.wait_for(
            conn.call_api("send_group_forward_msg", {"group_id": 1, "messages": []}, timeout=60), 5
        )
        elapsed = time.perf_counter() - t0

        assert resp is None
        assert elapsed < 5, f"在途请求应随连接收尾立刻失败，实际等了 {elapsed:.2f}s"
        assert conn.status == "error"
    finally:
        if gw is not None:
            for task in list(gw.bot_server_tasks.values()):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            for conn_ in gw.connections.values():
                if conn_.websocket is not None:
                    await conn_.websocket.close()
        await _close_server(server)


class _OpenWs:
    """健康连接桩：未收到关闭帧。"""

    close_code = None
    close_reason = None

    async def close(self) -> None:
        pass


class _ClosedWs:
    """已收到关闭帧但没被收尾的 ws 桩（漏网路径）。"""

    close_code = 1000
    close_reason = "session closed"

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def test_connection_gone_detection():
    """僵尸判定：ws 缺失 / 已带 close_code / 接收任务已亡 任一命中；健康连接不得误判。"""
    gw, conn = _gateway("ws://127.0.0.1:1")

    async def _forever():
        await asyncio.Event().wait()

    task = asyncio.create_task(_forever())
    gw.bot_server_tasks = {"main": task}  # 生产代码统一用 account_id 作键
    conn.websocket = _OpenWs()
    try:
        assert gw._connection_gone("main", conn) is False, "ws 未关 + 任务在跑 = 健康，不得误判"
        conn.websocket = _ClosedWs()
        assert gw._connection_gone("main", conn) is True, "已带 close_code 的连接必须判为已消失"
        conn.websocket = _OpenWs()
        gw.bot_server_tasks.clear()
        assert gw._connection_gone("main", conn) is True, "接收任务已亡必须判为已消失"
        conn.websocket = None
        assert gw._connection_gone("main", conn) is True, "ws 缺失必须判为已消失"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_supervise_sweeps_zombie_connection(monkeypatch):
    """兜底：状态仍标 connected 但 ws 已被对端关闭 → 按掉线处理并触发重连。"""
    gw, conn = _gateway("ws://127.0.0.1:1")
    conn.status = "connected"
    conn.websocket = _ClosedWs()

    calls: list[int] = []
    _fake_connect_recorder(gw, conn, calls)
    await _supervise_ticks(gw, 1, monkeypatch)

    assert calls == ["main"], f"僵尸连接必须被识别为掉线并自动重连，实际 {calls}"
    assert conn.last_error, "必须留下掉线原因，便于排查"
    assert conn.websocket is None
