"""抖音 API 操作：短链归一、作品详情、主页作品列表、流式下载、作品去重。

- 网络请求走 ``CurlCffiClient``（浏览器指纹模拟，见 ``app/infrastructure/curl_cffi.py``）；
- 签名（纯函数）在 ``abogus.py`` / ``xbogus.py``，本文件只负责拼签名后的请求；
- 纯逻辑（链接提取 / 无水印地址整理）在 ``media.py``。

**User-Agent 与指纹的关系**：``a_bogus`` 里烘了一串固定的 UA 特征码（``ABogus.ua_code``），
上游把它与写死的 Chrome/130 UA 配套使用。本插件默认**沿用 curl_cffi 指纹自带的 UA**
（bilibili_parser 的实测结论：写死旧 UA 会造成「指纹新、UA 旧」的矛盾而被风控），
并提供 ``user_agent`` 开关：若线上出现风控，可设成上游那个 UA 逐字复刻上游环境。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from urllib.parse import urlencode

from app.core.logger import module_logger
from app.infrastructure.curl_cffi import CurlCffiClient
from app.modules.media_cache import PART_SUFFIX

from . import media
from .abogus import ABogus
from .xbogus import XBogus

#: 与上游 douyin_parse 一致的 Web 端基础参数（``a_bogus`` 的指纹输入）
BASE_PARAMS = {
    "device_platform": "webapp",
    "aid": "6383",
    "channel": "channel_pc_web",
    "pc_client_type": "1",
    "version_code": "190500",
    "version_name": "19.5.0",
    "cookie_enabled": "true",
    "browser_language": "zh-CN",
    "browser_platform": "Win32",
    "browser_name": "Edge",
    "browser_online": "true",
    "engine_name": "Blink",
    "os_name": "Windows",
    "os_version": "10",
    "platform": "PC",
    "screen_width": "1920",
    "screen_height": "1080",
}

#: 上游配套使用的 UA（``user_agent`` 配置留空时不启用，见模块 docstring）
UPSTREAM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

API_DETAIL = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
API_POST = "https://www.douyin.com/aweme/v1/web/aweme/post/"

#: 抖音接口要求的附加头（UA 由指纹/配置决定，不在这里写死）
DOUYIN_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": "https://www.douyin.com",
    "Referer": "https://www.douyin.com/",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

#: 主页作品一次拉取的条数上限
DEFAULT_PAGE_COUNT = 20
#: 主页最多翻页数（与上游一致的 20 页上限）
MAX_PAGES = 20

#: 下载分块（1 MB）
DOWNLOAD_CHUNK = 1 << 20
#: 下载重试次数（CDN 偶发中断；重试前必须回滚到本片起始偏移，否则重复字节写坏文件）
DOWNLOAD_RETRIES = 3
DOWNLOAD_RETRY_BACKOFF = 1.5


async def _sleep(seconds: float) -> None:
    """退避等待（单独抽出便于测试注入）。"""
    await asyncio.sleep(seconds)


class DouyinAPI(CurlCffiClient):
    """抖音 API 封装：短链归一 + 作品详情 + 主页作品 + 流式下载。

    - 作品去重缓存键含 ``bot_id``（各账号独立去重，互不干扰）；
    - 详情请求先试 ``a_bogus``，失败再试 ``X-Bogus``（与上游同一降级顺序）。
    """

    #: 作品去重缓存：{(bot_id, aweme_id): 最近解析时间戳}
    _aweme_cache: dict[tuple, float] = {}

    def __init__(self, impersonate="chrome", proxy: str = "", user_agent: str = "") -> None:
        super().__init__(impersonate=impersonate, proxy=proxy)
        self.headers.update(DOUYIN_HEADERS)
        if user_agent:
            self.headers["User-Agent"] = user_agent
        self.abogus = ABogus()
        self.xbogus = XBogus(self.headers.get("User-Agent"))

    # ---------- 请求头 ----------

    def _headers(self, referer: str, cookie: str = "") -> dict:
        """按具体作品页拼请求头（Referer 要与内容对得上）。"""
        headers = dict(self.headers)
        headers["Referer"] = referer
        if cookie:
            headers["Cookie"] = cookie
        return headers

    # ---------- 链接归一 ----------

    async def resolve_short_link(self, url: str, timeout: int = 10) -> str:
        """跟随跳转拿到真实 URL（失败返回原 URL，由调用方决定后续）。"""
        try:
            resp = await self.GET(url, timeout=timeout)
            return str(resp.url) or url
        except Exception as e:
            module_logger.debug(f"[DouyinAPI] 短链解析失败: {e}")
            return url

    async def resolve_aweme_id(self, link: str, timeout: int = 10) -> str | None:
        """链接 → ``aweme_id``：直链直接抠；短链跟随跳转；再兜底扫落地页 HTML。"""
        aweme_id = media.aweme_id_from_url(link)
        if aweme_id:
            return aweme_id
        try:
            resp = await self.GET(link, timeout=timeout)
        except Exception as e:
            module_logger.debug(f"[DouyinAPI] 短链解析失败: {e}")
            return None
        aweme_id = media.aweme_id_from_url(str(resp.url))
        if aweme_id:
            return aweme_id
        return media.aweme_id_from_html(getattr(resp, "text", "") or "")

    # ---------- 作品详情 ----------

    async def get_aweme_detail(self, aweme_id: str, timeout: int = 10, cookie: str = "") -> dict | None:
        """取作品详情；``status_code=0`` 但没有 ``aweme_detail`` 视为失败（内容被过滤）。"""
        params = dict(BASE_PARAMS, aweme_id=str(aweme_id))
        headers = self._headers(f"https://www.douyin.com/video/{aweme_id}", cookie)
        data = await self._request_signed(API_DETAIL, params, headers, timeout)
        if not data:
            return None
        return data if data.get("aweme_detail") else None

    async def get_user_aweme_ids(
        self,
        sec_uid: str,
        max_pages: int = 1,
        count: int = DEFAULT_PAGE_COUNT,
        timeout: int = 10,
        cookie: str = "",
    ) -> list[str]:
        """拉取用户主页作品 ID 列表（按 ``max_cursor`` 翻页，去重保序）。"""
        if not sec_uid:
            return []
        headers = self._headers("https://www.douyin.com/", cookie)
        pages = max(1, min(int(max_pages or 1), MAX_PAGES))

        ids: list[str] = []
        seen: set = set()
        max_cursor = 0
        for _ in range(pages):
            params = dict(BASE_PARAMS)
            params.update({
                "sec_user_id": sec_uid,
                "max_cursor": str(max_cursor),
                "count": str(count),
                "locate_query": "false",
                "show_live_replay_strategy": "1",
                "need_time_list": "1",
                "time_list_query": "0",
                "whale_cut_token": "",
                "cut_version": "1",
                "publish_video_strategy_type": "2",
            })
            data = await self._request_signed(API_POST, params, headers, timeout)
            if not data:
                break

            for aweme in data.get("aweme_list") or []:
                aweme_id = (aweme or {}).get("aweme_id")
                if aweme_id and aweme_id not in seen:
                    seen.add(aweme_id)
                    ids.append(str(aweme_id))

            if not data.get("has_more"):
                break
            max_cursor = data.get("max_cursor") or 0

        return ids

    # ---------- 签名请求 ----------

    async def _request_signed(self, api_url: str, params: dict, headers: dict, timeout: int) -> dict | None:
        """先试 ``a_bogus``，失败再试 ``X-Bogus``（与上游同一顺序）。"""
        data = await self._get_signed(api_url, params, headers, timeout, use_abogus=True)
        if data is not None:
            return data
        return await self._get_signed(api_url, params, headers, timeout, use_abogus=False)

    async def _get_signed(
        self, api_url: str, params: dict, headers: dict, timeout: int, use_abogus: bool
    ) -> dict | None:
        """发一次带签名的 GET，返回 ``status_code == 0`` 的响应体，否则 None。"""
        if use_abogus:
            signed_url = ABogus.build_signed_url(api_url, params, self.abogus.get_value(params))
        else:
            signed_path = f"{api_url}?{urlencode(params)}"
            signed_url = self.xbogus.get_xbogus(signed_path)[0]

        try:
            resp = await self.GET(signed_url, headers=headers, timeout=timeout)
            if resp.status_code != 200 or not resp.content:
                return None
            data = resp.json()
        except Exception as e:
            module_logger.debug(f"[DouyinAPI] 请求失败({('a_bogus' if use_abogus else 'X-Bogus')}): {e}")
            return None

        if isinstance(data, dict) and data.get("status_code") == 0:
            return data
        return None

    # ---------- 下载 ----------

    async def download(self, url: str, dest: str, timeout: int = 60, headers: dict | None = None) -> str:
        """把 ``url`` 流式下载到 ``dest``（``.part`` + 原子改名）。

        容错：CDN 偶发中断连接，失败后**回滚到起始偏移**再重试（最多 ``DOWNLOAD_RETRIES`` 次）——
        不回滚直接重试会把重复字节写进文件。整体失败时清理半成品。
        """
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        part = dest + PART_SUFFIX
        with contextlib.suppress(OSError):
            os.remove(part)

        try:
            with open(part, "wb") as fh:
                for attempt in range(1, DOWNLOAD_RETRIES + 1):
                    try:
                        async with self.stream(
                            "GET", url, headers=headers or self.headers, timeout=timeout
                        ) as resp:
                            if resp.status_code not in (200, 206):
                                raise RuntimeError(f"下载失败：HTTP {resp.status_code}")
                            async for chunk in resp.aiter_content(chunk_size=DOWNLOAD_CHUNK):
                                if chunk:
                                    fh.write(chunk)
                        break
                    except Exception as e:
                        if attempt >= DOWNLOAD_RETRIES:
                            raise
                        module_logger.warning(
                            f"[DouyinAPI] 下载中断（{attempt}/{DOWNLOAD_RETRIES}），回滚重试: {e}"
                        )
                        fh.seek(0)
                        fh.truncate(0)
                        await _sleep(DOWNLOAD_RETRY_BACKOFF)
            os.replace(part, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(part)
            raise
        return dest

    # ---------- 去重 ----------

    @classmethod
    def filter_aweme_dedup(cls, aweme_ids: list, timeout: int, bot_id=None) -> list:
        """过滤超时时间内已解析过的作品，并清理过期缓存（键含 bot_id 区分账号）。"""
        now = time.time()
        fresh: list = []
        for aweme_id in aweme_ids:
            key = (bot_id, aweme_id)
            last = cls._aweme_cache.get(key)
            if last is not None and (now - last) < timeout:
                module_logger.info(f"[DouyinAPI] 作品 {aweme_id} 在 {timeout}s 内已解析，跳过")
                continue
            cls._aweme_cache[key] = now
            fresh.append(aweme_id)

        for key in [k for k, v in cls._aweme_cache.items() if (now - v) >= timeout]:
            del cls._aweme_cache[key]
        return fresh
