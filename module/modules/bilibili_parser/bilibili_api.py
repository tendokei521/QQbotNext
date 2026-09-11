"""B 站 API 操作：链接检测、短链解析、视频信息获取、播放地址、消息格式化、BV 去重。

- 网络请求走 CurlCffiClient（浏览器指纹模拟，自 fabric_api Bilibili_API 移植）；
- WBI 签名（纯函数）在 wbi.py，本文件只负责取密钥与签名后请求；
- 纯逻辑（正则提取 / 去重 / 格式化）保留为模块级函数。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
import urllib.parse

from app.core.logger import module_logger
from app.infrastructure.curl_cffi import CurlCffiClient
from . import wbi
from .video import DEFAULT_QN, HEIGHT_TO_QN, QUALITY_NAMES  # noqa: F401（对插件内部复用）

BILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/116.0.0.0 Safari/537.36 Edg/116.0.1938.69"
    ),
    "Referer": "https://www.bilibili.com/",
}

BILIBILI_API_URL = "https://api.bilibili.com/x/web-interface/view"
BILIBILI_API_NAV = "https://api.bilibili.com/x/web-interface/nav"
BILIBILI_API_PLAYURL_WBI = "https://api.bilibili.com/x/player/wbi/playurl"

# playurl 参数：fnval=0 请求「单文件」模式 —— 实测返回音视频合一的 mp4
# （ftypisom，含 vide+soun 双轨），**无需 ffmpeg 合并**，且匿名可达 720P。
FNVAL_SINGLE = 0

# 下载分块大小（1 MB）
DOWNLOAD_CHUNK = 1 << 20

# playurl 风控重试：实测该接口有**按请求随机命中**的风控（约 50%，与是否重复请求同一视频无关），
# 命中时返回 code=0 但 data={"v_voucher": ...}（无 durl），风控窗口约 5~6 秒。
# 因此退避间隔必须跨越该窗口：4 次尝试（t≈0 / +2 / +7 / +17s），最坏多等 17 秒。
# 该链路跑在后台任务里，且结果落盘缓存，同一视频只付一次代价。
PLAYURL_RETRIES = 4
PLAYURL_BACKOFF = (2.0, 5.0, 10.0)


async def _sleep(seconds: float) -> None:
    """退避等待（单独抽出便于测试注入）。"""
    await asyncio.sleep(seconds)


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
    """B站 API 封装：短链解析 + 视频信息 + 播放地址（curl_cffi 浏览器指纹模拟）。

    - BV 去重缓存键含 bot_id（各账号独立去重，互不干扰）；
    - WBI 密钥（nav）同一天内固定，类级缓存 6 小时，避免每次取流都打一次 nav。
    """

    # BV 去重缓存：{(bot_id, bv): 最近解析时间戳}
    _bv_cache: dict[tuple, float] = {}
    # WBI 密钥缓存：{"key": mixin_key, "ts": 获取时间戳}
    _wbi_cache: dict = {}
    _WBI_TTL = 6 * 3600

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

    async def get_mixin_key(self, timeout: int = 10, force: bool = False) -> str:
        """获取（并类级缓存）WBI mixin_key。

        ``nav`` 未登录返回 ``code=-101``，但 ``data.wbi_img`` 仍带有效密钥 ——
        因此这里**忽略业务错误码**，只取密钥；取不到才抛异常。
        """
        cached = self._wbi_cache
        if not force and cached.get("key") and (time.time() - cached.get("ts", 0)) < self._WBI_TTL:
            return cached["key"]

        resp = await self.GET(BILIBILI_API_NAV, headers=self.headers, timeout=timeout)
        payload = resp.json() or {}
        wbi_img = (payload.get("data") or {}).get("wbi_img") or {}
        img_url, sub_url = wbi_img.get("img_url"), wbi_img.get("sub_url")
        if not img_url or not sub_url:
            raise RuntimeError(f"nav 接口未返回 wbi_img 密钥: code={payload.get('code')}")

        key = wbi.extract_keys(img_url, sub_url)
        self.__class__._wbi_cache = {"key": key, "ts": time.time()}
        module_logger.debug("[BilibiliAPI] 已刷新 WBI 密钥")
        return key

    async def get_playurl_single(
        self,
        bvid: str,
        cid: int,
        qn: int = DEFAULT_QN,
        cookie: str = "",
        timeout: int = 10,
    ) -> dict | None:
        """取单文件播放地址（``fnval=0``，实测返回音视频合一的 mp4）。

        返回接口 ``data`` 字段（含 ``durl`` / ``quality`` / ``format`` / ``timelength``）。

        三种异常返回的处理：
        - ``-403``：视为 WBI 密钥轮换 → 强制刷新密钥后立即重试；
        - ``code=0`` 但只有 ``v_voucher``（风控）：数秒内自行解除 → 退避重试；
        - 其它错误码：直接失败返回 None。
        重试耗尽仍失败返回 None（调用方降级为文本提示）。
        """
        base = {"bvid": bvid, "cid": cid, "qn": qn, "fnval": FNVAL_SINGLE, "fourk": 1}
        headers = dict(self.headers)
        if cookie:
            headers["Cookie"] = cookie

        refresh_key = False
        for attempt in range(1, PLAYURL_RETRIES + 1):
            try:
                key = await self.get_mixin_key(timeout=timeout, force=refresh_key)
                refresh_key = False
                query = wbi.signed_query(base, key)
                resp = await self.GET(f"{BILIBILI_API_PLAYURL_WBI}?{query}", headers=headers, timeout=timeout)
                payload = resp.json() or {}
            except Exception as e:
                module_logger.error(f"[BilibiliAPI] 获取播放地址失败: {e}")
                return None

            code = payload.get("code")
            data = payload.get("data") or {}
            if code == 0 and data.get("durl"):
                return data
            if code == -403:
                module_logger.warning("[BilibiliAPI] playurl 返回 -403，刷新 WBI 密钥后重试")
                refresh_key = True
                continue
            if code != 0:
                module_logger.warning(f"[BilibiliAPI] playurl 失败: {code} {payload.get('message')}")
                return None

            # code=0 但拿不到 durl：风控（v_voucher）或降级返回，退避后重试
            reason = "风控校验 v_voucher" if "v_voucher" in data else f"无 durl（data={sorted(data)[:3]}）"
            if attempt >= PLAYURL_RETRIES:
                module_logger.error(f"[BilibiliAPI] playurl 重试 {PLAYURL_RETRIES} 次仍失败：{reason}")
                return None
            delay = PLAYURL_BACKOFF[min(attempt - 1, len(PLAYURL_BACKOFF) - 1)]
            module_logger.warning(f"[BilibiliAPI] playurl {reason}，{delay}s 后重试（{attempt}/{PLAYURL_RETRIES}）")
            await _sleep(delay)
        return None

    async def download_durl(self, segments: list, dest: str, timeout: int = 60) -> str:
        """把 ``durl`` 各分片顺序下载到 ``dest``（流式 + ``.part`` 原子改名）。

        单文件模式多数稿件只有 1 段，长视频可能多段：按顺序写入同一文件即为完整 mp4。
        失败时清理半成品，避免残留被误当成完整文件。
        """
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        part = dest + ".part"
        with contextlib.suppress(OSError):
            os.remove(part)

        try:
            with open(part, "wb") as fh:
                for seg in segments or []:
                    url = (seg or {}).get("url")
                    if not url:
                        continue
                    async with self.stream("GET", url, headers=self.headers, timeout=timeout) as resp:
                        if resp.status_code not in (200, 206):
                            raise RuntimeError(f"下载失败：HTTP {resp.status_code}")
                        async for chunk in resp.aiter_content(chunk_size=DOWNLOAD_CHUNK):
                            if chunk:
                                fh.write(chunk)
            os.replace(part, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(part)
            raise
        return dest

    @classmethod
    def filter_bv_dedup(cls, video_ids: list, timeout: int, bot_id=None) -> list:
        """过滤掉在超时时间内已解析过的 BV 号，并清理过期缓存（键含 bot_id 区分账号）。"""
        now = time.time()
        fresh_ids = []
        for vid in video_ids:
            key = (bot_id, vid)
            last = cls._bv_cache.get(key)
            if last is not None and (now - last) < timeout:
                module_logger.info(f"[BilibiliAPI] BV {vid} 在 {timeout}s 内已解析，跳过")
                continue
            cls._bv_cache[key] = now
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


def filter_bv_dedup(video_ids: list, timeout: int, bot_id=None) -> list:
    """过滤空串 + 过滤超时时间内已解析过的 BV 号（委托 BilibiliAPI，按 bot_id 独立去重）。"""
    video_ids = [v for v in video_ids if v and v.strip()]
    return BilibiliAPI.filter_bv_dedup(video_ids, timeout, bot_id=bot_id)


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
