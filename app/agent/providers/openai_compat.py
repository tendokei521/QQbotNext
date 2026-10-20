"""OpenAI 兼容 Provider（DeepSeek / OpenAI / 各类中转）。

- **原生 function calling**：支持 `tools` 参数 + `tool_executor` 工具循环——
  模型返回 tool_calls 时执行工具并把结果回传，直到模型给出最终回复（对齐 AstrBot agent）；
- 容错（化用 AstrBot request_retry）：认证/参数错误（4xx）→ 立即失败不重试；
  限流 429 / 服务端 5xx / 网络错误 → 指数退避重试；
- api_key 支持换行分隔多 key → 失败轮换。
"""

from __future__ import annotations

import asyncio
import json
from typing import Callable

import aiohttp

from app.agent import logger
from app.agent.loop.tool_loop import normalize_and_execute_tool_calls
from app.agent.providers.base import BaseProvider, LLMResponse, StreamEvent, build_extra_body, build_extra_headers, format_llm_error, merge_usage

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


class _LLMError(Exception):
    """LLM 请求错误基类，携带错误码（写入日志 / StreamEvent.error）。"""

    code = "ERR"

    def __init__(self, message: str = "", code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


class _FatalError(_LLMError):
    """不可恢复错误（认证/参数）：不重试。"""


class _AuthError(_LLMError):
    """认证失败（401/403）：当前 key 无效，轮换到下一个 key。"""


class _RetryableError(_LLMError):
    """可重试错误（限流 429 / 服务端 5xx / 部分 4xx / 网络）：指数退避重试。

    ``retry_after`` 是上游 ``Retry-After`` 头里的秒数：给了就听它的，别自作聪明
    用 1s/2s/4s 去撞同一道限流墙。
    """

    def __init__(self, message: str = "", code: str | None = None, retry_after: float | None = None):
        super().__init__(message, code)
        self.retry_after = retry_after


def _retry_after_of(resp) -> float | None:
    """从响应头取 ``Retry-After``（秒）。只认数字；HTTP-date 形式忽略。"""
    raw = ""
    try:
        raw = str(resp.headers.get("Retry-After") or resp.headers.get("retry-after") or "").strip()
    except Exception:  # noqa: BLE001 —— 头读不到就当没有
        return None
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def _split_keys(api_key: str) -> list[str]:
    if not api_key:
        return []
    if isinstance(api_key, str):
        return [k.strip() for k in api_key.replace("，", ",").replace("\n", ",").split(",") if k.strip()]
    return [str(api_key)]


#: SSE 里"上游不认识 stream_options"的报错特征（用于降级重试）
_STREAM_OPTIONS_HINTS = ("stream_options", "include_usage")


def _chunk_error_text(chunk: dict) -> str:
    """取出 SSE chunk 里的上游错误文本（``{"error": ...}`` / ``{"type":"error",...}``）。

    OpenAI 兼容中转（one-api / new-api / vLLM / Azure 风格）在**200 连接内**下发
    错误报文是常态。旧实现走 ``chunk.get("choices") or []`` 后直接 ``return events``，
    错误报文被当成"没有 choices 的正常 chunk"丢掉 —— 整条流对上层等于「成功但零产出」，
    于是又走「同 prompt 原样重试」并最终发兜底话术，而真因（upstream timeout /
    context length exceeded / quota）从没进过日志。
    """
    err = chunk.get("error")
    if err:
        if isinstance(err, dict):
            return str(err.get("message") or err.get("type") or err)
        return str(err)
    # 少数网关用 {"type": "error", "message": "..."} 的扁平形态
    if chunk.get("type") == "error":
        return str(chunk.get("message") or chunk.get("detail") or chunk)
    return ""


def events_from_chunk(chunk: dict) -> list[StreamEvent]:
    """把一个 SSE data chunk 解析成事件（纯函数，便于单测）。

    注意顺序：**先看 usage 再看 choices**——开了 ``stream_options.include_usage`` 后，
    上游会在最后发一个 ``choices: []`` 只带 ``usage`` 的 chunk，先 ``continue`` 就丢了。

    思考流也在这里解析：DeepSeek 系思考模型在流式里只发 ``delta.reasoning_content``
    （此时 ``delta.content`` 恒为 null），旧实现只认 ``content`` → 整条流被当成"零产出"。

    上游错误报文（``{"error": ...}``）同样要产出 ``error`` 事件，而不是被当成
    "没有 choices 的普通 chunk"丢掉。
    """
    events: list[StreamEvent] = []
    error_text = _chunk_error_text(chunk)
    if error_text:
        events.append(StreamEvent(type="error", text=error_text))
    usage = chunk.get("usage")
    if isinstance(usage, dict) and usage:
        events.append(StreamEvent(type="usage", usage=dict(usage)))
    choices = chunk.get("choices") or []
    if not choices:
        return events
    first = choices[0] or {}
    delta = first.get("delta", {}) or {}
    finish_reason = first.get("finish_reason", "")
    # reasoning_content 是 DeepSeek/vLLM 系的写法，reasoning 是部分中转商的写法
    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
    if reasoning:
        events.append(StreamEvent(type="reasoning", text=str(reasoning)))
    if delta.get("content"):
        events.append(StreamEvent(type="text", text=delta["content"]))
    for tc in delta.get("tool_calls") or []:
        events.append(StreamEvent(type="tool_call", tool_call=tc))
    if finish_reason:
        events.append(StreamEvent(type="done", finish_reason=finish_reason))
    return events


class OpenAICompatProvider(BaseProvider):
    name = "openai"
    alias_names = ("openai", "deepseek", "openrouter", "moonshot", "zhipu", "ollama", "lm_studio", "siliconflow")

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.api_keys = _split_keys(config.get("api_key", "")) or [""]
        self.api_base = (config.get("api_base", "") or "https://api.deepseek.com").rstrip("/")
        # HTTP 层瞬态重试次数。默认 2 而不是 3：这一层的每一次都会重发**全量上下文**，
        # 三层重试（本层 × 空回复 × 收敛）相乘起来能把一轮变成十几次请求。
        self.max_retries = max(1, int(config.get("retry_attempts", 2) or 2))

    def _extra_headers(self) -> dict:
        return build_extra_headers(self.config)

    def _extra_body(self) -> dict:
        return build_extra_body(self.config)

    def _has_auth(self) -> bool:
        return bool(self.api_keys[0]) or bool(self._extra_headers())

    def _normalize_base(self) -> str:
        """把 api_base 归一化为“可用于拼 /models 或 /chat/completions 的基地址”。

        支持三种填法：
        - 默认端点：https://api.example.com           -> https://api.example.com/v1
        - v1 端点：  https://api.example.com/v1        -> https://api.example.com/v1
        - 完整端点：https://api.example.com/v1/chat/completions
                                                    -> https://api.example.com/v1
        """
        base = self.api_base.rstrip("/")
        if base.endswith("/chat/completions"):
            base = base[: -len("/chat/completions")].rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        return base

    def _endpoint(self) -> str:
        """返回实际请求的 chat/completions 地址。

        如果 api_base 已经是完整端点（以 /chat/completions 结尾），直接使用；
        否则自动补全 /v1/chat/completions。
        """
        base = self.api_base.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{self._normalize_base()}/chat/completions"

    async def get_models(self) -> list[str]:
        """拉取 OpenAI 兼容 /models 列表。"""
        if not self._has_auth():
            return []
        base = self._normalize_base()
        url = f"{base}/models"
        headers = {"Content-Type": "application/json", **self._extra_headers()}
        if self.api_keys[0]:
            headers["Authorization"] = f"Bearer {self.api_keys[0]}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    logger.add_info("Api").error(f"拉取模型列表失败 HTTP {resp.status}")
                    return []
                data = await resp.json()
        return [str(item.get("id")) for item in (data.get("data") or []) if isinstance(item, dict) and item.get("id")]

    # ---------- 底层请求 ----------
    async def _post(self, api_key: str, payload: dict, timeout: int) -> dict:
        """发送单次请求，返回解析后的 JSON。非可重试错误抛 _FatalError。"""
        if not api_key and not self._extra_headers():
            raise _FatalError("API 密钥未配置", code="NO-API-KEY")
        headers = {"Content-Type": "application/json", **self._extra_headers()}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._endpoint(), headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    if resp.status in (401, 403):
                        raise _AuthError(f"HTTP {resp.status}: {body}", code=f"HTTP {resp.status}")
                    if resp.status in RETRYABLE_STATUS:
                        raise _RetryableError(
                            f"HTTP {resp.status}: {body}",
                            code=f"HTTP {resp.status}",
                            retry_after=_retry_after_of(resp),
                        )
                    raise _FatalError(f"HTTP {resp.status}: {body}", code=f"HTTP {resp.status}")
                return await resp.json()

    async def _stream_once(self, api_key: str, payload: dict, timeout: int):
        """发起一次流式 HTTP 请求，逐块产出 StreamEvent。"""
        if not api_key and not self._extra_headers():
            raise _FatalError("API 密钥未配置", code="NO-API-KEY")
        headers = {"Content-Type": "application/json", **self._extra_headers()}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._endpoint(), headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    if resp.status in (401, 403):
                        raise _AuthError(f"HTTP {resp.status}: {body}", code=f"HTTP {resp.status}")
                    if resp.status in RETRYABLE_STATUS:
                        raise _RetryableError(
                            f"HTTP {resp.status}: {body}",
                            code=f"HTTP {resp.status}",
                            retry_after=_retry_after_of(resp),
                        )
                    raise _FatalError(f"HTTP {resp.status}: {body}", code=f"HTTP {resp.status}")
                dropped = 0
                while True:
                    raw_line = await resp.content.readline()
                    if not raw_line:
                        break
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        # 半截 JSON = 连接被截断或上游发了非标准块：至少留个痕，
                        # 否则这段时间模型已吐出的正文会凭空少一截而没有解释
                        dropped += 1
                        continue
                    for event in events_from_chunk(chunk):
                        yield event
                if dropped:
                    logger.add_info("Api").warning(
                        f"流式响应有 {dropped} 个 SSE 数据块无法解析（上游报错或连接被截断），已丢弃"
                    )

    async def _request(self, payload: dict, timeout: int) -> dict | None:
        """带重试的请求。认证失败自动轮换 key；最终失败返回 None。"""
        last_error = ""
        for attempt in range(self.max_retries):
            key = self.api_keys[attempt % len(self.api_keys)]
            try:
                return await self._post(key, payload, timeout)
            except _AuthError as e:
                last_error = format_llm_error(e)
                if attempt < self.max_retries - 1:
                    logger.add_info("Api").warning(
                        f"API key {attempt % len(self.api_keys) + 1} 认证失败，轮换下一个: {last_error}"
                    )
                    await asyncio.sleep(0.5)
                else:
                    logger.add_info("Api").error(f"所有 API key 认证均失败: {last_error}")
                    return None
            except _FatalError as e:
                logger.add_info("Api").error(f"API 不可恢复错误: {format_llm_error(e)}")
                return None
            except Exception as e:
                last_error = format_llm_error(e)
                if attempt < self.max_retries - 1:
                    # 上游给了 Retry-After 就听它的；没有才用指数退避
                    delay = getattr(e, "retry_after", None) or (2 ** attempt)
                    logger.add_info("Api").warning(
                        f"API 请求失败，{delay}s 后重试 ({attempt + 1}/{self.max_retries}): {last_error}"
                    )
                    await asyncio.sleep(delay)
        logger.add_info("Api").error(f"API 请求最终失败: {last_error}")
        return None

    # ---------- 响应解析 ----------
    def _to_response(
        self,
        result: dict,
        tool_results: list[dict] | None = None,
        usage: dict | None = None,
    ) -> LLMResponse:
        # usage 由调用方传"本次请求的累计值"（工具多轮求和）；没传就退回这一轮的
        usage = usage if usage else (result.get("usage", {}) or {})
        choices = result.get("choices", []) or []
        content = ""
        reasoning = ""
        if choices:
            first = choices[0]
            message = first.get("message", {}) or {}
            content = (message.get("content") or "").strip()
            # reasoning_content 是 DeepSeek/vLLM 系的写法，reasoning 是部分中转商的
            # 写法（流式解析早已两种都认，这里此前只认前者，导致中转商用户的
            # 「思考吃满预算」诊断与收敛轮全部失效）
            reasoning = (message.get("reasoning_content") or message.get("reasoning") or "") or ""
            if first.get("finish_reason") == "length":
                logger.add_info("Api").warning("回复因 max_tokens 限制被截断")
        # token 消耗不在这里打：本次请求（含工具轮）结束后由 chat_with_fallback 统一 info 一次
        return LLMResponse(
            text=content,
            reasoning=reasoning,
            usage=usage,
            raw=result,
            tool_results=list(tool_results or []),
        )

    # ---------- 对话 + 工具循环 ----------
    async def chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout: int = 30,
        tools: list[dict] | None = None,
        tool_executor: Callable | None = None,
        max_tool_rounds: int = 5,
    ) -> LLMResponse:
        """对话请求。若提供 tools 且模型返回 tool_calls，则循环执行工具并回传结果。

        tools: OpenAI function 定义列表；tool_executor: async (name, args) -> str。
        """
        model = model or self.config.get("model", "deepseek-chat")
        payload: dict = {
            "model": model,
            "messages": list(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        payload.update(self._extra_body())

        tool_results: list[dict] = []
        total_usage: dict = {}  # 工具多轮的 token 消耗累计（本次请求结束后统一记一次）
        for _round in range(max_tool_rounds):
            result = await self._request(payload, timeout)
            if result is None:
                return LLMResponse(text="", raw=None, usage=total_usage)
            merge_usage(total_usage, result.get("usage", {}) or {})
            choices = result.get("choices", []) or []
            if not choices:
                return LLMResponse(text="", raw=None, usage=total_usage)
            message = choices[0].get("message", {}) or {}
            tool_calls = message.get("tool_calls") or []
            if not tool_calls or tool_executor is None:
                return self._to_response(result, tool_results, usage=total_usage)

            # 工具循环：保留 assistant tool_calls，追加各工具结果，重发。
            # 复用共享 helper（与流式 stream_response 同一套逻辑）：
            # id 自洽 + arguments 解析 + 执行 + 构造回传。
            normalized_calls, tool_messages = await normalize_and_execute_tool_calls(
                tool_calls, tool_executor, tool_results
            )
            payload["messages"].append({
                "role": "assistant",
                "content": message.get("content") or None,
                "tool_calls": normalized_calls,
            })
            payload["messages"].extend(tool_messages)

        logger.add_info("Api").warning(
            f"工具循环超过 {max_tool_rounds} 轮，强制结束（可调大 max_tool_rounds 配置）"
        )
        # 保留已执行工具结果与最后一次响应，避免已完成的工具调用静默丢失
        return self._to_response(result, tool_results, usage=total_usage)

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
        """流式对话请求：逐块产出 StreamEvent，支持 tools 碎片解析。

        注意：工具执行循环由调用方（LlmPipeline / chat.stream_response）负责，
        这里只负责单次 HTTP 流式请求的解析。
        """
        model = model or self.config.get("model", "deepseek-chat")
        payload: dict = {
            "model": model,
            "messages": list(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        payload.update(self._extra_body())
        # 让上游在流末尾回报 usage（OpenAI/DeepSeek 都要求显式开 include_usage）：
        # 用户 extra_body 里自己配了 stream_options 就尊重用户配置，不覆盖。
        if "stream_options" not in payload:
            payload["stream_options"] = {"include_usage": True}

        for attempt in range(self.max_retries):
            key = self.api_keys[attempt % len(self.api_keys)]
            started = False
            try:
                async for event in self._stream_once(key, payload, timeout):
                    started = True
                    yield event
                return
            except _AuthError as e:
                if attempt < self.max_retries - 1:
                    logger.add_info("Api").warning(
                        f"API key {attempt % len(self.api_keys) + 1} 认证失败，轮换下一个: {format_llm_error(e)}"
                    )
                    await asyncio.sleep(0.5)
                    continue
                yield StreamEvent(type="error", text=format_llm_error(e))
                return
            except _FatalError as e:
                # 少数上游不认 stream_options（会 400/422）：去掉该参数原样重发一次，
                # 拿不到 usage 也不能让流式回复整个失败。
                if "stream_options" in payload and any(h in str(e).lower() for h in _STREAM_OPTIONS_HINTS):
                    payload.pop("stream_options", None)
                    logger.add_info("Api").warning(
                        "上游不认识 stream_options.include_usage，已去掉该参数重试（本次不回报 token）"
                    )
                    try:
                        async for event in self._stream_once(key, payload, timeout):
                            started = True
                            yield event
                        return
                    except Exception as retry_error:
                        yield StreamEvent(type="error", text=format_llm_error(retry_error))
                        return
                yield StreamEvent(type="error", text=format_llm_error(e))
                return
            except Exception as e:
                if started or attempt >= self.max_retries - 1:
                    yield StreamEvent(type="error", text=format_llm_error(e))
                    return
                delay = getattr(e, "retry_after", None) or (2 ** attempt)
                logger.add_info("Api").warning(
                    f"流式请求失败，{delay}s 后重试 ({attempt + 1}/{self.max_retries}): {format_llm_error(e)}"
                )
                await asyncio.sleep(delay)

        yield StreamEvent(type="error", text="流式请求最终失败")

