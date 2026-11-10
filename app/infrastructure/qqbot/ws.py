"""官方机器人 WebSocket 接入（事件推送）。

协议要点（2026-11）：

1. ``GET /gateway`` 取 wss 地址（本模块用 ``QqBotAPI.gateway_url()``）；
2. 连接后收 ``op 10 Hello``（心跳周期）；
3. 发 ``op 2 Identify``：``{"token": "QQBot <access_token>", "intents", "shard": [0,1]}``；
4. 按周期发 ``op 1`` 心跳（``d`` 为最近收到的 ``s``），服务端回 ``op 11``；
5. 断线重连可发 ``op 6 Resume``（session_id + seq）补发遗漏事件；
6. 服务端 ``op 7`` 要求重连、``op 9`` 表示 session 无效（需重新 Identify）。

本模块只负责「把 op 0 Dispatch 的 payload 逐个吐出来」，重连与解码留在网关/连接层。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import websockets

from app.core.logger import logger

from .api import QqBotAPI

#: 群聊 + 单聊事件（官方把这两类放在同一个 intent 位里）
INTENT_GROUP_AND_C2C = 1 << 25
#: 心跳失败/无 Hello 的上限（秒）
HELLO_TIMEOUT = 15.0


class QqBotReconnect(RuntimeError):
    """需要重连：``resume=True`` 表示可以带 session_id/seq 走 Resume。"""

    def __init__(self, reason: str, *, resume: bool = True) -> None:
        self.reason = str(reason or "reconnect")
        self.resume = bool(resume)
        super().__init__(self.reason)


class QqBotWSClient:
    """一条官方事件 WebSocket 连接（一个账号一条）。"""

    def __init__(
        self,
        api: QqBotAPI,
        *,
        intents: int = INTENT_GROUP_AND_C2C,
        log: Any = None,
    ) -> None:
        self.api = api
        self.intents = int(intents)
        self.log = log or logger
        self.ws: Any = None
        self.session_id = ""
        self.seq: int | None = None
        self.heartbeat_interval: float = 45.0
        self._hb_task: asyncio.Task | None = None
        self._closed = False

    async def connect(self, *, resume: bool = False) -> None:
        """建连 + Hello + Identify/Resume，然后启动心跳。"""
        url = await self.api.gateway_url()
        self.ws = await websockets.connect(url, ping_interval=None, max_size=8 * 1024 * 1024)
        try:
            hello = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=HELLO_TIMEOUT))
        except Exception as e:  # noqa: BLE001 —— 握手失败统一交给重连层
            await self.close()
            raise QqBotReconnect(f"未收到 Hello: {e}", resume=False) from e
        if int(hello.get("op", -1)) != 10:
            await self.close()
            raise QqBotReconnect(f"首帧不是 Hello: {hello}", resume=False)
        self.heartbeat_interval = float(
            (hello.get("d") or {}).get("heartbeat_interval") or 45000
        ) / 1000.0
        if resume and self.session_id:
            await self._send_resume()
        else:
            await self._send_identify()
        self._hb_task = asyncio.create_task(self._heartbeat_loop(), name="qqbot_heartbeat")

    async def _send_identify(self) -> None:
        token = await self.api.tokens.get()
        payload = {
            "op": 2,
            "d": {
                "token": f"QQBot {token}",
                "intents": self.intents,
                "shard": [0, 1],
                "properties": {"$os": "linux", "$browser": "qqbot-next", "$device": "qqbot-next"},
            },
        }
        await self.ws.send(json.dumps(payload))

    async def _send_resume(self) -> None:
        token = await self.api.tokens.get()
        payload = {
            "op": 6,
            "d": {"token": f"QQBot {token}", "session_id": self.session_id, "seq": self.seq},
        }
        await self.ws.send(json.dumps(payload))

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._closed and self.ws is not None:
                await asyncio.sleep(self.heartbeat_interval)
                if self.ws is None or self._closed:
                    return
                await self.ws.send(json.dumps({"op": 1, "d": self.seq}))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 心跳断了让读循环自然收尾
            self.log.debug(f"[QQBot] 心跳发送失败（将重连）: {e}")

    async def events(self) -> AsyncIterator[dict]:
        """逐个吐出 op 0 事件；需要重连时抛 ``QqBotReconnect``。"""
        if self.ws is None:
            raise QqBotReconnect("连接尚未建立", resume=False)
        try:
            async for raw in self.ws:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    self.log.warning(f"[QQBot] 收到非 JSON 消息: {str(raw)[:200]}")
                    continue
                op = int(payload.get("op", -1))
                if op == 0:
                    if payload.get("s") is not None:
                        self.seq = int(payload["s"])
                    name = str(payload.get("t") or "")
                    if name == "READY":
                        self.session_id = str((payload.get("d") or {}).get("session_id") or "")
                    yield {
                        "t": name,
                        "d": payload.get("d") or {},
                        "id": payload.get("id") or "",
                        "s": payload.get("s"),
                    }
                elif op == 11:  # Heartbeat ACK
                    continue
                elif op == 7:  # Reconnect
                    raise QqBotReconnect("服务端要求重连", resume=True)
                elif op == 9:  # Invalid Session
                    self.session_id = ""
                    raise QqBotReconnect("session 无效，需要重新 Identify", resume=False)
        except websockets.ConnectionClosed as e:  # type: ignore[attr-defined]
            raise QqBotReconnect(f"连接关闭: {e}", resume=bool(self.session_id)) from e

    async def close(self) -> None:
        self._closed = True
        if self._hb_task is not None:
            self._hb_task.cancel()
            try:
                await self._hb_task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001 —— 心跳收尾异常只留痕
                self.log.debug(f"[QQBot] 心跳任务收尾异常: {e}")
            self._hb_task = None
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception as e:  # noqa: BLE001 —— 关闭失败不阻断收尾
                self.log.debug(f"[QQBot] 关闭 WebSocket 失败（已忽略）: {e}")
            self.ws = None
