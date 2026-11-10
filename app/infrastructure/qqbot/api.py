"""官方机器人 OpenAPI 客户端（HTTPS）。

只做四件事：拼鉴权头、发请求、把错误归一成 ``QqBotAPIError``、token 失效时重取一次。
业务逻辑（被动窗口、去重、事件解码）不在这里，避免这一层变成第二个网关。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger

from .auth import TokenError, TokenManager

#: 统一请求地址（2026-08-10 起；旧文档里的 api.sgroup.qq.com 已过时）
API_BASE = "https://api.bot.qq.com"
DEFAULT_TIMEOUT = 15.0

#: 这些错误码说明 token 无效/过期 → 重取一次再试
_AUTH_CODES = frozenset({401, 11241, 11242, 11243})
#: 限频类错误（调用方决定退避；这里只标记）
_RATE_LIMIT_CODES = frozenset({429, 22009, 1100100})
#: 被动回复的 msg_id 过期/错误（不能重试同一条被动发送）
_PASSIVE_EXPIRED_CODES = frozenset({304026, 304027, 304028, 40034005})


class QqBotAPIError(RuntimeError):
    """OpenAPI 业务失败（HTTP 可能仍是 200，错误在 body 的 err_code）。"""

    def __init__(
        self,
        code: int | str,
        message: str,
        *,
        http_status: int = 0,
        path: str = "",
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.message = str(message or "")
        self.http_status = int(http_status or 0)
        self.path = str(path or "")
        self.retryable = bool(retryable)
        super().__init__(f"qqbot api error code={code} {self.message}".strip())

    @property
    def passive_expired(self) -> bool:
        try:
            return int(self.code) in _PASSIVE_EXPIRED_CODES
        except (TypeError, ValueError):
            return False


class QqBotAPI:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        session: Any,
        base: str = API_BASE,
        log: Any = None,
    ) -> None:
        self.app_id = str(app_id or "").strip()
        self.app_secret = str(app_secret or "").strip()
        self.base = base.rstrip("/")
        self._session = session
        self.log = log or logger
        self.tokens = TokenManager(self.app_id, self.app_secret, session=session, log=self.log)

    @property
    def configured(self) -> bool:
        return self.tokens.configured

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        _retry_auth: bool = True,
    ) -> dict:
        """发一次 OpenAPI 请求；成功返回 body，失败抛 ``QqBotAPIError``。"""
        if not self.configured:
            raise QqBotAPIError("not_configured", "未配置 AppID / AppSecret", path=path)
        try:
            token = await self.tokens.get()
        except TokenError as e:
            raise QqBotAPIError("token_error", str(e), path=path, retryable=True) from e

        url = self.base + (path if path.startswith("/") else "/" + path)
        headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json; charset=utf-8"}
        try:
            async with self._session.request(
                method.upper(), url, json=json, params=params, headers=headers, timeout=timeout
            ) as resp:
                status = resp.status
                try:
                    data = await resp.json(content_type=None)
                except Exception:  # noqa: BLE001 —— 204/非 JSON 视为空 body
                    data = {}
        except Exception as e:  # noqa: BLE001 —— 网络错误统一成可重试错误
            raise QqBotAPIError("network", f"{method} {path} 失败: {e}", path=path, retryable=True) from e

        data = data if isinstance(data, dict) else {}
        code = data.get("err_code", data.get("code", 0))
        try:
            numeric_code = int(code or 0)
        except (TypeError, ValueError):
            numeric_code = 0
        if numeric_code in _AUTH_CODES and _retry_auth:
            await self.tokens.invalidate()
            return await self.request(
                method, path, json=json, params=params, timeout=timeout, _retry_auth=False
            )
        if status >= 400 or numeric_code:
            raise QqBotAPIError(
                numeric_code or status,
                str(data.get("message") or data.get("msg") or "请求失败"),
                http_status=status,
                path=path,
                retryable=status == 429 or numeric_code in _RATE_LIMIT_CODES,
            )
        return data

    # ==================== 常用请求 ====================

    async def send_group_message(self, group_openid: str, payload: dict) -> dict:
        return await self.request("POST", f"/v2/groups/{group_openid}/messages", json=payload)

    async def send_c2c_message(self, user_openid: str, payload: dict) -> dict:
        return await self.request("POST", f"/v2/users/{user_openid}/messages", json=payload)

    async def delete_group_message(self, group_openid: str, message_id: str) -> dict:
        return await self.request("DELETE", f"/v2/groups/{group_openid}/messages/{message_id}")

    async def delete_c2c_message(self, user_openid: str, message_id: str) -> dict:
        return await self.request("DELETE", f"/v2/users/{user_openid}/messages/{message_id}")

    async def gateway_url(self) -> str:
        data = await self.request("GET", "/gateway")
        url = str(data.get("url") or "").strip()
        if not url:
            raise QqBotAPIError("no_gateway", "官方未返回 WebSocket 接入点", path="/gateway")
        return url

    # ==================== 群管理（官方 2026 新增，部分内邀） ====================

    async def group_info(self, group_openid: str) -> dict:
        return await self.request("GET", f"/v2/groups/{group_openid}/info")

    async def group_members(self, group_openid: str, cursor: str = "") -> dict:
        return await self.request(
            "GET", f"/v2/groups/{group_openid}/members", params={"cursor": cursor}
        )

    async def restrict_chat(self, group_openid: str, members: list[dict]) -> dict:
        return await self.request(
            "POST", f"/v2/groups/{group_openid}/restrict_chat_setting", json={"members": members}
        )

    async def batch_remove_members(
        self, group_openid: str, openids: list[str], *, blacklist: bool = False
    ) -> dict:
        return await self.request(
            "POST", f"/v2/groups/{group_openid}/batch_remove_members",
            json={"member_openids": list(openids), "add_to_member_blacklist": bool(blacklist)},
        )

    # ==================== 富媒体上传 ====================

    @staticmethod
    def scene_base(scene: str, target: str) -> str:
        base = f"/v2/groups/{target}" if scene == "group" else f"/v2/users/{target}"
        return base

    async def upload_prepare(self, scene: str, target: str, payload: dict) -> dict:
        base = self.scene_base(scene, target)
        return await self.request("POST", f"{base}/upload_prepare", json=payload)

    async def upload_part_finish(self, scene: str, target: str, payload: dict) -> dict:
        base = self.scene_base(scene, target)
        return await self.request("POST", f"{base}/upload_part_finish", json=payload)

    async def upload_file(self, scene: str, target: str, payload: dict) -> dict:
        base = self.scene_base(scene, target)
        return await self.request("POST", f"{base}/files", json=payload)
