"""关键失败点的统一留痕：一条日志说清「谁 / 对什么 / 怎么失败 / 该去哪查」。

## 为什么需要这一层

线上真实事故：``warn.log`` 里只有一行

    [Image] 图片下载失败（HTTP 400）

既没有 URL、没有响应体、也没有调用方——排查时只能猜"是哪张图、哪条路径、是不是链接过期"。
根因最后是靠翻 SQLite 历史 + 手工重放请求才定位到的（QQ 图片直链的 ``rkey`` 过期，
CDN 返回 ``{"retcode":-5503007,"retmsg":"download url has expired"}``）。这类"信息不足的
失败日志"会让任何一个可自愈的降级都变成不可诊断的黑洞。

所以凡是**用户可感知的失败**（图没发出去、消息没发出去、请求失败、工具失败、历史没写进去），
都走这里，一次留全三类信息：

1. **事件码** ``code=image.download_http_error``：稳定、可 grep、可计数、可做告警规则；
2. **定位字段** ``bot=123 http=400 retcode=-5503007 url=multimedia.nt.qq.com.cn/…``：
   照着字段就能去查，不需要再问"当时是哪张图"；
3. **调用链** ``caller=prompt_prep.prepare:184<-pipeline.run:790``：
   一眼看出是哪条路径触发的（同一句 warning 可能来自收图、回填、工具、重试）。

## 使用约定

- ``code`` 用点分层级，**不要**把异常文本/URL 拼进 code（否则同一件事去重失效）；
- 热点路径（每轮都会走）失败时传 ``once=True``：同一 ``(code, once_key)`` 进程内只留一条，
  重复次数折叠进 debug，避免把 ``warn.log`` 刷成噪音；
- URL 一律先过 :func:`redact_url`：``rkey`` / ``token`` 这类签名参数会被脱敏，
  日志可以安全地贴进 issue；
- 本模块**永不抛异常**（日志失败不能反过来搞崩业务）。
"""

from __future__ import annotations

import sys
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.redact import redact_payloads

#: query 里一律脱敏的参数名（QQ 图片直链的签名/票据都在这里）
_SECRET_PARAMS = frozenset(
    {"rkey", "key", "skey", "token", "sign", "sig", "auth", "authkey", "ticket", "pass_ticket"}
)

#: 单个字段渲染上限（防止把整段响应体/整条 base64 塞进日志）
_FIELD_MAX = 200

#: 一次失败最多往上取几层调用方
_CALLER_DEPTH = 4

#: (账号, code, once_key) → 已出现次数；进程重启后清空
_seen: dict[tuple[str, str, str], int] = {}


def reset_failure_state() -> None:
    """清空 ``once=True`` 的去重状态（测试用）。"""
    _seen.clear()


def failure_count(code: str, *, once_key: str = "", account: object = "") -> int:
    """某事件码被记录过多少次（``once=True`` 时含被折叠的重复次数）。"""
    return _seen.get((str(account or "?"), str(code), str(once_key)), 0)


def _truncate(value: Any) -> str:
    text = " ".join(str(value).split())
    if len(text) <= _FIELD_MAX:
        return text
    return text[:_FIELD_MAX] + "…"


def redact_url(url: object, *, max_len: int = 240) -> str:
    """把 URL 渲染成可安全落日志的形态：签名参数打码、非 http 形态只截断。

    例：``https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=***&rkey=***``
    """
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return _truncate(text)
    if parts.scheme not in ("http", "https"):
        return _truncate(text)
    pairs: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        masked = key.lower() in _SECRET_PARAMS or len(value) > 12
        pairs.append((key, "***" if masked else value))
    rendered = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(pairs, safe="*"), parts.fragment)
    )
    return rendered if len(rendered) <= max_len else rendered[:max_len] + "…"


def url_host(url: object) -> str:
    """URL 的 host（解析失败返回空串；日志里比整条 URL 更适合做聚合键）。"""
    try:
        return urlsplit(str(url or "").strip()).netloc
    except ValueError:
        return ""


def caller_chain(depth: int = _CALLER_DEPTH) -> str:
    """当前调用链，格式 ``模块.函数:行号<-模块.函数:行号``（由近及远，最多 ``depth`` 层）。

    用 ``sys._getframe`` 而不是 ``inspect.stack()``：失败路径也不该为了记日志付出
    抓全栈的代价。本模块自身的帧自动跳过。
    """
    frames: list[str] = []
    try:
        frame = sys._getframe(1)
    except (AttributeError, ValueError):  # pragma: no cover —— 极端运行时不支持
        return ""
    while frame is not None and len(frames) < max(1, int(depth or 1)):
        module = str(frame.f_globals.get("__name__", "") or "")
        if not module.endswith("core.diagnostics"):
            short = module[len("app."):] if module.startswith("app.") else module
            frames.append(f"{short}.{frame.f_code.co_name}:{frame.f_lineno}")
        frame = frame.f_back
    return "<-".join(frames)


