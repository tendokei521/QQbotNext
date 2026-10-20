"""日志脱敏：把内联图片的 base64 载荷压成占位符。

**为什么要这一层**：模型请求里的图片是 ``data:image/png;base64,<几 MB>``
（见 ``app/agent/media/store.py`` 的 ``inline_bytes``/``data_url``）。一旦这串东西进了
日志，一条普通 user 消息就能把 ``debug.log`` 撑大几 MB，六小时轮转文件跟着翻倍，
而且 ``[Prompt]`` 调试输出会把整段载荷逐行刷屏（``llm_enhance`` 插件的 ``debug_prompt``
开关走的就是这条路径，见 ``app/agent/loop/turn_metrics.py``）。

**为什么挂在日志出口**：写日志的点有很多（调试 prompt、OneBot 工具调试、插件自己的
debug），逐个调用点打补丁必然漏。这里在 ``core/logger.py`` 的 Formatter 里统一过一遍，
任何 handler、任何模块、包括第三方插件打印的日志都被覆盖。

**只认明确形态，不做裸串启发式**：``data:...;base64,...`` 与 ``base64://...`` 覆盖了
本仓库全部图片内联路径（``agent/media/image.py``、``agent/media/store.py``、
``providers/anthropic.py``、``providers/gemini.py``）。至于"看起来像 base64 的长串"
不做猜测——长 token、签名、目录哈希都是这种长相，误伤它们反而让日志没法用。
"""

from __future__ import annotations

import re

#: 小于这个长度的载荷原样保留：短 base64 不占空间，压掉反而妨碍排查
MIN_PAYLOAD = 128

_DATA_URL_RE = re.compile(
    r"data:(?P<mime>[a-z0-9.+-]+/[a-z0-9.+-]+);base64,(?P<payload>[A-Za-z0-9+/=]+)",
    re.IGNORECASE,
)
_B64_SCHEME_RE = re.compile(r"base64://(?P<payload>[A-Za-z0-9+/=]+)")


def size_label(chars: int) -> str:
    """载荷体积的人话标签（按 base64 字符数算，够肉眼核对"是不是同一张图"）。"""
    if chars >= 1024 * 1024:
        return f"{chars / (1024 * 1024):.1f}MB"
    if chars >= 1024:
        return f"{chars / 1024:.1f}KB"
    return f"{chars}B"


def placeholder(chars: int) -> str:
    """占位符本体，例如 ``[base64 4.9KB]``。"""
    return f"[base64 {size_label(chars)}]"


def shorten_data_url(value: object) -> object:
    """单个 data URL → ``data:image/png;base64,[base64 4.9KB]``；非 data URL 原样返回。"""
    if not isinstance(value, str) or not value.startswith("data:"):
        return value
    head, sep, payload = value.partition(",")
    if not sep or not payload:
        return value
    return f"{head},{placeholder(len(payload))}"


def _redact_data_url(match: re.Match[str]) -> str:
    payload = match.group("payload")
    if len(payload) < MIN_PAYLOAD:
        return match.group(0)
    return f"data:{match.group('mime')};base64,{placeholder(len(payload))}"


def _redact_scheme(match: re.Match[str]) -> str:
    payload = match.group("payload")
    if len(payload) < MIN_PAYLOAD:
        return match.group(0)
    return f"base64://{placeholder(len(payload))}"


def redact_payloads(text: str) -> str:
    """把文本里的大块 base64 载荷换成占位符（其余内容一字不动）。

    快筛是必要的：该方法挂在 Formatter 上，每条日志都会过一遍，
    绝大多数消息不含 ``base64`` 字样，一次子串检查就返回。
    """
    if not text or "base64" not in text:
        return text
    return _B64_SCHEME_RE.sub(_redact_scheme, _DATA_URL_RE.sub(_redact_data_url, text))
