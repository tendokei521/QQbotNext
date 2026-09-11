"""合并转发节点构建（纯函数）。

节点格式与 recall_back 插件一致：``{"type": "node", "data": {"name", "uin", "content": [...]}}``。

一条视频对应一条合并转发，内含两个**分开的节点**：

1. 简介节点：标题 / 封面 / UP / 统计 / 简介 / 链接（复用 ``build_video_message``，**不含 reply 段**）；
2. 视频节点：720P mp4（本地文件用 ``file:///`` URI，避免 NapCat 再回源 CDN 直链）；
   取流或下载失败时降级为文本节点（原因 + 链接），简介节点照常发送。
"""

from __future__ import annotations

import os

from .bilibili_api import build_video_message

# 合并转发卡片上显示的发送者名
FORWARD_NAME = "B站解析"
# 无可用 uin 时的兜底（NapCat 要求节点带 uin）
FALLBACK_UIN = "10000"


def file_uri(path: str) -> str:
    """本地路径转 OneBot 可识别的 ``file:///`` URI（Windows / POSIX 通吃）。"""
    posix = os.path.abspath(path).replace("\\", "/").lstrip("/")
    return f"file:///{posix}"


def build_node(content, name: str = FORWARD_NAME, uin=None) -> dict:
    """构造一个合并转发节点。"""
    return {"type": "node", "data": {"name": name, "uin": str(uin or FALLBACK_UIN), "content": content}}


def build_intro_node(info: dict, show_cover: bool = True, name: str = FORWARD_NAME, uin=None) -> dict:
    """简介节点：复用现有文案构建（标题/封面/统计/简介/链接）。"""
    return build_node(build_video_message(info, show_cover), name=name, uin=uin)


def build_video_node(path: str, name: str = FORWARD_NAME, uin=None) -> dict:
    """720P 视频节点（本地 mp4）。"""
    return build_node([{"type": "video", "data": {"file": file_uri(path)}}], name=name, uin=uin)


def build_fallback_node(info: dict, reason: str, name: str = FORWARD_NAME, uin=None) -> dict:
    """取流/下载失败时的文本节点：说明原因 + 视频链接。"""
    bvid = info.get("bvid", "")
    text = f"⚠️ 720P 视频获取失败：{reason}\nhttps://www.bilibili.com/video/{bvid}"
    return build_node([{"type": "text", "data": {"text": text}}], name=name, uin=uin)


def build_forward_nodes(
    info: dict,
    uin=None,
    show_cover: bool = True,
    video_file: str | None = None,
    video_error: str | None = None,
    name: str = FORWARD_NAME,
) -> list[dict]:
    """构建一条视频的合并转发节点：简介节点 + 视频节点（或降级文本节点）。"""
    nodes = [build_intro_node(info, show_cover=show_cover, name=name, uin=uin)]
    if video_file:
        nodes.append(build_video_node(video_file, name=name, uin=uin))
    elif video_error:
        nodes.append(build_fallback_node(info, video_error, name=name, uin=uin))
    return nodes
