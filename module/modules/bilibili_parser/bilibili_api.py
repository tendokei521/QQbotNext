"""B 站 API 操作：链接检测、短链解析、视频信息获取、消息格式化、BV 去重。

- 网络请求走 CurlCffiClient（浏览器指纹模拟，自 fabric_api Bilibili_API 移植）；
- 纯逻辑（正则提取 / 去重 / 格式化）保留为模块级函数。
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse

from app.core.logger import module_logger
from app.infrastructure.curl_cffi import CurlCffiClient

BILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/116.0.0.0 Safari/537.36 Edg/116.0.1938.69"
    ),
    "Referer": "https://www.bilibili.com/",
}

BILIBILI_API_URL = "https://api.bilibili.com/x/web-interface/view"

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


# ==================== API 封装类（网络请求） ====================


class BilibiliAPI(CurlCffiClient):
    """B站 API 封装：短链解析 + 视频信息获取（curl_cffi 浏览器指纹模拟）。

    BV 去重缓存为类级状态（跨实例共享，API 层行为）。
    """

    # BV 去重缓存：{bv: 最近解析时间戳}
    _bv_cache: dict[str, float] = {}

    def __init__(self, impersonate="chrome", proxy: str = "") -> None:
        super().__init__(impersonate=impersonate, proxy=proxy)
        self.headers = dict(BILI_HEADERS)  # 覆盖默认请求头

    async def resolve_short_link(self, url: str, timeout: int = 10) -> str:
        """解析 b23.tv 短链接，返回真实 URL。"""
        if not url.startswith("http"):
            url = "https://" + url
        try:
            resp = await self.GET(url, timeout=timeout)
            return str(resp.url)
        except Exception as e:
            module_logger.debug(f"[BilibiliAPI] 短链解析失败: {e}")
            return url

    async def extract_b23(self, bv_list: list) -> list:
        """把 b23.tv 短链统一解析为 BV 号（其余原样保留）。"""
        b23_list = []
        bili_list = []
        for item in bv_list:
            for match in REGEX_SHORT.finditer(item):
                b23_list.append(match.group().strip())
            if not REGEX_SHORT.match(item):
                bili_list.append(item)
        for b23_url in b23_list:
            bili_list.append(extract_from_direct_link(await self.resolve_short_link(b23_url)))
        return bili_list

    async def get_video_info(self, vid: str, timeout: int = 10, cookie: str = "") -> dict | None:
        """通过 B站 API 获取视频信息。"""
        vid = vid.strip()
        params = {}
        if vid.lower().startswith("bv"):
            params["bvid"] = vid
        elif vid.lower().startswith("av"):
            params["aid"] = vid[2:]
        else:
            return None

        headers = dict(self.headers)
        if cookie:
            headers["Cookie"] = cookie

        try:
            resp = await self.GET(BILIBILI_API_URL, params=params, headers=headers, timeout=timeout)
            data = resp.json()
            return data["data"] if data.get("code") == 0 else None
        except Exception as e:
            module_logger.error(f"[BilibiliAPI] 获取视频信息失败: {e}")
            return None

    @classmethod
    def filter_bv_dedup(cls, video_ids: list, timeout: int) -> list:
        """过滤掉在超时时间内已解析过的 BV 号，并清理过期缓存（类级状态）。"""
        now = time.time()
        fresh_ids = []
        for vid in video_ids:
            last = cls._bv_cache.get(vid)
            if last is not None and (now - last) < timeout:
                module_logger.info(f"[BilibiliAPI] BV {vid} 在 {timeout}s 内已解析，跳过")
                continue
            cls._bv_cache[vid] = now
            fresh_ids.append(vid)

        stale = [k for k, v in cls._bv_cache.items() if (now - v) >= timeout]
        for k in stale:
            del cls._bv_cache[k]
        return fresh_ids


# ==================== 纯逻辑（链接提取 / 去重 / 格式化） ====================


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


def filter_bv_dedup(video_ids: list, timeout: int) -> list:
    """过滤掉在超时时间内已解析过的 BV 号，并清理过期缓存（委托 BilibiliAPI 类级缓存）。"""
    return BilibiliAPI.filter_bv_dedup(video_ids, timeout)


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
