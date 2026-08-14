"""curl_cffi HTTP 客户端封装（自 fabric_api/aiohttp/classes/curlcffi.py 移植，裁剪）。

- 浏览器指纹模拟（impersonate="chrome"），自动管理 Cookie；
- 提供 GET / GET_BINARY / POST，支持自定义 headers / proxy / timeout；
- 裁剪掉原封装的 SOUP（bs4）、SAVE / GATHER 等本项目暂不需要的能力。

用法（API 封装类继承本类）：

    async with BilibiliAPI() as api:
        info = await api.get_video_info(bvid)
"""

from __future__ import annotations

from typing import Any, Literal

from curl_cffi import AsyncSession

# 通用浏览器请求头（各 API 封装类可按需覆盖）
DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0"
    ),
}

Impersonate = Literal["chrome", "chrome99", "chrome100", "chrome101"] | None


class CurlCffiClient:
    """基于 curl_cffi.AsyncSession 的请求客户端（浏览器指纹模拟 + Cookie 自动管理）。"""

    def __init__(self, impersonate: Impersonate = "chrome", proxy: str = "") -> None:
        self.proxy = proxy
        self.headers = dict(DEFAULT_HEADERS)
        self.impersonate = impersonate
        self.session: AsyncSession | None = None

    # ── 生命周期 ──────────────────────────────────────────────

    async def __aenter__(self) -> "CurlCffiClient":
        self.session = AsyncSession(impersonate=self.impersonate)
        await self.session.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    # ── 请求 ──────────────────────────────────────────────────

    async def GET(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        timeout: int = 60,
    ):
        """GET 请求，返回 curl_cffi Response（.json() / .text / .status_code / .url）。"""
        session = self._require_session()
        return await session.get(
            url,
            impersonate=self.impersonate,
            proxy=self.proxy,
            params=params,
            headers=headers or self.headers,
            timeout=timeout,
        )

    async def GET_BINARY(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 60,
    ):
        """GET 请求，返回响应（调用方取 .content 二进制）。"""
        session = self._require_session()
        return await session.get(
            url,
            impersonate=self.impersonate,
            proxy=self.proxy,
            headers=headers or self.headers,
            timeout=timeout,
        )

    async def POST(
        self,
        url: str,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        timeout: int = 60,
    ):
        """POST 请求：data 为表单（x-www-form-urlencoded），json 为 JSON 体。"""
        session = self._require_session()
        return await session.post(
            url,
            impersonate=self.impersonate,
            proxy=self.proxy,
            params=params,
            data=data,
            json=json,
            headers=headers or self.headers,
            timeout=timeout,
        )

    # ── 内部 ──────────────────────────────────────────────────

    def _require_session(self) -> AsyncSession:
        if self.session is None:
            raise RuntimeError("CurlCffiClient 未进入上下文（请用 async with 使用）")
        return self.session
