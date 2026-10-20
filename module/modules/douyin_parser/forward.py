"""合并转发节点构建（纯函数）。

节点格式与 bilibili_parser / recall_back 一致：
``{"type": "node", "data": {"name", "uin", "content": [...]}}``。

一条作品对应一条合并转发，内含两个**分开的节点**：

1. 文案节点：文案 / 封面 / 作者 / 时长 / 链接；
2. 媒体节点：视频（本地 mp4）或图集（本地图片，多张放同一节点）；
   取流或下载失败时降级为文本节点（原因 + 链接），文案节点照常发送。
"""

from __future__ import annotations

import os

from app.infrastructure.fs.media_cache import format_duration

#: 合并转发卡片上显示的发送者名
FORWARD_NAME = "抖音解析"
#: 无可用 uin 时的兜底（OneBot 要求节点带 uin）
FALLBACK_UIN = "10000"

#: 文案展示长度上限
DESC_LIMIT = 100


def file_uri(path: str) -> str:
    """本地路径转 OneBot 可识别的 ``file:///`` URI（Windows / POSIX 通吃）。"""
    posix = os.path.abspath(path).replace("\\", "/").lstrip("/")
    return f"file:///{posix}"


def build_node(content, name: str = FORWARD_NAME, uin=None) -> dict:
    """构造一个合并转发节点。"""
    return {"type": "node", "data": {"name": name, "uin": str(uin or FALLBACK_UIN), "content": content}}


def build_intro_segments(meta: dict, show_cover: bool = True) -> list[dict]:
    """作品文案 → OneBot 消息段数组。"""
    kind = meta.get("content_type", "video")
    icon = "🖼" if kind == "image" else "📺"
    desc = (meta.get("desc") or "无文案").replace("\n", " ").strip()
    if len(desc) > DESC_LIMIT:
        desc = desc[:DESC_LIMIT] + "..."

    segments = [{"type": "text", "data": {"text": f"{icon} {desc}\n"}}]

    cover = meta.get("cover_url") or ""
    if show_cover and cover:
        segments.append({"type": "image", "data": {"file": cover}})
        segments.append({"type": "text", "data": {"text": "\n"}})

    lines = [f"作者：{meta.get('author_nickname') or '未知作者'}"]
    if kind == "image":
        lines.append(f"图集：{meta.get('image_count') or 0} 张")
    elif meta.get("duration"):
        lines.append(f"时长：{format_duration(meta['duration'])}")
    lines.append(meta.get("url") or f"https://www.douyin.com/video/{meta.get('aweme_id')}")
    segments.append({"type": "text", "data": {"text": "\n".join(lines)}})
    return segments


def build_intro_node(meta: dict, show_cover: bool = True, name: str = FORWARD_NAME, uin=None) -> dict:
    """文案节点。"""
    return build_node(build_intro_segments(meta, show_cover), name=name, uin=uin)


def build_video_node(path: str, name: str = FORWARD_NAME, uin=None) -> dict:
    """视频节点（本地 mp4）。"""
    return build_node([{"type": "video", "data": {"file": file_uri(path)}}], name=name, uin=uin)


def build_image_node(paths, name: str = FORWARD_NAME, uin=None) -> dict:
    """图集节点（本地图片，多张放同一节点）。"""
    content = [{"type": "image", "data": {"file": file_uri(path)}} for path in paths]
    return build_node(content, name=name, uin=uin)


def build_fallback_node(meta: dict, reason: str, name: str = FORWARD_NAME, uin=None) -> dict:
    """取流/下载失败时的文本节点：说明原因 + 作品链接。"""
    url = meta.get("url") or f"https://www.douyin.com/video/{meta.get('aweme_id')}"
    return build_node([{"type": "text", "data": {"text": f"⚠️ 媒体获取失败：{reason}\n{url}"}}],
                      name=name, uin=uin)


def build_forward_nodes(
    meta: dict,
    uin=None,
    show_cover: bool = True,
    video_file: str | None = None,
    image_files: list | None = None,
    media_error: str | None = None,
    name: str = FORWARD_NAME,
) -> list[dict]:
    """构建一条作品的合并转发节点：文案节点 + 媒体节点（或降级文本节点）。"""
    nodes = [build_intro_node(meta, show_cover=show_cover, name=name, uin=uin)]
    if video_file:
        nodes.append(build_video_node(video_file, name=name, uin=uin))
    elif image_files:
        nodes.append(build_image_node(image_files, name=name, uin=uin))
    elif media_error:
        nodes.append(build_fallback_node(meta, media_error, name=name, uin=uin))
    return nodes
