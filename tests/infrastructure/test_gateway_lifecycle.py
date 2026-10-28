"""网关监督循环健壮性回归测试。

真实故障形态：
1. ``start_all`` 用 ``gather`` 连接 auto_connect 账号且未加 ``return_exceptions``，
   监督任务又在 gather **之后**才创建——任一账号 connect 抛异常就整体失败，
   本次运行剩余时间里「自动重连」完全不存在。
2. ``_supervise`` 逐账号串行 ``await connect_bot``：N 个号同时掉线时第 N 个要等
   前 N-1 个握手完成，最坏排队数十秒。
修复：监督任务先创建、gather 收异常；重连批量并发，并用 Semaphore 限制并发握手数。
"""

from __future__ import annotations

import asyncio

from app.infrastructure.cache import Cache
from app.infrastructure.onebot.gateway import CONNECT_CONCURRENCY, OneBotGateway


class _Settings:
    ws_ping_interval = 30
    ws_ping_timeout = 10
    ws_connect_timeout = 3


class _HangingWs:
    """已连上的假连接：读循环挂起，直到被取消。"""

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Event().wait()

    async def send(self, data: str) -> None:
        pass

    async def close(self) -> None:
        pass


async def _run_supervise_ticks(gw: OneBotGateway, ticks: int, monkeypatch) -> None:
    """跑恰好 ticks 轮 _supervise 循环体（10s 间隔换成一次让步；其它 sleep 原样）。"""
    real_sleep = asyncio.sleep
    seen = 0

    async def fake_sleep(delay, *args, **kwargs):
        nonlocal seen
        if delay == 10:
            seen += 1
            if seen > ticks:
                raise asyncio.CancelledError
            await real_sleep(0)
            return
        await real_sleep(delay, *args, **kwargs)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    try:
        await gw._supervise()
    except asyncio.CancelledError:
        pass
    finally:
        monkeypatch.setattr(asyncio, "sleep", real_sleep)


def _bots_config(ids: list[str]) -> list[dict]:
    return [
        {"id": aid, "name": aid, "ws_url": "ws://127.0.0.1:1", "auto_connect": True}
        for aid in ids
    ]


async def test_start_all_keeps_supervise_alive_when_connect_raises():
    """单个账号 connect 抛异常不得让 start_all 失败，监督循环必须已启动。"""
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    calls: list[str] = []

    async def fake_connect(account_id: str) -> bool:
        calls.append(account_id)
        if account_id == "a":
            raise RuntimeError("握手炸了")
        return True

    gw.connect_bot = fake_connect
    try:
        await gw.start_all(_bots_config(["a", "b"]))  # 不得抛出
        assert sorted(calls) == ["a", "b"], "一个账号异常不得阻断其它账号启动连接"
        assert gw._supervise_task is not None and not gw._supervise_task.done(), \
            "监督循环必须在 connect 失败后仍然存活（否则本次运行永不自动重连）"
    finally:
        await gw.shutdown()


async def test_supervise_reconnects_due_accounts_concurrently(monkeypatch):
    """多个账号同时掉线：重连必须并发发起，不得逐个排队。"""
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    ids = ["a", "b", "c"]
    for aid in ids:
        await gw.add_bot("ws://127.0.0.1:1", None, account_id=aid, name=aid)
        conn = gw.connections[aid]
        conn.auto_connect = True
        conn.status = "error"
        conn.reconnect_attempts = 0
    gw.bots_provider = lambda: _bots_config(ids)

    calls: list[str] = []
    concurrent = 0
    peak = 0

    async def fake_connect(account_id: str) -> bool:
        nonlocal concurrent, peak
        calls.append(account_id)
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.05)  # 模拟握手耗时
        concurrent -= 1
        gw.connections[account_id].status = "connected"
        return True

    gw.connect_bot = fake_connect
    try:
        await _run_supervise_ticks(gw, 1, monkeypatch)
    finally:
        await gw.shutdown()

    assert sorted(calls) == ids, f"所有到期账号都必须被重连，实际 {calls}"
    assert peak >= 2, f"重连必须并发发起（旧实现串行 peak==1），实际峰值 {peak}"


async def test_supervise_survives_single_reconnect_exception(monkeypatch):
    """一个账号重连抛异常：其它账号照常重连，监督循环不退出。"""
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    ids = ["a", "b"]
    for aid in ids:
        await gw.add_bot("ws://127.0.0.1:1", None, account_id=aid, name=aid)
        conn = gw.connections[aid]
        conn.auto_connect = True
        conn.status = "error"
    gw.bots_provider = lambda: _bots_config(ids)

    calls: list[str] = []

    async def fake_connect(account_id: str) -> bool:
        calls.append(account_id)
        if account_id == "a":
            raise RuntimeError("连接炸了")
        return True

    gw.connect_bot = fake_connect
    try:
        await _run_supervise_ticks(gw, 1, monkeypatch)
    finally:
        await gw.shutdown()

    assert sorted(calls) == ids, f"单账号异常不得中断其它账号重连，实际 {calls}"


async def test_connect_semaphore_limits_parallel_handshakes():
    """并发握手上限：5 个账号同时连接，实际同时进行的握手不超过 CONNECT_CONCURRENCY。"""
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    ids = [f"acc{i}" for i in range(5)]
    for aid in ids:
        await gw.add_bot("ws://127.0.0.1:1", None, account_id=aid, name=aid)

    concurrent = 0
    peak = 0

    async def fake_connect_ws(url: str):
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.05)
        concurrent -= 1
        return _HangingWs()

    gw._connect_websocket = fake_connect_ws
    try:
        results = await asyncio.gather(*(gw.connect_bot(aid) for aid in ids))
        assert all(results), "所有账号都应连接成功"
        assert peak == CONNECT_CONCURRENCY, \
            f"同时握手数应被限制为 {CONNECT_CONCURRENCY}，实际峰值 {peak}"
    finally:
        await gw.shutdown()
