"""官方机器人鉴权：AppID + AppSecret → access_token（缓存、提前刷新、失效重取）。

接口事实（2026-11）：

- ``POST https://api.bot.qq.com/app/getAppAccessToken``，body ``{"appId","clientSecret"}``；
- 返回 ``access_token`` / ``expires_in``（7200 秒）；
- 有效期内的重复获取返回同一个值；过期前 60 秒内再取会发新 token；
- 业务错误走 body 的 ``code``，HTTP 仍可能是 200。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.core.logger import logger

TOKEN_URL = "https://api.bot.qq.com/app/getAppAccessToken"
#: 提前刷新窗口（秒）：官方在过期前 60 秒内才会发新 token
REFRESH_AHEAD_SECONDS = 60
#: 拿不到的兜底有效期（避免 expires_in 缺失时把 token 永久钉住）
DEFAULT_TTL_SECONDS = 7200


class TokenError(RuntimeError):
    """取 token 失败（AppID/Secret 错、被封禁、网络异常）。"""


class TokenManager:
    """一个账号一个实例：缓存 access_token，过期前刷新，并发只打一次。"""

    def __init__(self, app_id: str, app_secret: str, *, session: Any, log: Any = None) -> None:
        self.app_id = str(app_id or "").strip()
        self.app_secret = str(app_secret or "").strip()
        self._session = session
        self.log = log or logger
        self._token = ""
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.app_secret)

    async def get(self, *, force: bool = False) -> str:
        """取 token；``force=True`` 用于「服务端说 token 无效」后的重取。"""
        if not self.configured:
            raise TokenError("未配置 AppID / AppSecret")
        async with self._lock:
            if not force and self._token and time.time() < self._expires_at - REFRESH_AHEAD_SECONDS:
                return self._token
            await self._fetch()
            return self._token

    async def invalidate(self) -> None:
        self._token = ""
        self._expires_at = 0.0

    async def _fetch(self) -> None:
        payload = {"appId": self.app_id, "clientSecret": self.app_secret}
        try:
            async with self._session.post(TOKEN_URL, json=payload, timeout=15) as resp:
                data = await resp.json(content_type=None)
                status = resp.status
        except Exception as e:  # noqa: BLE001 —— 网络错误统一成可读的 TokenError
            raise TokenError(f"获取 access_token 失败: {e}") from e

        # 成功：{"access_token": "...", "expires_in": "7200"}
        token = str((data or {}).get("access_token") or "").strip()
        if token:
            try:
                ttl = int((data or {}).get("expires_in") or DEFAULT_TTL_SECONDS)
            except (TypeError, ValueError):
                ttl = DEFAULT_TTL_SECONDS
            self._token = token
            self._expires_at = time.time() + max(60, ttl)
            self.log.debug(f"[QQBot] access_token 已刷新（appid={self.app_id}，ttl={ttl}s）")
            return

        # 失败：业务错误在 body 的 code/message
        code = (data or {}).get("code") or status
        message = (data or {}).get("message") or "未知错误"
        raise TokenError(f"获取 access_token 失败: code={code} {message}")
