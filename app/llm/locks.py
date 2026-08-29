"""会话级异步锁：避免同一会话并发执行 LLM 请求/写历史。

示例：
    async with session_locks.lock("group_123"):
        ...
"""

from __future__ import annotations

import asyncio
from typing import Any


class SessionLockManager:
    """按 session_id 维护的 asyncio.Lock 注册表。"""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, session_id: str) -> asyncio.Lock:
        """获取（或创建）指定会话的异步锁。"""
        key = str(session_id or "unknown")
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def acquire(self, session_id: str) -> None:
        await self.lock(session_id).acquire()

    def release(self, session_id: str) -> None:
        lock = self._locks.get(str(session_id or "unknown"))
        if lock is not None and lock.locked():
            lock.release()

    def clear(self, session_id: str | None = None) -> None:
        if session_id is not None:
            self._locks.pop(str(session_id), None)
        else:
            self._locks.clear()

    def active_count(self) -> int:
        return sum(1 for l in self._locks.values() if l.locked())
