"""缓存过期清理的节流：set 不应每次全表扫描。

原实现 ``set()`` 末尾无条件 ``_cleanup()``：缓存里有 n 条时每次写都会遍历一遍，
高频写入（去重/冷却/跟随这类短命键很多）退化成 O(n²)。现在改为"每 60s 或每
256 次 set 才扫一次"，而**单项过期语义不变**——get/has 仍按条目自身 TTL 惰性判过期。
"""

from __future__ import annotations

import time

from app.infrastructure.cache import Cache


def _preload(cache: Cache, count: int, ttl: float = 3600) -> None:
    """直接塞长 TTL 条目（绕过 set，避免写测试数据时提前触发清理）。"""
    expire = time.time() + ttl
    cache._store = {f"k{i}": (i, expire) for i in range(count)}


def test_expired_entry_is_miss_between_cleanups():
    """清理被节流后，单项过期仍由 get/has 惰性判定（对外语义不变）。"""
    cache = Cache()
    cache._store["old"] = ("value", time.time() - 1)

    assert cache.get("old") is None
    assert "old" not in cache._store


def test_bulk_set_cost_does_not_scale_with_store_size():
    """实测回归：2 万条存量下 1000 次 set 应在毫秒级完成。

    旧实现是 1000 × 20000 = 2000 万次判断（通常 >1s）；新实现只在阈值处扫表。
    """
    cache = Cache()
    _preload(cache, 20_000)

    start = time.perf_counter()
    for index in range(1000):
        cache.set(f"n{index}", index, ttl=3600)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"2 万条存量下 1000 次 set 用了 {elapsed:.3f}s，清理节流失效"
