"""会话级异步锁：避免同一会话并发执行 LLM 请求/写历史。

示例：
    async with session_locks.lock("group_123"):
        ...

内存管理：QQ 机器人的会话数无界（每个群/用户组合都是一个会话），若注册表只增不删，
长时间运行会为历史上出现过的每个会话永久保留一个 asyncio.Lock（缓慢泄漏）。
因此 release() 在锁被释放且无人等待时顺手摘除注册表条目；摘除后若有新请求到达，
会新建一把锁——此时旧锁已无持有者与等待者，故不会破坏互斥语义。

不能用 WeakValueDictionary 直接替代：pipeline.py 的用法是持有锁对象跨越 await
（lock() -> await acquire() -> ... -> release()），若中间没有强引用，锁对象可能
在等待期间被回收，导致同一会话出现两把不同的锁而失去互斥。
"""

from __future__ import annotations

import asyncio


class SessionLockManager:
    """按 session_id 维护的 asyncio.Lock 注册表（空闲时自动回收，避免无界增长）。"""

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
        """释放指定会话的锁；若随后已无人持有/等待，则顺带回收注册表条目。

        始终校验锁对象仍是注册表中的当前对象（`is` 比较），避免误删并发场景下
        新建的同名锁。
        """
        key = str(session_id or "unknown")
        lock = self._locks.get(key)
        if lock is None:
            return
        if lock.locked():
            lock.release()
        # 释放后若既无持有者也无等待者，说明该会话空闲，可安全摘除以防止无界增长
        if not lock.locked() and not self._has_waiters(lock):
            if self._locks.get(key) is lock:
                self._locks.pop(key, None)

    @staticmethod
    def _has_waiters(lock: asyncio.Lock) -> bool:
        """是否存在排队等待该锁的协程（兼容不同 Python 版本的私有实现）。"""
        waiters = getattr(lock, "_waiters", None)
        return bool(waiters) if waiters is not None else False

    def clear(self, session_id: str | None = None) -> None:
        if session_id is not None:
            self._locks.pop(str(session_id), None)
        else:
            self._locks.clear()

    def active_count(self) -> int:
        return sum(1 for l in self._locks.values() if l.locked())

    def tracked_count(self) -> int:
        """当前注册表内跟踪的锁数量（用于验证空闲回收是否生效）。"""
        return len(self._locks)

