"""单文件（720P mp4）取流结果整理与本地缓存策略（纯逻辑，不碰网络）。

- ``pick_single``：把 playurl ``fnval=0`` 的返回整理成可下载的分片列表；
- 缓存：同一视频只下载一次，按 TTL + 容量上限淘汰（纯函数返回待删路径，由调用方执行）。
"""

from __future__ import annotations

import os
import time

# 单文件模式实测返回 mp4（ftypisom，含 vide+soun 双轨），故按 .mp4 命名
VIDEO_EXT = ".mp4"
PART_SUFFIX = ".part"

# 清晰度代码 -> 名称（部分高清晰度需登录/大会员）
QUALITY_NAMES = {
    6: "240P", 16: "360P", 32: "480P", 64: "720P", 74: "720P60",
    80: "1080P", 112: "1080P+", 116: "1080P60", 120: "4K", 125: "HDR", 127: "8K",
}

# 配置里的分辨率档位 -> B站清晰度代码 qn
HEIGHT_TO_QN = {"360": 16, "480": 32, "720": 64}
DEFAULT_QN = 64


def human_size(n: float) -> str:
    """字节数转可读大小（下载/日志展示用）。"""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def format_duration(ms) -> str:
    """毫秒转 ``M:SS`` / ``H:MM:SS``（视频时长展示）。"""
    if not ms:
        return "-"
    total = int(int(ms) / 1000)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def pick_single(play_data: dict | None) -> dict | None:
    """整理 ``fnval=0`` 的 playurl 返回；无可用分片（如风控返回）时返回 None。

    长视频 ``durl`` 可能多段，按顺序拼接即为完整文件。
    """
    segments = [
        {"url": seg.get("url"), "size": int(seg.get("size") or 0)}
        for seg in ((play_data or {}).get("durl") or [])
        if seg.get("url")
    ]
    if not segments:
        return None

    quality = (play_data or {}).get("quality")
    return {
        "segments": segments,
        "total_size": sum(seg["size"] for seg in segments),
        "quality": quality,
        "quality_name": QUALITY_NAMES.get(quality, str(quality or "未知清晰度")),
        "format": (play_data or {}).get("format") or "",
        "duration": (play_data or {}).get("timelength") or 0,
    }


def pick_page(info: dict, page: int = 1) -> dict:
    """按分 P 序号选页面（含 ``cid``）；越界回落第一 P，无 ``pages`` 时用顶层 ``cid``。

    与参考实现 ``pick_page`` 等价：多 P 稿件的分 P cid 只存在于 ``pages[].cid``，
    顶层 ``cid`` 恒为第 1 P。
    """
    pages = (info or {}).get("pages") or []
    for item in pages:
        if item.get("page") == page:
            return item
    if pages:
        return pages[0]
    return {"cid": (info or {}).get("cid"), "page": 1, "part": (info or {}).get("title")}


def cache_path(cache_dir: str, bvid: str, page: int = 1) -> str:
    """本地缓存文件路径：``<cache_dir>/<bvid>_p<page>.mp4``。"""
    return os.path.join(cache_dir, f"{bvid}_p{int(page or 1)}{VIDEO_EXT}")


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
