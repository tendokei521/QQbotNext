"""媒体缓存通用处理（跨模块共享）。

视频/图集类插件都要做同一件事：**同一份媒体只下载一次**，并按 TTL + 容量上限淘汰。
本文件把这部分纯逻辑收在一处，插件各自调用，不再各自复制：

- ``human_size`` / ``format_duration``：日志与文案用的格式化；
- ``is_fresh``：缓存文件是否仍在保留期内；
- ``pick_stale``：算出该删哪些文件（纯函数，返回待删路径，由调用方执行）；
- ``sweep``：按上面的策略真正清理目录（带日志，失败只告警不抛）。

调用方只需提供自己的 ``cache_path`` 命名（如 ``<bvid>_p1.mp4`` / ``<aweme_id>.mp4``）。
"""

from __future__ import annotations

import os
import time

#: 下载中的临时后缀（下完 ``os.replace`` 改名，避免半成品被当成缓存命中）
PART_SUFFIX = ".part"


def human_size(n: float) -> str:
    """字节数转可读大小（下载/日志展示用）。"""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def format_duration(ms) -> str:
    """毫秒转 ``M:SS`` / ``H:MM:SS``（时长展示）。"""
    if not ms:
        return "-"
    total = int(int(ms) / 1000)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def is_fresh(path: str, ttl_minutes: int, now: float | None = None) -> bool:
    """缓存文件是否存在且未过期（TTL <= 0 表示永不过期）。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False
    ttl = int(ttl_minutes or 0) * 60
    if ttl <= 0:
        return True
    return ((time.time() if now is None else now) - mtime) < ttl


def pick_stale(
    entries: list[tuple[str, float, int]],
    ttl_minutes: int,
    max_bytes: int,
    now: float | None = None,
) -> list[str]:
    """计算需要删除的缓存文件（先到期，再按最旧超出容量上限）。

    Args:
        entries: ``[(path, mtime, size), ...]``
        ttl_minutes: 保留时长（<=0 表示不按时间淘汰）
        max_bytes: 目录容量上限（<=0 表示不按容量淘汰）
    """
    now = time.time() if now is None else now
    ttl = int(ttl_minutes or 0) * 60

    stale = [path for path, mtime, _size in entries if ttl > 0 and (now - mtime) >= ttl]
    kept = sorted((e for e in entries if e[0] not in stale), key=lambda e: e[1])

    total = sum(size for _path, _mtime, size in kept)
    if max_bytes <= 0:
        return stale
    for path, _mtime, size in kept:
        if total <= max_bytes:
            break
        stale.append(path)
        total -= size
    return stale


def sweep(cache_dir: str, ttl_minutes: int, max_mb: int, logger=None) -> list[str]:
    """按 TTL + 容量上限清理缓存目录，返回被删除的路径列表。

    目录不存在或读取失败只告警，不抛异常（清理失败不该影响本次业务）。
    """
    try:
        entries = []
        for name in os.listdir(cache_dir):
            path = os.path.join(cache_dir, name)
            if os.path.isfile(path):
                entries.append((path, os.path.getmtime(path), os.path.getsize(path)))
        stale = pick_stale(entries, ttl_minutes, int(max_mb or 0) * 1024 * 1024)
        for path in stale:
            os.remove(path)
        if stale and logger is not None:
            logger.debug(f"清理媒体缓存 {len(stale)} 个文件")
        return stale
    except OSError as e:
        if logger is not None:
            logger.warning(f"清理媒体缓存失败: {e}")
        return []
