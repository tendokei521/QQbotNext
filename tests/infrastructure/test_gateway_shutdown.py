"""网关关停/断开的 best-effort 与超时回归测试。

真实问题：
- ``_bot_server`` 收尾 ``await worker`` 无超时：worker 若卡在不可取消的处理里，
  整个关停被无限挂住；
- ``disconnect_bot`` 里 ``await task`` 只 suppress CancelledError，读循环抛其它异常
  会跳过「关 ws / 复位状态 / 广播」；
- ``shutdown`` 逐账号 await 无兜底：一个账号收尾失败，后面的账号就不关了。
"""

from __future__ import annotations

import asyncio
import json
import time

from app.infrastructure.cache import Cache
from app.infrastructure.onebot import gateway as gateway_module
from app.infrastructure.onebot.client import BotConnection
from app.infrastructure.onebot.gateway import OneBotGateway


class _Settings:
    ws_ping_interval = 30
    ws_ping_timeout = 10
    ws_connect_timeout = 3


class _CaptureLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.infos: list[str] = []

    def warning(self, msg: str, *_a, **_k) -> None:
        self.warnings.append(str(msg))

    def info(self, msg: str, *_a, **_k) -> None:
        self.infos.append(str(msg))

    def debug(self, *_a, **_k) -> None:
        pass

    def error(self, *_a, **_k) -> None:
        pass

    def exception(self, *_a, **_k) -> None:
        pass

    def prefix(self, *_a, **_k):
        return self

    def add_info(self, *_a, **_k):
        return self


def _gateway(log: _CaptureLogger) -> OneBotGateway:
    gw = OneBotGateway(settings=_Settings(), cache=Cache(), logger_=log)

    async def _noop(conn):
        return False

    gw._get_login_info = _noop
    gw._get_login_groups = _noop
    return gw


def _conn(ws=None) -> BotConnection:
    conn = BotConnection(websocket=ws)
    conn.account_id = "main"
    conn.account_name = "主号"
    conn.status = "connected"
    return conn


class _FakeWs:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def _wait(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


async def test_disconnect_times_out_stubborn_read_loop(monkeypatch):
    """读循环吞掉取消也不肯退出：disconnect 必须按超时返回并完成收尾，而不是挂住。"""
    monkeypatch.setattr(gateway_module, "GATEWAY_SHUTDOWN_TIMEOUT", 0.05)
    log = _CaptureLogger()
    gw = _gateway(log)
    ws = _FakeWs()
    conn = _conn(ws)
    gw.connections = {"main": conn}

    stop = asyncio.Event()
    started = asyncio.Event()

    async def stubborn() -> None:
        started.set()
        while not stop.is_set():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                continue  # 故意忽略取消

    task = asyncio.create_task(stubborn(), name="stubborn-read-loop")
    gw.bot_server_tasks["main"] = task
    try:
        assert await _wait(started.is_set), "读循环应先进入挂起状态"
        t0 = time.perf_counter()
        await gw.disconnect_bot("main", manual=True)
        elapsed = time.perf_counter() - t0

        assert elapsed < 1.0, f"disconnect 被卡死任务拖住了 {elapsed:.2f}s"
        assert conn.status == "disconnected"
        assert ws.closed is True, "超时也要保证 ws 被关闭"
        assert not gw.bot_server_tasks
        assert any("未退出" in warning for warning in log.warnings), log.warnings
    finally:
        stop.set()
        task.cancel()
        await asyncio.wait({task}, timeout=1)


async def test_disconnect_finalizes_when_read_loop_raised(monkeypatch):
    """读循环以异常结束：仍要关 ws、复位状态、广播 disconnected（旧实现会跳过）。"""
    log = _CaptureLogger()
    gw = _gateway(log)
    ws = _FakeWs()
    conn = _conn(ws)
    gw.connections = {"main": conn}

    async def boom() -> None:
        raise RuntimeError("读循环炸了")

    task = asyncio.create_task(boom(), name="crashed-read-loop")
    await asyncio.sleep(0)  # 让它先以异常结束
    gw.bot_server_tasks["main"] = task

    await gw.disconnect_bot("main", manual=True)

    assert conn.status == "disconnected"
    assert ws.closed is True
    assert any("读循环炸了" in warning for warning in log.warnings), log.warnings


async def test_disconnect_finalizes_when_stopping_listener_raises():
    """停止反向监听抛异常：收尾（关 ws / 状态 / 广播 / 清标记）仍必须执行。"""
    log = _CaptureLogger()
    gw = _gateway(log)
    ws = _FakeWs()
    conn = _conn(ws)
    conn._disconnecting = True
    gw.connections = {"main": conn}

    async def boom_stop(account_id: str) -> None:
        raise RuntimeError("监听关闭失败")

    gw.reverse_link.stop = boom_stop  # type: ignore[method-assign]

    await gw.disconnect_bot("main", manual=True)

    assert conn.status == "disconnected"
    assert ws.closed is True
    assert conn._disconnecting is False
    assert any("监听关闭失败" in warning for warning in log.warnings), log.warnings


async def test_shutdown_is_best_effort_across_accounts():
    """一个账号断开失败，不得阻断其它账号的关停。"""
    log = _CaptureLogger()
    gw = _gateway(log)
    gw.connections = {"a": _conn(), "b": _conn()}
    calls: list[str] = []

    async def fake_disconnect(account_id: str, *, manual: bool = False) -> None:
        calls.append(account_id)
        if account_id == "a":
            raise RuntimeError("第一个账号炸了")

    gw.disconnect_bot = fake_disconnect  # type: ignore[method-assign]

    await gw.shutdown()

    assert sorted(calls) == ["a", "b"], f"单个账号失败后仍要关其它账号，实际 {calls}"
    assert any("关停账号「a」" in warning for warning in log.warnings), log.warnings


class _HangingWs:
    """投递一条消息后挂起（供 _bot_server 收尾测试）。"""

    def __init__(self) -> None:
        self._sent = False
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if not self._sent:
            self._sent = True
            return json.dumps({
                "post_type": "message", "message_type": "private",
                "user_id": 100, "self_id": 1, "time": int(time.time()),
                "message": [{"type": "text", "data": {"text": "hi"}}],
            })
        await asyncio.Event().wait()

    async def send(self, data: str) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


async def test_bot_server_cancel_does_not_hang_on_stubborn_worker(monkeypatch):
    """worker 卡在不可取消的事件处理里：_bot_server 取消后必须按超时返回。"""
    monkeypatch.setattr(gateway_module, "GATEWAY_SHUTDOWN_TIMEOUT", 0.05)
    log = _CaptureLogger()
    gw = _gateway(log)
    conn = _conn(_HangingWs())
    gw.connections = {"main": conn}

    started = asyncio.Event()
    stop = asyncio.Event()

    async def stubborn_handler(event) -> None:
        started.set()
        while not stop.is_set():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                continue  # 故意忽略取消

    gw.dispatch_handler = stubborn_handler
    task = asyncio.create_task(gw._bot_server("main"), name="bot_server:main")
    try:
        assert await _wait(started.is_set), "worker 应先开始处理事件"
        task.cancel()
        _done, pending = await asyncio.wait({task}, timeout=2)
        assert not pending, "_bot_server 取消后不得无限等待 worker"
        assert any("worker" in warning and "未退出" in warning for warning in log.warnings), log.warnings
    finally:
        stop.set()
        for pending_task in list(asyncio.all_tasks()):
            if pending_task.get_name().startswith("dispatch_worker:main"):
                pending_task.cancel()
        await asyncio.sleep(0)
