"""B 站 API 操作：链接检测、短链解析、视频信息获取、消息格式化、BV 去重。

从 astrbot napcat_bilibili_parser 移植，logger 换为框架 module_logger；
新增域名白名单校验与 BV 去重缓存。
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse

import aiohttp

from app.core.logger import module_logger

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/116.0.0.0 Safari/537.36 Edg/116.0.1938.69"
    ),
    "Referer": "https://www.bilibili.com/",
}

# 正则
REGEX_SHORT = re.compile(r"(?:https?://)?b23\.tv/[a-zA-Z0-9]+", re.IGNORECASE)
REGEX_VIDEO = re.compile(r"(BV[a-zA-Z0-9]{10}|av\d+)", re.IGNORECASE)
REGEX_DIRECT_LINK = re.compile(
    r"https?://(?:www\.)?bilibili\.com/video/(BV[a-zA-Z0-9]{10}|av\d+)/?[^\s]*",
    re.IGNORECASE,
)

# B站允许的域名白名单（防止恶意链接/非 B 站域名）
_ALLOWED_DOMAINS = (
    "bilibili.com", "b23.tv", "bilivideo.com", "bilivideo.cn",
    "bilivideo.net", "hdslb.com", "bili2233.cn", "bili22.cn",
    "bili23.cn", "bili33.cn",
)

# BV 去重缓存：{bv: 最近解析时间戳}
_bv_cache: dict[str, float] = {}


def format_number(num: int) -> str:
    """格式化数字（万 / 亿）。"""
    if not isinstance(num, int):
        return "0"
    if num >= 100_000_000:
        return f"{round(num / 100_000_000, 1)}亿"
    if num >= 10_000:
        return f"{round(num / 10_000, 1)}万"
    return str(num)


def _is_allowed_domain(url: str) -> bool:
    """检查 URL 域名是否在 B站白名单内。"""
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        return any(host == d or host.endswith("." + d) for d in _ALLOWED_DOMAINS)
    except Exception:
        return False


def _find_qqdocurl(data) -> str:
    """从已解析的 JSON dict 中查找 B站相关的 qqdocurl。"""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return ""
    if not isinstance(data, dict):
        return ""
    meta = data.get("meta", "")
    if not isinstance(meta, dict):
        return ""
    for _key, val in meta.items():
        if isinstance(val, dict):
            url = val.get("qqdocurl", "") or val.get("url", "")
            if url and _is_allowed_domain(url):
                return url
    return ""


# ==================== 链接提取 ====================


def extract_from_text(raw: list) -> list:
    """从文本片段中提取 B站 URL（直链 / BV号 / b23 短链）。"""
    if raw is None:
        return []
    bv_list = []
    for item in raw:
        for match in REGEX_DIRECT_LINK.finditer(item):
            bv_list.append(match.group(1).strip())
        for match in REGEX_VIDEO.finditer(item):
            bv_list.append(match.group(0).strip())
        for match in REGEX_SHORT.finditer(item):
            bv_list.append(match.group().strip())
    return bv_list


def extract_from_json(raw: list) -> list:
    """从 JSON 段（小程序卡片）中提取 B站 URL。"""
    bv_list = []
    for item in raw:
        qqdocurl = _find_qqdocurl(item)
        if qqdocurl:
            for match in REGEX_SHORT.finditer(qqdocurl):
                bv_list.append(match.group().strip())
    return bv_list


def extract_from_direct_link(raw: str) -> str:
    """从字符串中提取直链 BV 号。"""
    for match in REGEX_DIRECT_LINK.finditer(raw):
        return match.group(1).strip()
    return ""


async def extract_b23(bv_list: list) -> list:
    """把 b23.tv 短链统一解析为 BV 号（其余原样保留）。"""
    b23_list = []
    bili_list = []
    for item in bv_list:
        for match in REGEX_SHORT.finditer(item):
            b23_list.append(match.group().strip())
        if not REGEX_SHORT.match(item):
            bili_list.append(item)
    for b23_url in b23_list:
        bili_list.append(extract_from_direct_link(await resolve_short_link(b23_url)))
    return bili_list


async def resolve_short_link(url: str, timeout: int = 10) -> str:
    """解析 b23.tv 短链接，返回真实 URL。"""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.head(
                url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                return str(resp.url)
    except Exception as e:
        module_logger.debug(f"[BilibiliAPI] 短链解析失败: {e}")
        return url


async def get_video_info(vid: str, timeout: int = 10, cookie: str = "") -> dict | None:
    """通过 B站 API 获取视频信息。"""
    vid = vid.strip()
    params = {}
    if vid.lower().startswith("bv"):
        params["bvid"] = vid
    elif vid.lower().startswith("av"):
        params["aid"] = vid[2:]
    else:
        return None

    headers = dict(HEADERS)
    if cookie:
        headers["Cookie"] = cookie

    url = "https://api.bilibili.com/x/web-interface/view"
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                data = await resp.json()
                return data["data"] if data.get("code") == 0 else None
    except Exception as e:
        module_logger.error(f"[BilibiliAPI] 获取视频信息失败: {e}")
        return None


# ==================== BV 去重 ====================


def filter_bv_dedup(video_ids: list, timeout: int) -> list:
    """过滤掉在超时时间内已解析过的 BV 号，并清理过期缓存。"""
    now = time.time()
    fresh_ids = []
    for vid in video_ids:
        last = _bv_cache.get(vid)
        if last is not None and (now - last) < timeout:
            module_logger.info(f"[BilibiliAPI] BV {vid} 在 {timeout}s 内已解析，跳过")
            continue
        _bv_cache[vid] = now
        fresh_ids.append(vid)

    stale = [k for k, v in _bv_cache.items() if (now - v) >= timeout]
    for k in stale:
        del _bv_cache[k]
    return fresh_ids


# ==================== 消息格式化 ====================


def build_video_message(info: dict, show_cover: bool = True) -> list:
    """将 B站视频信息构建为 OneBot 消息段数组。"""
    bvid = info.get("bvid", "")
    title = info.get("title", "未知标题")
    pic = info.get("pic", "")
    owner_name = info.get("owner", {}).get("name", "未知UP")
    stat = info.get("stat", {})

    desc = info.get("desc", "") or "无简介"
    desc = desc.replace("\n", " ")
    if len(desc) > 100:
        desc = desc[:100] + "..."

    segments = [{"type": "text", "data": {"text": f"📺 {title}\n"}}]

    # 封面图
    if show_cover and pic:
        if pic.startswith("http"):
            segments.append({"type": "image", "data": {"file": pic}})
        elif pic.startswith("//"):
            segments.append({"type": "image", "data": {"file": f"https:{pic}"}})
        segments.append({"type": "text", "data": {"text": "\n"}})

    info_text = (
        f"UP主：{owner_name}\n"
        f"点赞：{format_number(stat.get('like', 0))}  |  收藏：{format_number(stat.get('favorite', 0))}\n"
        f"投币：{format_number(stat.get('coin', 0))}  |  转发：{format_number(stat.get('share', 0))}\n"
        f"播放：{format_number(stat.get('view', 0))}  |  弹幕：{format_number(stat.get('danmaku', 0))}\n"
        f"简介：{desc}\n"
        f"https://www.bilibili.com/video/{bvid}"
    )
    segments.append({"type": "text", "data": {"text": info_text}})
    return segments
