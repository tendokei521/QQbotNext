"""抖音链接与媒体信息整理（纯逻辑，不碰网络）。

职责边界：本文件只做「字符串进、字符串/字典出」的整理，不做任何请求 ——
网络在 ``douyin_api.py``，编排在 ``service.py``，转发节点在 ``forward.py``。

覆盖：
- 从文本/QQ 小程序卡片里提取抖音链接（短链 / 直链 / 图集 / 主页）；
- 从链接或落地页 HTML 里提取 ``aweme_id`` / ``sec_uid``；
- 从 ``aweme_detail`` 里整理**无水印**视频地址与图集地址；
- 缓存文件命名与扩展名推断。
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse

# ---------------- 链接识别 ----------------

#: 短链（v.douyin.com/xxxxx）
REGEX_SHORT = re.compile(r"(?:https?://)?v\.douyin\.com/[A-Za-z0-9_\-]+/?", re.IGNORECASE)
#: 直链：视频 / 图集 / aweme detail
REGEX_DIRECT = re.compile(
    r"https?://(?:www\.|m\.)?douyin\.com/(?:video|note|aweme/detail)/(\d+)",
    re.IGNORECASE,
)
#: 分享页（iesdouyin）
REGEX_IES = re.compile(
    r"https?://(?:www\.)?iesdouyin\.com/share/(?:video|note)/(\d+)",
    re.IGNORECASE,
)
#: 用户主页
REGEX_USER = re.compile(
    r"https?://(?:www\.|m\.)?douyin\.com/user/([A-Za-z0-9_\-]+)",
    re.IGNORECASE,
)
#: 任意 http(s) URL（用于从文本里捞出候选短链）
REGEX_ANY_URL = re.compile(r"https?://[^\s\u3000\"'<>]+", re.IGNORECASE)
#: 无协议的短链（``v.douyin.com/xxx``）：QQ 转发常把 ``https://`` 吃掉；
#: 负向后顾避免重复匹配完整 URL 里的同一段。
REGEX_BARE_SHORT = re.compile(r"(?<![\w./])v\.douyin\.com/[A-Za-z0-9_\-]+/?", re.IGNORECASE)
#: URL 路径里的 aweme_id
REGEX_AWEME_IN_URL = re.compile(r"/(?:video|note|aweme/detail)/(\d+)")
#: 落地页 HTML 里的 aweme_id（多种字段名，ID 通常 19 位）
REGEX_AWEME_IN_HTML = re.compile(
    r'"(?:aweme_id|itemId|video_id|note_id)"\s*:\s*"?(\d{15,})"?'
)

#: 允许的域名（防止恶意链接把请求引到任意主机）
_ALLOWED_DOMAINS = ("douyin.com", "iesdouyin.com", "douyin.com.cn")


def is_allowed_url(url: str) -> bool:
    """URL 域名是否属于抖音（含子域）。"""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return any(host == d or host.endswith("." + d) for d in _ALLOWED_DOMAINS)


def _normalize(url: str) -> str:
    """补协议、去尾部标点（分享文案里链接后面常跟中文标点）。"""
    url = url.strip().strip("，。、；：！？）】》\"'")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def extract_links(texts) -> list[str]:
    """从文本片段里提取抖音链接（保序去重）。

    只返回抖音域名的链接；短链与直链都返回**原样链接**，由 API 层再解析成 aweme_id。
    同时接受带协议与不带协议（``v.douyin.com/xxx``）两种写法。
    """
    found: list[str] = []
    for text in texts or []:
        if not isinstance(text, str):
            continue
        # 两种写法合并后按出现位置排序，保证与文案里的先后顺序一致
        matches = [(m.start(), m.group(0)) for m in REGEX_ANY_URL.finditer(text)]
        matches += [(m.start(), m.group(0)) for m in REGEX_BARE_SHORT.finditer(text)]
        for _pos, candidate in sorted(matches):
            url = _normalize(candidate)
            if not is_allowed_url(url):
                continue
            # 只保留「短链 / 直链 / 图集 / 主页」，避免把其它抖音页面当作品解析
            if not (REGEX_SHORT.match(url) or REGEX_DIRECT.match(url)
                    or REGEX_IES.match(url) or REGEX_USER.match(url)):
                continue
            if url not in found:
                found.append(url)
    return found


def _find_qqdocurl(data) -> str:
    """从（可能是 JSON 字符串的）小程序卡片里找抖音链接。"""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return ""
    if not isinstance(data, dict):
        return ""
    meta = data.get("meta")
    if not isinstance(meta, dict):
        return ""
    for value in meta.values():
        if isinstance(value, dict):
            url = value.get("qqdocurl") or value.get("url") or ""
            if url and is_allowed_url(url):
                return url
    return ""


def extract_from_json(parts) -> list[str]:
    """从 QQ 小程序卡片 JSON 段里提取抖音链接。"""
    found: list[str] = []
    for part in parts or []:
        url = _find_qqdocurl(part)
        if url and url not in found:
            found.append(url)
    return found


def aweme_id_from_url(url: str) -> str | None:
    """从直链/落地页 URL 里提取 ``aweme_id``（短链需先跟随跳转，见 API 层）。"""
    for regex in (REGEX_AWEME_IN_URL, REGEX_DIRECT, REGEX_IES):
        match = regex.search(url or "")
        if match:
            return match.group(1)
    return None


def aweme_id_from_html(html: str) -> str | None:
    """从落地页 HTML 里提取 ``aweme_id``（短链跳转后仍拿不到 ID 时兜底）。"""
    match = REGEX_AWEME_IN_HTML.search(html or "")
    return match.group(1) if match else None


def sec_uid_from_url(url: str) -> str | None:
    """从用户主页链接里提取 ``sec_uid``。"""
    match = REGEX_USER.search(url or "")
    return match.group(1) if match else None


# ---------------- 内容类型与无水印地址 ----------------

#: 图集类型（2=图集，68=Live 图集）；其余按视频处理
_IMAGE_TYPES = (2, 68)


def content_type(data: dict) -> str:
    """判定作品类型：``"video"`` 或 ``"image"``（图集）。

    先看有没有 ``images``（图集 / Live 图的硬特征），再看 ``aweme_type`` ——
    只按 ``aweme_type`` 判断时，缺字段的响应会被误判成视频而整条丢弃。
    """
    aweme = (data or {}).get("aweme_detail") or {}
    if aweme.get("images"):
        return "image"
    if aweme.get("aweme_type") in _IMAGE_TYPES:
        return "image"
    return "video"


def _strip_watermark(url: str) -> str:
    """``playwm`` → ``play``，并去掉水印相关查询参数（``?`` / ``&`` 两种写法都要认）。"""
    clean = (url or "").replace("playwm", "play")
    return re.split(r"[?&](?:watermark|logo_name)=", clean)[0]


def _ratio_from_text(*candidates) -> str:
    """从 ``quality_type`` / ``gear_name`` 里抠出 ``720p`` 这类档位。"""
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = candidate.get("name", "")
        match = re.search(r"(\d+p)", str(candidate or "").lower())
        if match:
            return match.group(1)
    return ""


def _ratio_from_bitrate(bit_rate: int) -> str:
    """没有档位文本时按码率粗估（与上游同一口径）。"""
    if bit_rate >= 2_000_000:
        return "1080p"
    if bit_rate >= 1_000_000:
        return "720p"
    if bit_rate >= 500_000:
        return "540p"
    return "480p"


#: 档位排序权重（用于「最高清晰度优先」）
_RATIO_ORDER = {"1080p": 5, "720p": 4, "540p": 3, "480p": 2, "360p": 1}


def extract_video_qualities(data: dict) -> list[dict]:
    """整理全部可用清晰度（最高在前）。

    依次尝试：``video.bit_rate[]`` → ``video.play_addr.url_list`` → 由 ``uri`` 拼装。
    """
    aweme = (data or {}).get("aweme_detail") or {}
    video = aweme.get("video") or {}
    play_addr = video.get("play_addr") or {}
    url_list = play_addr.get("url_list") or []

    qualities: list[dict] = []
    for info in video.get("bit_rate") or []:
        if not isinstance(info, dict):
            continue
        urls = (info.get("play_addr") or {}).get("url_list") or []
        if not urls:
            continue
        bit_rate = int(info.get("bit_rate") or 0)
        ratio = (
            _ratio_from_text(info.get("quality_type"), info.get("gear_name"))
            or _ratio_from_bitrate(bit_rate)
        )
        label = f"{ratio} ({bit_rate // 1000}Kbps)" if bit_rate else ratio
        for url in urls:
            qualities.append({
                "url": _strip_watermark(url),
                "ratio": ratio,
                "bit_rate": bit_rate,
                "quality_label": label,
                "gear_name": info.get("gear_name") or "",
            })

    if not qualities and url_list:
        for url in url_list:
            match = re.search(r"ratio=(\d+p)", str(url).lower())
            ratio = match.group(1) if match else ""
            qualities.append({
                "url": _strip_watermark(url),
                "ratio": ratio or "1080p",
                "bit_rate": 0,
                "quality_label": ratio or "1080p",
                "gear_name": "",
            })

    uri = play_addr.get("uri")
    if not qualities and uri:
        for ratio in ("1080p", "720p", "540p", "480p", "360p"):
            qualities.append({
                "url": f"https://aweme.snssdk.com/aweme/v1/play/?video_id={uri}&ratio={ratio}&line=0",
                "ratio": ratio,
                "bit_rate": 0,
                "quality_label": ratio,
                "gear_name": "",
            })

    seen: set = set()
    unique: list[dict] = []
    for item in qualities:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        unique.append(item)
    unique.sort(key=lambda q: (q["bit_rate"], _RATIO_ORDER.get(q["ratio"], 0)), reverse=True)
    return unique


def extract_nwm_url(data: dict) -> str | None:
    """取最高清晰度的无水印直链（无可用地址返回 None）。"""
    qualities = extract_video_qualities(data)
    return qualities[0]["url"] if qualities else None


def _is_animated_image(img: dict) -> bool:
    """判定是否为动图 / Live 图（这类要走 ``video`` 字段拿 mp4）。"""
    return bool(
        img.get("video")
        or img.get("live_photo_type") == 1
        or img.get("clip_type") == 5
        or img.get("is_animated") in (True, 1)
        or img.get("animated") in (True, 1)
    )


def _image_urls(img: dict) -> tuple[list[str], list[str]]:
    """单张图 → ``(无水印候选, 带水印候选)``。

    Live 图优先取 ``video.play_addr``（mp4）；静态图取 ``url_list``；
    再兜底 ``download_url_list``（可能带水印）。
    """
    clean: list[str] = []
    watermark: list[str] = []

    if _is_animated_image(img):
        video = img.get("video") or {}
        for key in ("play_addr", "download_addr"):
            urls = (video.get(key) or {}).get("url_list") or []
            if urls:
                clean.append(_strip_watermark(urls[0]))
                break

    if not clean:
        for key in ("animated_url_list", "gif_url_list", "live_url_list", "motion_url_list"):
            urls = img.get(key) or []
            if isinstance(urls, str):
                urls = [urls]
            clean.extend(u for u in urls if isinstance(u, str) and u)
        for key in ("animated_url", "gif_url", "live_url", "motion_url"):
            value = img.get(key)
            if isinstance(value, str) and value:
                clean.append(value)

    if not clean:
        urls = img.get("url_list") or []
        clean.extend(u for u in urls[:1] if isinstance(u, str) and u)

    download = img.get("download_url_list") or []
    watermark.extend(u for u in download[:1] if isinstance(u, str) and u)
    return clean, watermark


def extract_image_data(data: dict) -> dict | None:
    """整理图集地址（无图集返回 None）。

    优先级：Live 图（mp4）> 静态图 > 带水印图；同图集内按「去掉查询串」去重。
    """
    aweme = (data or {}).get("aweme_detail") or {}
    images = aweme.get("images") or []
    if not images:
        return None

    live_urls: list[str] = []
    static_urls: list[str] = []
    watermark_urls: list[str] = []
    for img in images:
        if not isinstance(img, dict):
            continue
        clean, watermark = _image_urls(img)
        if _is_animated_image(img) and clean:
            live_urls.extend(clean)
        else:
            static_urls.extend(clean)
        watermark_urls.extend(watermark)

    if live_urls:
        final_urls, is_live = live_urls, True
    elif static_urls:
        final_urls, is_live = static_urls, False
    elif watermark_urls:
        final_urls, is_live = watermark_urls, False
    else:
        return None

    seen: set = set()
    deduplicated: list[str] = []
    for url in final_urls:
        key = url.split("?")[0]
        if key not in seen:
            seen.add(key)
            deduplicated.append(url)
    if not deduplicated:
        return None

    return {
        "image_urls": deduplicated,
        "image_urls_watermark": watermark_urls,
        "image_count": len(deduplicated),
        "preview_url": deduplicated[0],
        "is_live": is_live,
    }


def build_meta(data: dict) -> dict:
    """整理作品元信息（文案 / 作者 / 封面 / 时长 / 类型）。"""
    aweme = (data or {}).get("aweme_detail") or {}
    author = aweme.get("author") or {}
    video = aweme.get("video") or {}
    cover_list = (video.get("cover") or {}).get("url_list") or []

    kind = content_type(data)
    meta = {
        "aweme_id": aweme.get("aweme_id"),
        "desc": aweme.get("desc") or "",
        "create_time": aweme.get("create_time"),
        "author_nickname": author.get("nickname") or "",
        "author_sec_uid": author.get("sec_uid") or "",
        "cover_url": cover_list[0] if cover_list else None,
        "content_type": kind,
        "duration": video.get("duration") or 0,
        "url": f"https://www.douyin.com/video/{aweme.get('aweme_id')}",
    }
    if kind == "image":
        image_data = extract_image_data(data)
        if image_data:
            meta["image_count"] = image_data["image_count"]
            meta["image_urls"] = image_data["image_urls"]
            meta["is_live"] = image_data["is_live"]
            meta["cover_url"] = meta["cover_url"] or image_data["preview_url"]
    return meta


# ---------------- 缓存命名 ----------------

#: 图片扩展名白名单（不在其中一律按 .jpg 存）
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
VIDEO_EXT = ".mp4"


def guess_ext(url: str, default: str = ".jpg") -> str:
    """按 URL 路径推断扩展名（抖音图集常见 .jpeg/.webp，Live 图是 .mp4）。"""
    path = urllib.parse.urlparse(url or "").path.lower()
    for ext in (".mp4",) + _IMAGE_EXTS:
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return default


def video_cache_path(cache_dir: str, aweme_id: str) -> str:
    """视频缓存路径：``<cache_dir>/<aweme_id>.mp4``。"""
    return os.path.join(cache_dir, f"{aweme_id}{VIDEO_EXT}")


def album_cache_path(cache_dir: str, aweme_id: str, index: int, url: str = "") -> str:
    """图集缓存路径：``<cache_dir>/<aweme_id>_<序号>.<ext>``（序号从 1 起，便于排序）。"""
    return os.path.join(cache_dir, f"{aweme_id}_{int(index):03d}{guess_ext(url)}")
