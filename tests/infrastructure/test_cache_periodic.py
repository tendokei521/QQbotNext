"""缓存过期清理的节流：set 不应每次全表扫描。

原实现 ``set()`` 末尾无条件 ``_cleanup()``：缓存里有 n 条时每次写都会遍历一遍，
高频写入（去重/冷却/跟随这类短命键很多）退化成 O(n²)。现在改为"每 60s 或每
256 次 set 才扫一次"，而**单项过期语义不变**——get/has 仍按条目自身 TTL 惰性判过期。
"""

from __future__ import annotations

import time

from app.infrastructure.cache import (
    CLEANUP_EVERY_SETS,
    CLEANUP_INTERVAL,
    Cache,
)


def _preload(cache: Cache, count: int, ttl: float = 3600) -> None:
    """直接塞长 TTL 条目（绕过 set，避免写测试数据时提前触发清理）。"""
    expire = time.time() + ttl
    cache._store = {f"k{i}": (i, expire) for i in range(count)}


def test_set_does_not_scan_whole_store_every_call(monkeypatch):
    cache = Cache()
    calls: list[int] = []
    original = cache._cleanup

    def counting_cleanup() -> None:
        calls.append(1)
        original()

    monkeypatch.setattr(cache, "_cleanup", counting_cleanup)
    _preload(cache, 20_000)

    for index in range(100):  # 远低于次数阈值、也在时间间隔内
        cache.set(f"new{index}", index, ttl=3600)

    assert calls == [], "set 每次都做全表清理：缓存越大写入越慢（O(n²)）"


def test_cleanup_still_runs_every_n_sets():
    """次数阈值到达时必须真的清掉过期条目（节流不能变成"永不清理"）。"""
    cache = Cache()
    cache._store = {"expired": (1, time.time() - 10), "fresh": (2, time.time() + 3600)}
    cache._sets_since_cleanup = CLEANUP_EVERY_SETS - 1

    cache.set("trigger", 1, ttl=3600)

    assert "expired" not in cache._store
    assert "fresh" in cache._store
    assert cache._sets_since_cleanup == 0


def test_cleanup_still_runs_when_interval_elapses():
    """时间阈值到达时同样要清理（长期低频写入也不能让过期条目堆积）。"""
    cache = Cache()
    cache._store = {"expired": (1, time.time() - 10)}
    cache._last_cleanup = time.time() - CLEANUP_INTERVAL - 1

    cache.set("trigger", 1, ttl=3600)

    assert "expired" not in cache._store


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
