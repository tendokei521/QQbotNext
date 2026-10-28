"""事件队列背压回归测试。

真实故障形态：接收循环里 ``await queue.put(event)`` 在队列满时停住 → 同一连接上
模块正在等的 API 响应（echo）没人消费 → ``send_group_msg`` 等全部超时 10~60s。
修复方向：接收循环用 ``put_nowait``，队满时丢最旧事件并汇总告警，永不阻塞收包。
"""

from __future__ import annotations

import asyncio
import json
import time

from app.infrastructure.cache import Cache
from app.infrastructure.onebot.client import BotConnection
from app.infrastructure.onebot.gateway import OneBotGateway
from app.infrastructure.onebot.gateway_events import EVENT_QUEUE_MAXSIZE, enqueue_event


class _Settings:
    ws_ping_interval = 30
    ws_ping_timeout = 10
    ws_connect_timeout = 3


class _NullLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str, *_a, **_k) -> None:
        self.warnings.append(str(message))

    def info(self, *_a, **_k) -> None:
        pass

    debug = info

    def error(self, *_a, **_k) -> None:
        pass

    def exception(self, *_a, **_k) -> None:
        pass

    def prefix(self, *_a, **_k):
        return self

    def add_info(self, *_a, **_k):
        return self


class _FloodWs:
    """先连续投递 ``count`` 条消息事件，再回投一次 API 响应（模拟事件洪峰 + 在途请求）。"""

    def __init__(self, count: int) -> None:
        self.count = count
        self.sent: list[dict] = []
        self._emitted = 0
        self._responded = False

    def __aiter__(self):
        return self

    @staticmethod
    def _message(text: str) -> str:
        return json.dumps({
            "post_type": "message", "message_type": "private",
            "user_id": 100, "self_id": 1, "time": int(time.time()),
            "message": [{"type": "text", "data": {"text": text}}],
        })

    async def __anext__(self) -> str:
        if self._emitted < self.count:
            self._emitted += 1
            return self._message(f"flood-{self._emitted}")
        if not self._responded:
            deadline = time.monotonic() + 2
            while not any(p.get("action") == "send_private_msg" for p in self.sent):
                if time.monotonic() > deadline:
                    break
                await asyncio.sleep(0.01)
            echo = next((p["echo"] for p in self.sent if p.get("action") == "send_private_msg"), None)
            if echo:
                self._responded = True
                return json.dumps({"status": "ok", "retcode": 0, "echo": echo, "data": {"message_id": 9}})
        raise StopAsyncIteration

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def close(self) -> None:
        pass


async def test_queue_full_does_not_block_api_response():
    """回归：事件洪峰（队列打满）时，模块 await 的 API 响应仍须在 1s 内返回。

    旧实现 ``await queue.put`` 会让接收循环停在队列满的那一刻：worker 处理第一条
    事件时发起的 ``send_private_msg`` 永远等不到 echo（10s 超时）。修复后接收循环
    持续收包，丢弃计数 > 0 且 echo 1s 内 resolve。
    """
    gw = OneBotGateway(settings=_Settings(), cache=Cache())

    async def _noop_login(conn):
        return False

    gw._get_login_info = _noop_login
    gw._get_login_groups = _noop_login

    ws = _FloodWs(EVENT_QUEUE_MAXSIZE * 2 - 48)  # 明显超过队列上限
    conn = BotConnection(websocket=ws, bot_id=123)
    conn.account_id = "main"
    conn.account_name = "主号"
    gw.connections = {"main": conn}

    reply_ok = asyncio.Event()
    handled = 0

    async def handler(event):
        nonlocal handled
        handled += 1
        if handled > 1:
            return  # 洪峰事件不重复发请求
        resp = await conn.send_private_msg(100, "hi")
        if resp and resp.get("status") == "ok":
            reply_ok.set()

    gw.dispatch_handler = handler
    t0 = time.perf_counter()
    task = asyncio.create_task(gw._bot_server("main"))
    try:
        await asyncio.wait_for(reply_ok.wait(), 1.0)
        elapsed = time.perf_counter() - t0
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert reply_ok.is_set(), "队列打满时 API echo 仍须被接收循环消费（不得 10s 超时）"
    assert elapsed < 1.0, f"API 响应应在 1s 内返回，实际 {elapsed:.2f}s"
    assert gw.event_queue_drops.get("main", 0) > 0, "事件队列满时必须统计并丢弃最旧事件"
    assert any(p.get("action") == "send_private_msg" for p in ws.sent)


async def test_enqueue_drops_oldest_and_counts():
    """队列满 → 丢最旧一条（并把它返回，供诊断历史标记 dropped），累计计数+告警。"""
    log = _NullLogger()
    queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    drops: dict[str, int] = {}

    assert enqueue_event(queue, "e1", account_id="main", drops=drops, logger=log) is None
    assert enqueue_event(queue, "e2", account_id="main", drops=drops, logger=log) is None
    assert enqueue_event(queue, "e3", account_id="main", drops=drops, logger=log) == "e1", \
        "队满时必须返回被丢掉的最旧项（诊断台据此标记 dropped）"

    assert queue.qsize() == 2
    assert queue.get_nowait() == "e2", "队满时必须丢最旧事件，而不是阻塞或丢新事件"
    assert queue.get_nowait() == "e3"
    assert drops["main"] == 1
    assert log.warnings and "丢弃" in log.warnings[0]
