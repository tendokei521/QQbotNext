"""反向监听器生命周期回归测试。

真实缺陷：``listening`` 用 ``getattr(server, "closed", None)`` 判断，而 websockets 16
的 ``Server`` 没有 ``closed`` 属性（只有 ``is_serving()``）→ 永远返回 True：
监听已停/已崩也不会重建，监督循环与诊断台都以为「还在听」。
"""

from __future__ import annotations

import socket

from app.infrastructure.onebot.reverse import ReverseWsServer


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _NullLog:
    def __init__(self) -> None:
        self.debugs: list[str] = []

    def debug(self, msg: str, *_a, **_k) -> None:
        self.debugs.append(str(msg))

    def info(self, *_a, **_k) -> None:
        pass

    def warning(self, msg: str, *_a, **_k) -> None:
        self.debugs.append(str(msg))


class _BrokenServeState:
    """is_serving() 自身抛异常：状态探测失败应按「未监听」处理。"""

    def is_serving(self) -> bool:
        raise RuntimeError("loop 已关闭")


async def test_listening_turns_false_after_underlying_server_is_closed():
    """回归：底层 Server close + wait_closed 后 listening 必须为 False（旧实现恒 True）。"""
    port = _free_port()

    async def handler(ws, client):  # pragma: no cover —— 本用例不接入客户端
        await ws.wait_closed()

    srv = ReverseWsServer(url=f"ws://127.0.0.1:{port}/onebot/v11/ws", handler=handler, log=_NullLog())
    await srv.start()
    try:
        assert srv.listening is True
        assert srv._server is not None
        srv._server.close()  # 模拟监听被外部/异常关闭，但 _server 引用还在
        await srv._server.wait_closed()
        assert srv.listening is False, "websockets 16 没有 closed 属性，必须用 is_serving() 判断"
    finally:
        await srv.stop()


async def test_listening_false_when_state_probe_raises():
    """状态探测异常（事件循环已关等）→ 按未监听处理，并留 debug 痕迹。"""

    async def handler(ws, client):  # pragma: no cover
        await ws.wait_closed()

    log = _NullLog()
    srv = ReverseWsServer(url="ws://127.0.0.1:1/onebot/v11/ws", handler=handler, log=log)
    srv._server = _BrokenServeState()

    assert srv.listening is False
    assert any("监听状态探测失败" in line for line in log.debugs)
