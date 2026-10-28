"""登录信息拉取失败不得让连接永久半上线（回归测试）。

真实故障形态：``get_login_info`` 失败一次就放弃，而「已尝试取登录信息」的标记
在成败之前就置位 → 之后永不重试。WebUI 显示已连接，但 ``runtime.online`` 恒为
False，定时任务 / 主动消息 / 模块 ``on_online`` 全部不工作。
修复：失败按 2/4/8s 退避重试，成功后才置位 ``login_retrieved``；下一条正常事件
到达时若上一轮重试已耗尽，会重新拉起一轮。
"""

from __future__ import annotations

import asyncio
import json
import time

from app.infrastructure.cache import Cache
from app.infrastructure.onebot import gateway_lifecycle as lifecycle_module
from app.infrastructure.onebot.client import BotConnection
from app.infrastructure.onebot.gateway import OneBotGateway


class _Settings:
    ws_ping_interval = 30
    ws_ping_timeout = 10
    ws_connect_timeout = 3


class _NullLogger:
    def warning(self, *_a, **_k):
        pass

    info = warning
    debug = warning
    error = warning
    exception = warning

    def prefix(self, *_a, **_k):
        return self

    def add_info(self, *_a, **_k):
        return self


class _OneMessageWs:
    """投递一条（或多条）消息后保持连接不结束，供测试观察后台任务。"""

    def __init__(self, messages: int = 1) -> None:
        self.sent: list[dict] = []
        self._remaining = messages

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self._remaining > 0:
            self._remaining -= 1
            return json.dumps({
                "post_type": "message", "message_type": "private",
                "user_id": 100, "self_id": 1, "time": int(time.time()),
                "message": [{"type": "text", "data": {"text": "hi"}}],
            })
        await asyncio.Event().wait()

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def close(self) -> None:
        pass


def _conn(ws) -> BotConnection:
    conn = BotConnection(websocket=ws)
    conn.account_id = "main"
    conn.account_name = "主号"
    conn.status = "connected"
    return conn


async def _wait(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


async def test_login_retry_succeeds_on_third_attempt(monkeypatch):
    """前两次 get_login_info 失败、第三次成功 → 必须最终登录成功并置位已取过。"""
    monkeypatch.setattr(lifecycle_module, "LOGIN_RETRY_DELAYS", (0.01, 0.01, 0.01))
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    conn = _conn(_OneMessageWs())
    gw.connections = {"main": conn}

    calls = {"info": 0}

    async def flaky_login_info():
        calls["info"] += 1
        if calls["info"] < 3:
            return {"status": "failed", "retcode": 1, "message": "服务未就绪"}
        return {"status": "ok", "data": {"user_id": 987654, "nickname": "小明"}}

    async def groups():
        return {"status": "ok", "data": []}

    conn.get_login_info = flaky_login_info
    conn.get_group_list = groups

    ok = await gw._login_with_retry(conn, _NullLogger())

    assert ok is True, "第三次成功必须返回 True"
    assert calls["info"] == 3, f"应恰好重试到第 3 次，实际 {calls['info']} 次"
    assert conn.bot_id == 987654
    assert conn.login_info["nickname"] == "小明"
    assert conn.login_retrieved is True, "只有成功后才允许置位「已取过」"


async def test_login_failure_keeps_retrieved_false_and_gives_up(monkeypatch):
    """全部尝试失败：不置位已取过（后续事件到达还会再试），返回 False 而非卡死。"""
    monkeypatch.setattr(lifecycle_module, "LOGIN_RETRY_DELAYS", (0.01, 0.01, 0.01))
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    conn = _conn(_OneMessageWs())
    gw.connections = {"main": conn}

    calls = {"info": 0}

    async def always_fail():
        calls["info"] += 1
        return {"status": "failed"}

    conn.get_login_info = always_fail
    conn.get_group_list = always_fail

    assert await gw._login_with_retry(conn, _NullLogger()) is False
    assert calls["info"] == 1 + len(lifecycle_module.LOGIN_RETRY_DELAYS)
    assert conn.login_retrieved is False, "失败不得置位「已取过」，否则永不重试"


async def test_bot_server_recovers_half_online_state(monkeypatch):
    """端到端：接收循环收到第一条事件即拉登录信息，前两次失败后自愈为已上线。"""
    monkeypatch.setattr(lifecycle_module, "LOGIN_RETRY_DELAYS", (0.01, 0.01, 0.01))
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    ws = _OneMessageWs()
    conn = _conn(ws)
    gw.connections = {"main": conn}

    calls = {"info": 0}

    async def flaky_login_info():
        calls["info"] += 1
        if calls["info"] < 3:
            return {"status": "failed", "retcode": 1}
        return {"status": "ok", "data": {"user_id": 10001, "nickname": "小明"}}

    async def groups():
        return {"status": "ok", "data": []}

    conn.get_login_info = flaky_login_info
    conn.get_group_list = groups

    logged_in = asyncio.Event()

    async def on_login(conn_):
        logged_in.set()

    gw.login_handler = on_login
    task = asyncio.create_task(gw._bot_server("main"))
    try:
        assert await _wait(lambda: logged_in.is_set()), "登录信息必须最终拉取成功（不再永久半上线）"
        assert conn.bot_id == 10001
        assert conn.login_retrieved is True
        assert conn.status == "connected"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
