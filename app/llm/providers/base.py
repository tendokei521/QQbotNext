"""LLM Provider 基类与统一响应实体。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# 用于从异常消息中回挖 HTTP 状态码（历史异常可能不带 code 属性）
_HTTP_RE = re.compile(r"\bHTTP[ /_]?(\d{3})\b", re.IGNORECASE)


@dataclass
class LLMResponse:
    """统一 LLM 响应（化用 AstrBot LLMResponse 的轻量版）。"""

    text: str = ""
    reasoning: str = ""
    usage: dict = field(default_factory=dict)
    raw: Any = None
    tool_results: list = field(default_factory=list)  # 工具循环执行记录 [{name,args,result}]

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


@dataclass
class StreamEvent:
    """流式输出事件。

    type:
        - text: 文本增量
        - tool_call: 工具调用碎片（需要按 index 累积）
        - done: 本轮流结束
        - error: 流式请求失败
    """

    type: str = "text"
    text: str = ""
    tool_call: dict | None = None
    finish_reason: str = ""


def _safe_str(e: Exception) -> str:
    try:
        return (str(e) or "").strip()
    except Exception:
        return ""


def _infer_code(e: Exception) -> str:
    """推断 LLM 请求异常的错误码：优先已标注的 code，其次 HTTP 状态码/errno/异常类型。"""
    code = getattr(e, "code", None)
    if code:
        if isinstance(code, int):
            return f"HTTP {code}"
        return str(code)
    status = getattr(e, "status", None)
    if isinstance(status, int):
        return f"HTTP {status}"
    if isinstance(e, TimeoutError):
        return "TIMEOUT"
    m = _HTTP_RE.search(_safe_str(e))
    if m:
        return f"HTTP {m.group(1)}"
    errno = getattr(e, "errno", None)
    if errno is not None:
        return f"ERRNO-{errno}"
    name = type(e).__name__
    if name in (
        "ClientConnectorError",
        "ClientConnectionError",
        "ClientOSError",
        "ServerDisconnectedError",
        "ClientError",
        "ConnectionError",
    ):
        return "CONNECT"
    if name in ("ClientPayloadError",):
        return "PAYLOAD"
    if name in ("ServerTimeoutError", "ClientTimeoutError"):
        return "TIMEOUT"
    # 消息特征兜底（普通异常/字符串化网络错误）
    low = _safe_str(e).lower()
    if any(m in low for m in ("timed out", "timeout")) and "http" not in low:
        return "TIMEOUT"
    if "cannot connect" in low:
        return "CONNECT"
    if any(m in low for m in ("connection refused", "connection reset")):
        return "CONNECT"
    if any(m in low for m in ("name or service not known", "nodename nor servname", "getaddrinfo")):
        return "DNS"
    return name or "ERR"


def format_llm_error(e: Exception, fallback: str = "请求失败") -> str:
    """统一格式化 LLM 请求错误：带错误码前缀，如 ``[HTTP 429] 上游限流``。

    消息里若已重复错误码前缀（如 ``HTTP 429: xxx``），会去掉重复部分，
    避免日志出现 ``[HTTP 429] HTTP 429: xxx`` 的冗余。
    """
    code = _infer_code(e)
    msg = _safe_str(e)
    if not msg:
        return f"[{code}] {fallback}"
    if code:
        lowered = msg.lower()
        if lowered.startswith(code.lower()):
            rest = msg[len(code):].lstrip(":/- ").strip()
            return f"[{code}] {rest}" if rest else f"[{code}]"
    return f"[{code}] {msg}"


class BaseProvider:
    """对话 Provider 基类。子类实现 chat()，处理「调哪个 LLM、如何容错」。"""

    name = "base"
    alias_names: tuple[str, ...] = ()

    def __init__(self, config: dict) -> None:
        self.config = config or {}

    async def get_models(self) -> list[str]:
        """返回该连接可用的模型列表；不支持时返回空列表。"""
        return []

    async def chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout: int = 30,
    ) -> LLMResponse:
        raise NotImplementedError

    async def chat_stream(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout: int = 30,
        tools: list[dict] | None = None,
        tool_executor=None,
    ):
        """流式对话请求：逐块产出 StreamEvent，支持 tools 的碎片解析。"""
        raise NotImplementedError
        yield StreamEvent(type="error", text="not implemented")  # pragma: no cover
