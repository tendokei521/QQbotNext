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
        - usage: 上游在流末尾回报的 usage（只带 usage，不带文本）
        - error: 流式请求失败
    """

    type: str = "text"
    text: str = ""
    tool_call: dict | None = None
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)


#: 判断「上游到底有没有报 usage」时要看的字段（各家拼写不同）
_TOKEN_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "prompt_tokens_details",
)


def _as_int(value: Any) -> int | None:
    """能当整数看就返回整数，否则 None（bool 不算数字）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def merge_usage(target: dict, usage: dict | None) -> dict:
    """把一次调用的 usage 累加进 ``target``（跨工具轮/跨重试求和）。

    数值字段相加（含 ``prompt_tokens_details`` 这类嵌套字典），非数值字段首次写入保留。
    """
    if not isinstance(usage, dict):
        return target
    for key, value in usage.items():
        if isinstance(value, dict):
            node = target.get(key)
            if not isinstance(node, dict):
                node = {}
                target[key] = node
            merge_usage(node, value)
            continue
        number = _as_int(value)
        if number is not None:
            target[key] = _as_int(target.get(key)) or 0
            target[key] += number
        elif key not in target:
            target[key] = value
    return target


def has_token_usage(usage: dict | None) -> bool:
    """上游有没有真的回报 token 数（全 0 也算报了）。"""
    if not isinstance(usage, dict):
        return False
    for key in _TOKEN_USAGE_KEYS:
        value = usage.get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            if value:
                return True
            continue
        return True
    return False


def cache_hit_miss(usage: dict | None) -> tuple[int | None, int | None]:
    """从各家 usage 里取出 ``(缓存命中, 未命中)``；上游没给就返回 ``(None, None)``。

    - DeepSeek：``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``；
    - OpenAI：``prompt_tokens_details.cached_tokens``（未命中 = 输入 - 命中）；
    - Anthropic：``cache_read_input_tokens``（命中）/ ``cache_creation_input_tokens``（写入）。

    **不自己估算**：上游没报就是 ``None``，宁缺毋编。
    """
    if not isinstance(usage, dict):
        return None, None
    hit = _as_int(usage.get("prompt_cache_hit_tokens"))
    miss = _as_int(usage.get("prompt_cache_miss_tokens"))
    if hit is None and miss is None:
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached = _as_int(details.get("cached_tokens"))
            if cached is not None:
                hit = cached
                prompt = _as_int(usage.get("prompt_tokens"))
                miss = max(prompt - cached, 0) if prompt is not None else None
    if hit is None and miss is None:
        read = _as_int(usage.get("cache_read_input_tokens"))
        write = _as_int(usage.get("cache_creation_input_tokens"))
        if read is not None or write is not None:
            hit, miss = read, write
    return hit, miss


def input_output_tokens(usage: dict | None) -> tuple[int | None, int | None]:
    """从各家 usage 里取出 ``(输入, 输出)``；上游没给就返回 ``(None, None)``。"""
    if not isinstance(usage, dict):
        return None, None
    prompt = _as_int(usage.get("prompt_tokens"))
    if prompt is None:
        prompt = _as_int(usage.get("input_tokens"))
    completion = _as_int(usage.get("completion_tokens"))
    if completion is None:
        completion = _as_int(usage.get("output_tokens"))
    return prompt, completion


def format_usage(usage: dict | None) -> str:
    """把 usage 拼成一行可读文本：**上游给什么就用什么**，缺的字段不编。

    形如 ``输入 1234（缓存命中 1000 / 未命中 234）/ 输出 567 / 合计 1801 tokens``；
    只有命中/未命中时退化为 ``缓存命中 1000 / 未命中 234 tokens``；什么都没有返回空串。
    """
    prompt, completion = input_output_tokens(usage)
    hit, miss = cache_hit_miss(usage)
    total = _as_int((usage or {}).get("total_tokens")) if isinstance(usage, dict) else None

    parts: list[str] = []
    if prompt is not None:
        detail = ""
        if hit is not None or miss is not None:
            detail = f"（缓存命中 {hit if hit is not None else '?'} / 未命中 {miss if miss is not None else '?'}）"
        parts.append(f"输入 {prompt}{detail}")
    elif hit is not None or miss is not None:
        parts.append(f"缓存命中 {hit if hit is not None else '?'} / 未命中 {miss if miss is not None else '?'}")
    if completion is not None:
        parts.append(f"输出 {completion}")
    if total is not None:
        parts.append(f"合计 {total}")
    return " ".join([" / ".join(parts), "tokens"]) if parts else ""



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


def build_extra_headers(config: dict) -> dict:
    """从 Provider 预设配置中提取自定义请求头（headers / extra_headers）。"""
    raw = (config or {}).get("headers") or (config or {}).get("extra_headers") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if v is not None}


def build_extra_body(config: dict) -> dict:
    """从 Provider 预设配置中提取自定义请求体扩展字段（extra_body）。"""
    raw = (config or {}).get("extra_body") or {}
    return raw if isinstance(raw, dict) else {}


class BaseProvider:
    """对话 Provider 基类。子类实现 chat()，处理「调哪个 LLM、如何容错」。"""

    name = "base"
    alias_names: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ("chat", "stream")

    def __init__(self, config: dict) -> None:
        self.config = config or {}

    def supports(self, capability: str) -> bool:
        """声明该 Provider 是否支持某项能力。"""
        return capability in self.capabilities

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
        tools: list[dict] | None = None,
        tool_executor=None,
        max_tool_rounds: int = 5,
    ) -> LLMResponse:
        """对话请求（子类实现）。

        ``tools`` / ``tool_executor`` / ``max_tool_rounds`` 是原生 function calling
        的工具循环参数；不支持工具的 Provider 可以忽略它们（基类签名在此声明，
        便于调用方统一传参而无需逐家内省）。
        """
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
