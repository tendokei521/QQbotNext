"""扫码登录转发 + 自动回填账号（计划 P5-3）。

用户路径目标：**不打开 NapCat 面板**也能登录 —— WebUI 显示二维码 → 手机扫码 →
登录成功后自动把账号的 ``ws_url`` / ``token`` 写好并触发连接。

实现上把"和协议端说话"收敛到一个可注入的 HTTP 调用器：
- 真实环境调 NapCat 的 HTTP API（默认 ``http://127.0.0.1:6099``）；
- 单测注入假函数，不联网、不需要协议端。

安全边界：只允许 ``127.0.0.1`` / ``localhost`` 的协议端地址（不接受用户填远端地址，
避免把二维码/凭据中转到别人的机器上）。
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

#: NapCat 常见 HTTP API 端点
QR_ENDPOINT = "/api/QQLogin/GetQQLoginQrcode"
STATUS_ENDPOINT = "/api/QQLogin/CheckLoginStatus"
#: 登录成功后写进账号的默认反向 WS 地址（协议端连回本程序）
DEFAULT_REVERSE_WS = "ws://127.0.0.1:3001/onebot/v11/ws"


class ProtocolLoginError(RuntimeError):
    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


def _assert_local(base_url: str) -> str:
    parsed = urlparse(str(base_url or "").strip())
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ProtocolLoginError(
            "PROTOCOL_BASE_URL_INVALID",
            f"协议端地址必须是本机 http(s) 地址：{base_url!r}",
            "默认 http://127.0.0.1:6099；不接受远端地址",
        )
    return str(base_url).rstrip("/")


class ProtocolLoginService:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:6099",
        *,
        http_get: Callable[[str], Any] | None = None,
        http_post: Callable[[str, dict | None], Any] | None = None,
    ) -> None:
        self.base_url = _assert_local(base_url)
        self._get = http_get
        self._post = http_post

    # ==================== 和协议端说话 ====================

    def _request(self, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        try:
            if payload is None:
                data = self._get(url) if self._get else self._real_get(url)
            else:
                data = self._post(url, payload) if self._post else self._real_post(url, payload)
        except ProtocolLoginError:
            raise
        except Exception as e:  # noqa: BLE001 —— 连不上是用户最常见的处境，要转成可读提示
            raise ProtocolLoginError(
                "PROTOCOL_UNREACHABLE",
                f"连不上协议端 HTTP API：{e}",
                "确认协议端已启动，且开启 HTTP 服务（默认 6099 端口）",
            ) from e
        if not isinstance(data, dict):
            raise ProtocolLoginError("PROTOCOL_BAD_RESPONSE", "协议端返回的不是 JSON 对象")
        return data

    @staticmethod
    def _real_get(url: str) -> dict:  # pragma: no cover —— 真实网络由手动验证覆盖
        import json
        import urllib.request

        with urllib.request.urlopen(url, timeout=8) as response:  # noqa: S310 —— 已限制为本机地址
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _real_post(url: str, payload: dict | None) -> dict:  # pragma: no cover
        import json
        import urllib.request

        body = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(request, timeout=8) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))

    # ==================== 二维码 ====================

    def qrcode(self) -> dict:
        """取登录二维码：返回可直接放进 ``<img src>`` 的 data URL。"""
        data = self._request(QR_ENDPOINT)
        payload = data.get("data") if isinstance(data.get("data"), dict) else data
        image = str(payload.get("qrcode") or payload.get("base64") or payload.get("image") or "")
        url = str(payload.get("qrcode_url") or payload.get("url") or "")
        if not image and not url:
            raise ProtocolLoginError(
                "PROTOCOL_NO_QRCODE", "协议端没有返回二维码", "确认协议端版本支持扫码登录接口"
            )
        if image and not image.startswith("data:"):
            image = "data:image/png;base64," + image
        elif not image and url:
            # 没有图就用二维码内容渲染（前端用二维码组件渲染 url 文本）
            image = ""
        return {"image": image, "content": url, "raw": payload}

    def status(self) -> dict:
        """查询登录状态：{online, user_id, nickname}。"""
        data = self._request(STATUS_ENDPOINT)
        payload = data.get("data") if isinstance(data.get("data"), dict) else data
        online = bool(
            payload.get("is_online")
            or payload.get("online")
            or payload.get("status") in ("online", True, 1, "1")
        )
        user_id = payload.get("uin") or payload.get("user_id") or payload.get("userId") or 0
        nickname = str(payload.get("nick") or payload.get("nickname") or "")
        return {
            "online": online,
            "user_id": int(user_id) if str(user_id).isdigit() else user_id,
            "nickname": nickname,
        }

    # ==================== 登录后回填账号 ====================

    async def complete_login(
        self,
        config_service: Any,
        account_id: str,
        *,
        ws_url: str = DEFAULT_REVERSE_WS,
        access_token: str = "",
    ) -> dict:
        """登录成功后把协议端地址/令牌写进账号，并返回该账号当前配置。

        只在**状态为在线**时才写，避免把"还没扫码"的账号配错。
        """
        state = self.status()
        if not state["online"]:
            raise ProtocolLoginError(
                "PROTOCOL_NOT_LOGGED_IN", "协议端还没登录成功，请先扫码",
            )
        bots = config_service.get_bots()
        target = next((bot for bot in bots if str(bot.get("id")) == str(account_id)), None)
        if target is None:
            raise ProtocolLoginError(
                "PROTOCOL_ACCOUNT_MISSING", f"账号不存在：{account_id}", "先在账号管理里创建账号"
            )
        updated = []
        for bot in bots:
            if str(bot.get("id")) != str(account_id):
                updated.append(bot)
                continue
            row = dict(bot)
            row["ws_url"] = ws_url or row.get("ws_url") or DEFAULT_REVERSE_WS
            if access_token:
                row["access_token"] = access_token
            row["conn_mode"] = "reverse"
            updated.append(row)
        await config_service.save_bots(updated)
        return {
            "account_id": str(account_id),
            "ws_url": (ws_url or DEFAULT_REVERSE_WS),
            "user_id": state["user_id"],
            "nickname": state["nickname"],
        }


def decode_qr_payload(content: str) -> bytes | None:
    """把 ``data:image/png;base64,...`` 解成字节（给"另存为图片"用）。"""
    if not content.startswith("data:"):
        return None
    _, _, payload = content.partition(",")
    try:
        return base64.b64decode(payload)
    except (ValueError, base64.binascii.Error):
        return None
