"""账号「断开」语义回归测试。

两个实际现象（远端日志：12 次「断开连接 → 连接成功 → 登录成功」，全部无重连失败、无配置更新）：

1. ``disconnect_bot`` 只把 status 置成 disconnected，既不记录「用户要它停」，
   也不影响 auto_connect；而 ``_supervise`` 每 10s 把「status=disconnected 且
   auto_connect=True」当成掉线重新连接 → 用户点了「断开」，最多 10s 后账号又连回来。
   注意不能在断开时直接把 ``auto_connect`` 置 False：``_reconcile`` 每轮都从
   持久化配置把它同步回来，所以必须用一个独立于配置的运行期抑制标志。

2. ``connect_bot`` 在 await WS 握手期间不校验是否已被取消，此时
   ``disconnect_bot`` 照样能跑完，握手成功后会把 status/websocket 覆盖回 connected
   → 「断开取消不掉在途连接」。
"""

import asyncio
import contextlib

from app.infrastructure.cache import Cache
from app.infrastructure.onebot.client import BotConnection
from app.infrastructure.onebot.gateway import OneBotGateway
from app.services.bot_service import BotService

WS_URL = "ws://127.0.0.1:1"


class _Settings:
    ws_ping_interval = 30
    ws_ping_timeout = 10
    ws_connect_timeout = 3


class _Dispatcher:
    async def dispatch(self, event) -> None:
        pass


class _ConfigService:
    """最小配置服务：只提供 BotService.__init__ 需要的两个读取口。"""

    def __init__(self, bots: list[dict]) -> None:
        self._bots = bots

    def get_bots(self) -> list[dict]:
        return [dict(b) for b in self._bots]

    def get_bot_accounts(self) -> dict:
        return {}


class _StubWs:
    """可 async for 迭代的假 ws：不产出消息，直到被取消。"""

    def __init__(self) -> None:
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Event().wait()

    async def close(self) -> None:
        self.closed = True


def _gateway(bots: list[dict]):
    """按配置构造网关：connection 0 + 同一份配置的 provider。"""
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    cfg = bots[0]
    conn = BotConnection(ws_url=cfg["ws_url"], auto_connect=cfg["auto_connect"])
    conn.index = 0
    gw.connections = {0: conn}
    gw.bots_provider = lambda: [dict(b) for b in bots]
    return gw, conn


def _service(gw, bots: list[dict]) -> BotService:
    """真实 BotService（WebUI 断/连走的也是它）。"""
    return BotService(
        gateway=gw,
        registry=object(),
        config_service=_ConfigService(bots),
        dispatcher=_Dispatcher(),
    )


async def _supervise_ticks(gw, ticks: int, monkeypatch) -> None:
    """跑恰好 ``ticks`` 轮 _supervise 循环体（把 10s 间隔换成一次让步）。"""
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


def _fake_connect_recorder(gw, conn, calls: list):
    """替换 connect_bot：只记录被调用，不做真实握手。"""

    async def fake_connect(index: int) -> bool:
        calls.append(index)
        conn.status = "connected"
        return True

    gw.connect_bot = fake_connect
    return fake_connect


async def test_manual_disconnect_survives_supervise(monkeypatch):
    """回归：WebUI 点「断开」后，监督循环不得把这个 auto_connect 账号连回来。"""
    bots = [{"ws_url": WS_URL, "owner_id": None, "auto_connect": True}]
    gw, conn = _gateway(bots)
    svc = _service(gw, bots)
    connects: list[int] = []
    _fake_connect_recorder(gw, conn, connects)

    await svc.disconnect(0)  # = POST /api/bots/0/disconnect
    assert conn.status == "disconnected"
    assert conn.suppress_auto_reconnect is True, "手动断开应置位抑制标志"

    await _supervise_ticks(gw, 3, monkeypatch)
    assert connects == [], f"手动断开后监督循环不得重连，实际调用 connect_bot {connects} 次"


async def test_unexpected_drop_still_auto_reconnects(monkeypatch):
    """反例保护：非手动（意外掉线）的连接仍必须被监督循环自动恢复。"""
    bots = [{"ws_url": WS_URL, "owner_id": None, "auto_connect": True}]
    gw, conn = _gateway(bots)
    connects: list[int] = []
    _fake_connect_recorder(gw, conn, connects)

    conn.status = "error"  # _bot_server 异常路径，不经过 disconnect_bot
    await _supervise_ticks(gw, 1, monkeypatch)
    assert connects == [0], "意外掉线的 auto_connect 账号应被自动重连"


async def test_explicit_connect_clears_manual_disconnect(monkeypatch):
    """用户点「连接」后恢复自动重连语义（抑制只在手动断开到显式连接之间有效）。"""
    bots = [{"ws_url": WS_URL, "owner_id": None, "auto_connect": True}]
    gw, conn = _gateway(bots)
    svc = _service(gw, bots)
    ws = _StubWs()

    async def fake_connect_ws(url: str):
        return ws

    monkeypatch.setattr(gw, "_connect_websocket", fake_connect_ws)

    await svc.disconnect(0)
    assert conn.suppress_auto_reconnect is True

    assert await svc.connect(0) is True
    assert conn.status == "connected"
    assert conn.suppress_auto_reconnect is False, "显式连接应解除手动断开抑制"

    await gw.disconnect_bot(0)
    for task in list(gw.bot_server_tasks.values()):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_reopen_auto_connect_switch_clears_suppression():
    """配置页把「自动连接」从关注到开 → 视为明确要它在线，解除抑制。"""
    bots = [{"ws_url": WS_URL, "owner_id": None, "auto_connect": True}]
    gw, conn = _gateway(bots)
    svc = _service(gw, bots)

    await svc.disconnect(0)
    assert conn.suppress_auto_reconnect is True

    bots[0]["auto_connect"] = False  # 关掉开关：只改配置，不解除抑制
    await gw.reconcile()
    assert conn.auto_connect is False
    assert conn.suppress_auto_reconnect is True

    bots[0]["auto_connect"] = True  # 再打开：解除抑制，恢复自动重连
    await gw.reconcile()
    assert conn.auto_connect is True
    assert conn.suppress_auto_reconnect is False


async def test_disconnect_cancels_inflight_connect(monkeypatch):
    """回归：握手期间点「断开」，在途连接必须被取消，不得覆盖断开状态。"""
    bots = [{"ws_url": WS_URL, "owner_id": None, "auto_connect": False}]
    gw, conn = _gateway(bots)
    started = asyncio.Event()
    release = asyncio.Event()
    ws = _StubWs()

    async def slow_connect(url: str):
        started.set()
        await release.wait()
        return ws

    monkeypatch.setattr(gw, "_connect_websocket", slow_connect)

    task = asyncio.create_task(gw.connect_bot(0))
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert conn.status == "connecting"

        await gw.disconnect_bot(0)  # 正在握手时点「断开」
        assert conn.status == "disconnected"

        release.set()
        assert await asyncio.wait_for(task, 2) is False, "被取消的握手应返回 False"
        assert conn.status == "disconnected", "握手结果不得覆盖断开状态"
        assert conn.websocket is None
        assert ws.closed, "被作废的握手结果必须关闭"
        assert not gw.bot_server_tasks, "不得为被取消的连接留下 bot_server 任务"
    finally:
        release.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        for t in list(gw.bot_server_tasks.values()):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
