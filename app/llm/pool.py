"""LLM 请求池：同一会话的防抖 / 合并 / 取代。

核心语义：
- 同一 ``group_key``（群/私聊会话）的新 Job 到达时，旧 Job 被标记为 superseded；
- 只保留最新 Job 等待防抖计时器；
- 计时器到期后放行最新 Job，旧 Job 自动放弃。

``debounce``（消息抖动）与 ``max_wait``（最长等待）配合使用：
- 每来一条新消息就重置计时器（经典防抖）；
- ``max_wait`` 从**本批次第一条消息**起算，到点强制放行——否则持续刷屏时
  计时器被无限重置，会永远等不到静默、永远不响应。
"""

from __future__ import annotations

import asyncio
from typing import Any


class LlmPool:
    def __init__(self) -> None:
        self._current: dict[str, Any] = {}
        self._timers: dict[str, asyncio.Task] = {}
        self._pending_texts: dict[str, list[str]] = {}
        #: 本批次第一条消息的到达时刻（loop 单调时钟），用于 max_wait 兜底
        self._batch_started: dict[str, float] = {}

    async def wait_for_continue(
        self,
        job: Any,
        debounce: float = 0.0,
        max_wait: float = 0.0,
    ) -> bool:
        """让 job 进入请求池等待。

        返回 ``True`` 表示可以继续；返回 ``False`` 表示已被新消息取代或主动跳过。
        """
        key = job.group_key
        loop = asyncio.get_running_loop()
        now = loop.time()

        prev = self._current.get(key)
        if prev is not None and prev is not job:
            prev.superseded = True
            prev.go.set()

        self._current[key] = job
        self._pending_texts.setdefault(key, []).append(job.ctx.user_text)

        if debounce > 0:
            # 批次起始时刻只在“没有正在等待的批次”时记录
            if self._batch_started.get(key) is None:
                self._batch_started[key] = now
            remaining = debounce
            if max_wait > 0:
                deadline = self._batch_started[key] + max_wait
                remaining = min(debounce, max(0.0, deadline - now))
            self._cancel_timer(key)
            self._timers[key] = asyncio.create_task(self._timer(key, remaining, job))
        else:
            self._batch_started.pop(key, None)
            job.go.set()

        await job.go.wait()

        if job.superseded or job.skip:
            return False
        return True

    async def _timer(self, key: str, delay: float, job: Any) -> None:
        try:
            await asyncio.sleep(delay)
            if self._current.get(key) is job:
                job.go.set()
        except asyncio.CancelledError:
            pass
        finally:
            if self._timers.get(key) is asyncio.current_task():
                self._timers.pop(key, None)

    def _cancel_timer(self, key: str) -> None:
        task = self._timers.pop(key, None)
        if task is not None and not task.done():
            task.cancel()

    def finish(self, job: Any) -> None:
        """Job 结束（成功/放弃）后从池中移除。"""
        key = job.group_key
        if self._current.get(key) is job:
            self._current.pop(key, None)
            self._cancel_timer(key)
            # 只有当前 Job 结束时才清空 pending 文本，避免旧 Job 误清新 Job
            self._pending_texts.pop(key, None)
            self._batch_started.pop(key, None)

    def take_pending_texts(self, key: str) -> list[str]:
        """取出该会话暂存的用户消息文本（用于合并请求）。"""
        return self._pending_texts.pop(key, [])

    def pending_texts(self, key: str) -> list[str]:
        return list(self._pending_texts.get(key, []))


def merge_debounced_texts(texts: list[str], limit: int = 0) -> str:
    """把抖动窗口内攒下的多条消息合并成一段用户文本。

    逐条截断到 ``limit`` 而不是整段截断：整段截会把后面的消息直接丢掉，
    而那些消息正是抖动要合并进来的东西。
    """
    parts: list[str] = []
    for text in texts:
        cleaned = str(text or "").strip()
        if not cleaned:
            continue
        parts.append(cleaned[:limit] if limit and limit > 0 else cleaned)
    return "\n".join(parts)

