"""流式回复有序消息池。

职责：
- 接收流式句子，按 FIFO 顺序保存；
- 按 SendPolicy 计算发送间隔；
- 支持前缀/后缀；
- 每条消息发送前后仍然由外部调用方触发 pre_send / post_send 钩子；
- 流结束后等待队列清空。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from app.domain.message import Message
from app.llm.send_policy import SendPolicy


class StreamSendPool:
    def __init__(
        self,
        config: dict | None = None,
        *,
        send_message: Callable[[Message], Awaitable[None]],
        pre_send: Callable[[Message], Awaitable[bool]] | None = None,
        post_send: Callable[[Message], Awaitable[None]] | None = None,
    ) -> None:
        self.config = config or {}
        self._send_message = send_message
        self._pre_send = pre_send
        self._post_send = post_send
        self._policy = SendPolicy(self.config)

        maxsize = int(self.config.get("stream_send_max_queue", 20) or 20)
        self._queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=maxsize)
        self._finished = False
        self._flush = False
        self._drained = asyncio.Event()
        self._paused = asyncio.Event()
        self._paused.set()
        self._sender_task = asyncio.create_task(self._sender_loop())

    async def put(self, msg: Message) -> None:
        if self._finished:
            return

        policy = self.config.get("stream_queue_full_policy", "backpressure")
        if policy == "backpressure":
            await self._queue.put(msg)
        elif policy == "drop_newest":
            if not self._queue.full():
                await self._queue.put(msg)
        elif policy == "drop_oldest":
            if self._queue.full():
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except asyncio.QueueEmpty:
                    pass
            await self._queue.put(msg)

    async def finish(self) -> None:
        """通知消息池：不会再有新消息。"""
        self._finished = True
        if self.config.get("stream_flush_on_finish", True):
            self._flush = True
        if self._queue.empty():
            self._drained.set()

    async def wait_drained(self) -> None:
        """等待队列清空且 sender 结束。"""
        await self._drained.wait()

    def pause(self) -> None:
        self._paused.clear()

    def resume(self) -> None:
        self._paused.set()

    def shutdown(self) -> None:
        if self._sender_task and not self._sender_task.done():
            self._sender_task.cancel()

    def _apply_affix(self, msg: Message) -> Message:
        prefix = self.config.get("stream_send_prefix", "") or ""
        suffix = self.config.get("stream_send_suffix", "") or ""
        if not prefix and not suffix:
            return msg
        if isinstance(msg, Message):
            return Message.from_text(prefix + msg.text + suffix)
        return Message.from_text(prefix + str(msg) + suffix)

    async def _sender_loop(self) -> None:
        try:
            while True:
                msg = await self._queue.get()
                try:
                    await self._paused.wait()

                    delay = 0.0 if self._flush else self._policy.next_delay(msg)
                    if delay > 0:
                        await asyncio.sleep(delay)

                    final_msg = self._apply_affix(msg)

                    if self._pre_send is not None:
                        skip = await self._pre_send(final_msg)
                        if skip or getattr(final_msg, "skip", False):
                            continue

                    await self._send_message(final_msg)

                    if self._post_send is not None:
                        await self._post_send(final_msg)
                finally:
                    self._queue.task_done()
                    if self._finished and self._queue.empty():
                        self._drained.set()
        except asyncio.CancelledError:
            raise
