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
from typing import Awaitable, Callable

from app.core.logger import logger
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
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        self.config = config or {}
        self._send_message = send_message
        self._pre_send = pre_send
        self._post_send = post_send
        self._should_cancel = should_cancel
        self._policy = SendPolicy(self.config)

        maxsize = int(self.config.get("stream_send_max_queue", 20) or 20)
        self._queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=maxsize)
        self._finished = False
        self._flush = False
        self._cancelled = False
        self._drained = asyncio.Event()
        # 「已取出队列、但尚未发送结束」的条数：finish() 只据此判断是否还有在途消息，
        # 队列空不代表发完（发送节奏有 sleep）。
        self._inflight = 0
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
        if self.config.get("stream_flush_on_finish", False):
            self._flush = True
        if self._queue.empty() and self._inflight == 0:
            self._drained.set()

    async def wait_drained(self) -> None:
        """等待队列清空**且最后一条已真正发送完**。

        只等「队列为空」是不够的：``finish()`` 在队列为空时会立刻置位 ``_drained``，
        但此时发送协程可能还卡在发送节奏的 ``sleep`` 里（消息已被取出、正等待发送）。
        调用方（LlmPipeline）随后会 ``shutdown()`` 取消发送协程，于是最后一条消息
        被静默丢弃——表现为「模型明明回了，用户却收不到」。
        因此 ``_drained`` 只在「无排队 + 无在途」时置位，这里等它即可。

        发送协程自身的异常不在这里抛出（它的异常已在 ``_sender_loop`` 内记日志，
        抛出会顶掉流水线收尾逻辑并造成"Task exception was never retrieved"噪音）。
        """
        await self._drained.wait()

    def pause(self) -> None:
        self._paused.clear()

    def resume(self) -> None:
        self._paused.set()

    async def shutdown(self) -> None:
        self.clear_pending()
        task = self._sender_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def clear_pending(self) -> None:
        """丢弃队列中尚未发送的消息。"""
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

    def _apply_affix(self, msg: Message) -> Message:
        prefix = self.config.get("stream_send_prefix", "") or ""
        suffix = self.config.get("stream_send_suffix", "") or ""
        if not prefix and not suffix:
            return msg
        if isinstance(msg, Message):
            return Message.from_text(prefix + msg.text + suffix)
        return Message.from_text(prefix + str(msg) + suffix)

    async def _sender_loop(self) -> None:
        """流式发送循环。

        正常结束条件：``finish()`` 已调用、队列为空且无在途消息 → 置位 ``_drained``
        并退出（这样 ``wait_drained()`` 才有确定的完成语义）。
        单条发送失败不能吞掉：记 error 日志并继续发后面的（一条失败不该让整段回复中断），
        同时保证循环最终仍会置位 ``_drained``，否则调用方会一直等在收尾上。
        """
        try:
            while True:
                # 收尾退出：不再有新消息、队列已空、上一条也已发完 → 置位并结束
                if self._finished and self._queue.empty() and self._inflight == 0:
                    self._drained.set()
                    return

                msg = await self._queue.get()
                self._inflight += 1

                # 回复打断：任务已过期则不再发送剩余消息
                if self._should_cancel and self._should_cancel():
                    self._cancelled = True
                    self._queue.task_done()
                    self._inflight -= 1
                    break

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
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    # 单条发送失败：留痕后继续处理后续消息，避免整段流式回复被打断
                    logger.add_info("StreamSendPool").error(f"[发送池] 单条消息发送失败（已跳过）: {e}")
                finally:
                    self._queue.task_done()
                    self._inflight -= 1
        except asyncio.CancelledError:
            raise
        finally:
            # 无论正常收尾、被取消还是异常退出，都解除调用方等待，避免收尾死等
            self._drained.set()
