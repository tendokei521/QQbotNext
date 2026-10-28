"""WebUI 鉴权：静态令牌校验 + 签名会话 cookie。

## 为什么需要会话 cookie

历史实现只有「静态令牌」一种凭据，浏览器端靠服务端把令牌注入页面 `window.WEBUI_TOKEN`，
再由前端塞进 `Authorization: Bearer`。副作用是令牌对页面内所有 JS 可见（任何 XSS 都能读走），
并且未认证用户只会撞上一个 HTTP Basic 质询框，没有正式的登录界面。

现在补一层**签名会话 cookie**：登录页校验令牌后签发 HttpOnly cookie，之后浏览器自动携带。
它和静态令牌是**并存**的两条凭据通道，不是替代关系：

| 通道 | 谁用 | 形态 |
|---|---|---|
| 会话 cookie | 浏览器（登录表单） | HttpOnly 签名 cookie，JS 读不到 |
| Bearer / Basic / `?token=` | 脚本、curl、插件页、`<img>`、WS | 原有方式，全部保留 |

## 为什么不引入 itsdangerous / jose

`itsdangerous` 只是 starlette 的**传递依赖**（未写进 requirements.txt），直接 import 等于
引入一个隐式依赖。签名逻辑本身很短，这里用标准库 `hmac` + `hashlib` + `base64` 实现，
零新增依赖。

## 签名密钥从哪来

直接用 `WEBUI_TOKEN` 经 HMAC 派生，不额外持久化 secret。好处：

- 令牌不变 → 重启后会话继续有效（不会每次重启都要求重新登录）；
- 换掉令牌 → 所有旧会话立即失效（本就是安全预期）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from app.core.logger import webui_logger

#: 会话 cookie 名
SESSION_COOKIE = "qqbot_webui_session"

#: 会话有效期（7 天）
SESSION_TTL_SECONDS = 7 * 24 * 3600

#: 登录失败限流：同一来源在窗口内最多失败次数
LOGIN_MAX_FAILURES = 10
LOGIN_WINDOW_SECONDS = 300

#: 派生会话签名密钥的域分隔标签（换标签 = 使所有既有会话失效）
_KEY_LABEL = b"qqbot-next/webui-session/v1"


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _signing_key(token: str) -> bytes:
    """由静态令牌派生会话签名密钥（令牌本身不出现在 cookie 里）。"""
    return hmac.new(token.encode("utf-8"), _KEY_LABEL, hashlib.sha256).digest()


def _sign(key: bytes, payload: str) -> str:
    return _b64e(hmac.new(key, payload.encode("ascii"), hashlib.sha256).digest())


def issue_session(token: str, ttl: int = SESSION_TTL_SECONDS) -> str:
    """签发会话 cookie 值：`<base64url(payload)>.<base64url(hmac)>`。"""
    payload = _b64e(json.dumps(
        {"exp": int(time.time()) + ttl, "sid": secrets.token_hex(8)},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8"))
    return f"{payload}.{_sign(_signing_key(token), payload)}"


def verify_session(token: str, value: str | None) -> bool:
    """校验会话 cookie：签名必须匹配且未过期。任何异常一律视为无效。

    注意：``hmac.compare_digest`` 对 ``str`` 只接受 ASCII。伪造/损坏的 cookie
    可能带非 ASCII 字符，直接比较会抛 ``TypeError`` → 每个请求 500。因此这里
    统一按 UTF-8 编码后比较，并把整段解析包在 try 里：坏 cookie = 未认证。
    """
    if not token or not value or "." not in value:
        return False
    payload, _, signature = value.rpartition(".")
    if not payload or not signature:
        return False
    try:
        expected = _sign(_signing_key(token), payload)
        # 定长比较，避免逐字节提前返回泄露签名前缀
        if not hmac.compare_digest(signature.encode("utf-8"), expected.encode("utf-8")):
            return False
        data = json.loads(_b64d(payload))
        exp = int(data.get("exp", 0))
    except (ValueError, TypeError, UnicodeError) as e:
        # 伪造/损坏的 cookie：按未认证处理，不能让它把请求打成 500
        webui_logger.debug(f"[WebUI] 会话 cookie 无效（按未认证处理）: {e}")
        return False
    return exp > int(time.time())


def tokens_match(provided: str, expected: str) -> bool:
    """静态令牌比较：定长比较，两条通道共用。

    **必须按 UTF-8 编码后比较**：``hmac.compare_digest`` 对 ``str`` 只接受 ASCII，
    非 ASCII 令牌会直接抛 ``TypeError`` → 所有 ``/api``、页面、WS 握手全部 500，
    全站不可用（与 ``onebot/reverse.py::_token_matches`` 口径一致）。
    """
    if not provided or not expected:
        return False
    _warn_non_ascii_token(expected)
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


#: 已经告警过的非 ASCII 令牌（按令牌值去重，避免每个请求都刷一条日志）
_NON_ASCII_WARNED: set[str] = set()


def _warn_non_ascii_token(token: str) -> None:
    """令牌含非 ASCII 字符时明确告警：能工作，但传给客户端时容易失真。"""
    if token.isascii() or token in _NON_ASCII_WARNED:
        return
    _NON_ASCII_WARNED.add(token)
    webui_logger.warning(
        "[WebUI] WEBUI_TOKEN 含非 ASCII 字符：鉴权已按 UTF-8 比较可正常工作，"
        "但部分终端 / HTTP 客户端 / 反向代理传参会失真，建议改用 ASCII 令牌"
    )


#: 查询参数里可携带令牌的键名。`token` 是历史名，`accesstoken` 与其等价
#: （便于书签/启动脚本直接拼一条「一键登录」URL）。
QUERY_TOKEN_KEYS = ("token", "accesstoken")


def query_token_from(params) -> str:
    """从查询参数取令牌，兼容 `token` 与 `accesstoken` 两个键名。"""
    for key in QUERY_TOKEN_KEYS:
        value = params.get(key)
        if value:
            return str(value).strip()
    return ""


# --------------------------------------------------------------------------- #
# 凭据提取（HTTP 与 WebSocket 共用一套规则）
# --------------------------------------------------------------------------- #
def credential_from_headers(authorization: str, query_token: str = "") -> str:
    """按 Bearer → Basic → query 的顺序提取静态令牌。

    Basic 把 password 当令牌（浏览器原生登录框会弹），username 不参与校验。
    """
    auth = (authorization or "").strip()
    lowered = auth.lower()
    if lowered.startswith("bearer "):
        return auth[7:].strip()
    if lowered.startswith("basic "):
        try:
            decoded = base64.b64decode(auth[6:], validate=False).decode("utf-8", errors="ignore")
        except (ValueError, TypeError):
            return ""
        # 只按第一个冒号切分：令牌本身可能含冒号
        return decoded.split(":", 1)[1].strip() if ":" in decoded else ""
    return (query_token or "").strip()


def is_authenticated(
    *,
    expected_token: str,
    authorization: str = "",
    query_token: str = "",
    session_cookie: str | None = None,
) -> bool:
    """会话 cookie 或静态令牌任一有效即通过。"""
    if not expected_token:
        return False
    if verify_session(expected_token, session_cookie):
        return True
    return tokens_match(credential_from_headers(authorization, query_token), expected_token)


# --------------------------------------------------------------------------- #
# 登录失败限流（进程内；重启即清空，够用且无状态）
# --------------------------------------------------------------------------- #
@dataclass
class _Bucket:
    count: int
    first_at: float


class LoginRateLimiter:
    """按来源 IP 限制登录失败次数，防在线爆破。

    只记失败、成功即清零；窗口过期自动回收。进程内实现——本项目本就单进程运行，
    多进程部署需要外部存储，届时应换成 Redis 之类。
    """

    def __init__(self, max_failures: int = LOGIN_MAX_FAILURES,
                 window: float = LOGIN_WINDOW_SECONDS) -> None:
        self.max_failures = max_failures
        self.window = window
        self._buckets: dict[str, _Bucket] = {}

    def _now(self) -> float:
        return time.time()

    def _prune(self, now: float) -> None:
        stale = [k for k, v in self._buckets.items() if now - v.first_at > self.window]
        for key in stale:
            self._buckets.pop(key, None)

    def is_blocked(self, source: str) -> bool:
        now = self._now()
        self._prune(now)
        bucket = self._buckets.get(source)
        return bool(bucket and bucket.count >= self.max_failures)

    def retry_after(self, source: str) -> int:
        """距离窗口重置的秒数（用于 Retry-After 头）。"""
        bucket = self._buckets.get(source)
        if not bucket:
            return 0
        return max(1, int(self.window - (self._now() - bucket.first_at)))

    def record_failure(self, source: str) -> None:
        now = self._now()
        bucket = self._buckets.get(source)
        if bucket is None or now - bucket.first_at > self.window:
            self._buckets[source] = _Bucket(count=1, first_at=now)
        else:
            bucket.count += 1

    def reset(self, source: str) -> None:
        self._buckets.pop(source, None)

    def clear(self) -> None:
        self._buckets.clear()


#: 全局限流器实例（应用单进程共享）
login_rate_limiter = LoginRateLimiter()


# --------------------------------------------------------------------------- #
# 重定向目标校验
# --------------------------------------------------------------------------- #
def safe_next_path(raw: str | None) -> str:
    """只允许站内相对路径，挡住 `//evil.com` 这类开放重定向。"""
    candidate = (raw or "").strip()
    if not candidate.startswith("/"):
        return "/"
    if candidate.startswith("//") or candidate.startswith("/\\"):
        return "/"
    # 含控制字符或换行的一律拒绝（响应头注入防护）
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in candidate):
        return "/"
    return candidate