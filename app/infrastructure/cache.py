"""进程内缓存（内存实现，接口可替换为 Redis）。

语义与原 cache_manager 一致：set/get/has/delete/clear/touch/refresh，
带 TTL 的惰性清理。模块间共享临时状态（去重、跟随、冷却等）。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from app.core.logger import logger

#: 过期清理的触发间隔（秒）与 set 次数阈值：二者任一到达才全表扫一遍。
#: 原实现每次 set 都扫全表，缓存条目多时高频 set 变成 O(n²)。
CLEANUP_INTERVAL = 60.0
CLEANUP_EVERY_SETS = 256


class Cache:
    """线程安全的 TTL 内存缓存。"""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = threading.RLock()
        # 清理节流状态：不参与对外语义，get/has 仍按单项惰性判过期
        self._last_cleanup = time.time()
        self._sets_since_cleanup = 0

    def set(self, key: str, value: Any = True, ttl: int = 300) -> None:
        if not isinstance(ttl, int):
            logger.error(f"[Cache] {key} TTL {ttl} 不是整数")
            return
        expire = time.time() + ttl
        with self._lock:
            self._store[key] = (value, expire)
            self._sets_since_cleanup += 1
            due = (
                self._sets_since_cleanup >= CLEANUP_EVERY_SETS
                or time.time() - self._last_cleanup >= CLEANUP_INTERVAL
            )
        if due:
            self._cleanup()

    def get(self, key: str) -> Any:
        with self._lock:
            if self.has(key):
                return self._store[key][0]
        return None

    def has(self, key: str) -> bool:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return False
            if time.time() > item[1]:
                del self._store[key]
                return False
            return True

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._store.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def touch(self, key: str) -> bool:
        """按原剩余 TTL 刷新。"""
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return False
            value, expire = item
            remaining = expire - time.time()
            if remaining <= 0:
                return False
            self._store[key] = (value, time.time() + remaining)
            return True

    def refresh(self, key: str, ttl: int = 300) -> bool:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return False
            self._store[key] = (item[0], time.time() + ttl)
            return True

    def _cleanup(self) -> None:
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._store.items() if now > v[1]]
            for k in expired:
                del self._store[k]
            self._last_cleanup = now
            self._sets_since_cleanup = 0


# 进程内唯一实例（由容器注入；未装配时使用默认实例）
_cache = Cache()
server_cache = Cache()


def get_cache() -> Cache:
    return _cache
