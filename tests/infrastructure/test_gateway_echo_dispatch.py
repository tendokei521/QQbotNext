"""API 响应（echo）不得被当成事件派发（回归测试）。

真实故障形态：迟到的 API 响应（超时后才回来的 echo）既不在 ``_pending`` 也不在
``_orphan_echoes`` 时，``handle_api_response`` 返回 False → 接收循环继续
``codec.decode`` → ``BaseEvent(type="unknown")`` 入队 → 进入模块链做无意义派发。
修复：任何带 echo 的消息都按「已消费」处理；无 ``post_type`` 的消息在入队前兜底丢弃。
"""

from __future__ import annotations

import asyncio
import json
import time

from app.infrastructure.cache import Cache
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


def test_unknown_echo_is_consumed_not_treated_as_event():
    """未知/重复的 echo 也返回 True（已消费），只有完全无 echo 的才返回 False。"""
    conn = BotConnection(websocket=None)
    conn.account_id = "main"

    assert conn.handle_api_response({"status": "ok", "echo": "never-seen", "data": {}}) is True
    assert conn.handle_api_response({"status": "ok", "data": {}}) is False

    # 迟到响应：请求方已放弃，仍属于「响应」而非事件
    conn._orphan_echoes.append("orphan-1")
    assert conn.handle_api_response({"status": "ok", "echo": "orphan-1"}) is True


class _MixedWs:
    """先投递「未知 echo 响应 + 无 post_type 包 + 一条真实事件」，然后保持连接。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._queue = [
            json.dumps({"status": "ok", "retcode": 0, "echo": "unknown-echo", "data": {}}),
            json.dumps({"status": "ok", "retcode": 0, "data": {}}),  # 无 echo 且无 post_type
            json.dumps({
                "post_type": "message", "message_type": "private",
                "user_id": 100, "self_id": 1, "time": int(time.time()),
                "message": [{"type": "text", "data": {"text": "真事件"}}],
            }),
        ]

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self._queue:
            return self._queue.pop(0)
        await asyncio.Event().wait()

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def close(self) -> None:
        pass


async def test_echo_and_typeless_payloads_never_reach_dispatch():
    """端到端：只有真实事件进入派发链，echo 响应/无 post_type 包一律不进。"""
    gw = OneBotGateway(settings=_Settings(), cache=Cache())

    async def _noop_login(conn):
        return False

    gw._get_login_info = _noop_login
    gw._get_login_groups = _noop_login

    ws = _MixedWs()
    conn = BotConnection(websocket=ws)
    conn.account_id = "main"
    conn.account_name = "主号"
    gw.connections = {"main": conn}

    seen: list = []

    async def handler(event):
        seen.append(event)

    gw.dispatch_handler = handler
    task = asyncio.create_task(gw._bot_server("main"))
    try:
        deadline = time.monotonic() + 2
        while not seen and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)  # 给错误派发留出发生窗口
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert len(seen) == 1, f"只有真实事件可以入队派发，实际 {[e.event_type for e in seen]}"
    assert seen[0].event_type == "message_private"
