"""单文件（720P mp4）取流结果整理与本地缓存策略（纯逻辑，不碰网络）。

- ``pick_single``：把 playurl ``fnval=0`` 的返回整理成可下载的分片列表；
- ``cache_path``：本地缓存命名；
- 缓存淘汰策略（TTL + 容量上限）已提取为共享库 ``app/infrastructure/fs/media_cache.py``。
"""

from __future__ import annotations

import os

# 单文件模式实测返回 mp4（ftypisom，含 vide+soun 双轨），故按 .mp4 命名
VIDEO_EXT = ".mp4"

# 清晰度代码 -> 名称（部分高清晰度需登录/大会员）
QUALITY_NAMES = {
    6: "240P", 16: "360P", 32: "480P", 64: "720P", 74: "720P60",
    80: "1080P", 112: "1080P+", 116: "1080P60", 120: "4K", 125: "HDR", 127: "8K",
}

# 配置里的分辨率档位 -> B站清晰度代码 qn
HEIGHT_TO_QN = {"360": 16, "480": 32, "720": 64}
DEFAULT_QN = 64


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
