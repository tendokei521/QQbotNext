"""WS 广播与日志推送的慢客户端保护。

历史问题（`app/webui/ws.py`）：

- `broadcast()` 按连接顺序 `await send_text` 且无超时：一个挂起的标签页就能把
  `gateway._notify_status` / 配置保存接口一起卡住；
- `/ws/logs` 端点用 `websocket.send_text` 直发，绕开连接管理；
- 日志端点每秒同步读日志文件，在事件循环里做文件 IO。

修法：每连接一个发送队列 + sender 任务；广播只入队；队列满 / 单条发送超时即断开
该连接；日志推送也走 manager，读取用 `asyncio.to_thread`。
"""

from __future__ import annotations

import asyncio
import time

from app.application.log_service import LogService
from app.webui import ws as ws_mod


class _FakeWebSocket:
    """最小 WebSocket 替身：可接受连接、可被 gate 卡住发送、记录是否被关闭。"""

    def __init__(self, block: asyncio.Event | None = None) -> None:
        self.accepted = False
        self.closed = False
        self.sent: list[str] = []
        self._block = block

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, message: str) -> None:
        if self._block is not None:
            await self._block.wait()
        self.sent.append(message)

    async def close(self, code: int = 1000) -> None:
        self.closed = True


async def test_broadcast_never_blocks_on_slow_client():
    """队列被慢客户端塞满 → 广播仍在 1s 内返回，并断开该客户端。"""
    manager = ws_mod.ConnectionManager()
    block = asyncio.Event()
    slow = _FakeWebSocket(block)
    await manager.connect(slow)

    begin = time.monotonic()
    for _ in range(ws_mod.CLIENT_QUEUE_SIZE + 5):
        await manager.broadcast('{"type": "status"}')
    elapsed = time.monotonic() - begin

    assert elapsed < 1.0, f"broadcast 被慢客户端卡住 {elapsed:.2f}s"
    assert slow not in manager.active_connections
    assert manager.dropped >= 1

    block.set()
    await asyncio.sleep(0)  # 让 sender 任务收尾


async def test_slow_send_times_out_and_disconnects(monkeypatch):
    """单条发送超过 SEND_TIMEOUT_SECONDS → 断开该连接（其他连接不受影响）。"""
    monkeypatch.setattr(ws_mod, "SEND_TIMEOUT_SECONDS", 0.05)
    manager = ws_mod.ConnectionManager()
    block = asyncio.Event()
    slow = _FakeWebSocket(block)
    healthy = _FakeWebSocket()
    await manager.connect(slow)
    await manager.connect(healthy)

    begin = time.monotonic()
    await manager.broadcast('{"type": "status"}')
    assert time.monotonic() - begin < 1.0, "广播本身不能等 send_text"

    await asyncio.sleep(0.25)  # 等超时发生
    assert slow not in manager.active_connections
    assert slow.closed is True
    assert healthy.sent == ['{"type": "status"}'], "健康客户端必须照常收到广播"


async def test_broadcast_reaches_every_healthy_client():
    manager = ws_mod.ConnectionManager()
    first = _FakeWebSocket()
    second = _FakeWebSocket()
    await manager.connect(first)
    await manager.connect(second)

    await manager.broadcast('{"type": "modules_reloaded"}')
    await asyncio.sleep(0.05)

    assert first.sent == ['{"type": "modules_reloaded"}']
    assert second.sent == ['{"type": "modules_reloaded"}']
    assert len(manager.active_connections) == 2, "健康连接不能被顺带摘掉"


async def test_send_to_unknown_connection_returns_false():
    manager = ws_mod.ConnectionManager()
    assert await manager.send(_FakeWebSocket(), "{}") is False


async def test_log_service_async_reads_without_blocking_loop(tmp_path):
    """异步日志读取与同步版结果一致（IO 已挪到线程池）。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "user.log").write_text(
        "2026-01-01 00:00:00 - info - hello\n", encoding="utf-8"
    )
    service = LogService(log_dir)

    expected = service.get_recent_logs(10, ["info"], source="user")
    actual = await service.get_recent_logs_async(10, ["info"], source="user")

    assert actual == expected
    assert actual[-1]["message"] == "hello"