def exception_brief(error: BaseException | None) -> str:
    """异常 → ``TypeError: xxx``（没有异常返回空串）。"""
    if error is None:
        return ""
    return f"{type(error).__name__}: {error}"


def format_failure(
    code: str,
    message: str = "",
    *,
    fields: Mapping[str, Any] | None = None,
    error: BaseException | None = None,
    caller: str = "",
) -> str:
    """拼一条结构化失败日志（纯函数，便于测试与复用）。

    输出：``消息 | code=xxx k=v … | error=Type: msg | caller=a:b<-c:d``。
    值为空/None/False 的字段直接跳过（``http=0`` 这类默认值不占位置）。
    """
    parts: list[str] = []
    if message:
        parts.append(str(message))
    parts.append(f"code={code}")
    for key, value in (fields or {}).items():
        if value is None or value is False:
            continue
        text = _truncate(value)
        if not text:
            continue
        parts.append(f"{key}={text}")
    brief = exception_brief(error)
    if brief:
        parts.append(f"error={_truncate(brief)}")
    chain = caller or caller_chain()
    if chain:
        parts.append(f"caller={chain}")
    return redact_payloads(" | ".join(parts))


def log_failure(
    log: Any,
    code: str,
    message: str = "",
    *,
    fields: Mapping[str, Any] | None = None,
    error: BaseException | None = None,
    level: str = "warning",
    once: bool = False,
    once_key: str = "",
    account: object = "",
    with_traceback: bool = False,
) -> bool:
    """记一条结构化失败日志（见模块 docstring）。

    Args:
        log: 目标 logger（``PrefixLogger`` 或任何带 debug/info/warning/error 的对象）。
        code: 稳定事件码，如 ``image.download_http_error``。
        message: 人话说明（首段，方便肉眼扫）；可空。
        fields: 定位字段（账号/会话/URL/HTTP 状态/重试次数…），空值自动跳过。
        error: 触发的异常（只写 ``类型: 文本``，栈按需另开）。
        level: ``warning``（默认）/ ``info`` / ``error`` / ``debug``。
        once: ``True`` 时同一 ``(account, code, once_key)`` 进程内只写一次，重复折叠。
        once_key: 去重键的第二段；**不要**拼 URL/异常文本这类易变内容里的随机部分，
            但对"每张不同的死链只提醒一次"这种场景，redact 后的 URL 正好合适。
        account: 账号 id（去重维度；空值归为 ``?``）。
        with_traceback: 是否额外在 debug 级别打完整栈（warn 行保持一行可 grep）。

    Returns:
        True = 本次真的写了日志；False = 被 ``once`` 去重/写日志本身出错。
    """
    try:
        account_key = str(account or "?")
        dedupe = (account_key, str(code), str(once_key))
        count = _seen.get(dedupe, 0)
        text = format_failure(code, message, fields=fields, error=error)
        if once and count:
            _seen[dedupe] = count + 1
            writer = getattr(log, "debug", None)
            if callable(writer):
                writer(f"{code} 重复失败 #{count + 1}（已折叠，首次记录见前文）")
            return False
        _seen[dedupe] = count + 1
        writer = getattr(log, level, None) or log.warning
        writer(text)
        if with_traceback and error is not None:
            debug_writer = getattr(log, "debug", None)
            if callable(debug_writer):
                debug_writer(f"{code} 调用栈", exc_info=True)
        return True
    except Exception:  # noqa: BLE001 —— 日志出口绝不能反过来搞崩业务
        return False


def elapsed_ms(start: float) -> int:
    """从 :func:`time.monotonic` 起点算耗时（毫秒，用于 ``elapsed_ms=`` 字段）。"""
    try:
        return int(max(0.0, (time.monotonic() - float(start)) * 1000))
    except (TypeError, ValueError):
        return 0


__all__ = [
    "caller_chain",
    "elapsed_ms",
    "exception_brief",
    "failure_count",
    "format_failure",
    "log_failure",
    "redact_url",
    "reset_failure_state",
    "url_host",
]
