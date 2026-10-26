"""OpenAI Responses API Provider（``POST {base}/responses``）。

和 ``openai_compat``（Chat Completions）的差别不只是路径：

- 对话以 **input items** 表达，不是 ``messages[]``：
  * 普通消息：``{"role": "user", "content": "..."}``
  * 模型发起的工具调用：``{"type": "function_call", "call_id", "name", "arguments"}``
  * 工具结果：``{"type": "function_call_output", "call_id", "output"}``（独立 item，不是 role=tool 消息）
- 工具定义是扁平结构（``{"type": "function", "name", ...}``），没有 ``function`` 外层；
- 流式输出是 typed event（``response.output_text.delta`` /
  ``response.function_call_arguments.delta`` / ``response.completed``），不是
  ``chat.completion.chunk``；
- ``max_output_tokens`` 有下限 16（低于会被 400 拒绝），且默认要求 ``store: false``
  （对话不留在服务商侧）。

适配器仍然遵守既有契约：**一次请求、不在适配器里跑工具循环**。模型要调用的工具
通过 ``LLMResponse.tool_calls``（OpenAI 形状）交给 ``loop/tool_step``，下一轮再把
工具结果按上面的 item 形状喂回来。

为什么要有它：OpenAI 新一代模型与部分中转只开放 Responses 端点，只支持
Chat Completions 的适配器连"能对话"都做不到；反过来也一样。预设里的
「API 格式」让用户按端点实际支持的协议选，而不是猜。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import aiohttp

from app.agent import logger
from app.agent.providers.base import (
    LLMResponse,
    StreamEvent,
    format_llm_error,
)
from app.agent.providers.openai_compat import (
    _VERSIONED_BASE_RE,
    RETRYABLE_STATUS,
    OpenAICompatProvider,
    _AuthError,
    _FatalError,
    _retry_after_of,
    _RetryableError,
)

#: OpenAI Responses 拒收小于 16 的 ``max_output_tokens``（实测 400）。
_MIN_OUTPUT_TOKENS = 16


def _content_text(content) -> str:
    """把字符串 / 富内容块列表压成纯文本（图片单独处理）。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            parts.append(str(part))
            continue
        kind = str(part.get("type", "")).lower()
        if kind in ("text", "input_text", "output_text"):
            parts.append(str(part.get("text", "")))
        elif kind == "refusal":
            parts.append(str(part.get("refusal", "")))
    return "\n".join(p for p in parts if p)


def _content_parts(content, *, role: str) -> list[dict] | str:
    """把统一 content 转成 Responses 的 content：纯文本直接给字符串，带图给块列表。"""
    text = _content_text(content)
    images: list[str] = []
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if str(part.get("type", "")).lower() not in ("image_url", "image"):
                continue
            raw = part.get("image_url", part)
            url = raw.get("url", "") if isinstance(raw, dict) else str(raw or "")
            if url:
                images.append(url)
    if not images:
        return text
    text_type = "output_text" if role == "assistant" else "input_text"
    blocks: list[dict] = []
    if text:
        blocks.append({"type": text_type, "text": text})
    for url in images:
        if not url.lower().startswith(("data:", "http://", "https://")):
            continue
        blocks.append({"type": "input_image", "image_url": url})
    return blocks or text


def _tool_arguments(raw) -> str:
    """工具参数统一成 JSON 字符串（Responses 的 arguments 是字符串）。"""
    if isinstance(raw, str):
        return raw or "{}"
    if raw is None:
        return "{}"
    try:
        return json.dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def to_responses_input(messages: list[dict]) -> tuple[list[dict], str]:
    """``messages[]`` → ``(input items, instructions)``。

    system / developer 消息折进顶层 ``instructions``（Responses 的惯用位置）；
    其余按角色转成 message item；工具调用/结果转成独立 item。

    这里故意**不生成** ``reasoning`` item：本项目不把上一轮的思考内容回放给模型
    （既省 token，也避免跨服务商的 reasoning 签名不兼容被 400）。
    """
    items: list[dict] = []
    system_parts: list[str] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "") or "").lower()
        content = message.get("content", "")
        if role in ("system", "developer"):
            text = _content_text(content)
            if text.strip():
                system_parts.append(text)
            continue
        if role in ("tool", "function"):
            call_id = str(message.get("tool_call_id") or message.get("call_id") or message.get("id") or "")
            items.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": _content_text(content) or "(no output)",
            })
            continue
        if role == "assistant":
            text = _content_text(content)
            if text.strip():
                items.append({
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                })
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or {}
                call_id = str(call.get("id") or call.get("call_id") or "")
                items.append({
                    "type": "function_call",
                    "call_id": call_id,
                    "name": str(function.get("name") or call.get("name") or ""),
                    "arguments": _tool_arguments(function.get("arguments", call.get("arguments"))),
                })
            continue
        # user 及其它角色按原样发（developer 已在上面拦截）
        items.append({"role": role or "user", "content": _content_parts(content, role=role or "user")})
    return items, "\n\n".join(system_parts).strip()


def to_responses_tools(tools: list[dict] | None) -> list[dict]:
    """Chat Completions 的工具声明 → Responses 的扁平工具声明。"""
    result: list[dict] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        if str(tool.get("type", "function")) != "function":
            continue
        function = tool.get("function") or tool
        name = str(function.get("name", "") or "")
        if not name:
            continue
        item = {
            "type": "function",
            "name": name,
            "description": str(function.get("description", "") or ""),
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
        }
        if function.get("strict") is not None:
            item["strict"] = bool(function.get("strict"))
        result.append(item)
    return result


def responses_usage(result: dict) -> dict:
    """Responses 的 usage → 本项目统一的 token 口径。"""
    usage = (result or {}).get("usage") or {}
    if not isinstance(usage, dict):
        return {}
    mapped: dict = {}
    if usage.get("input_tokens") is not None:
        mapped["prompt_tokens"] = int(usage.get("input_tokens") or 0)
    if usage.get("output_tokens") is not None:
        mapped["completion_tokens"] = int(usage.get("output_tokens") or 0)
    if usage.get("total_tokens") is not None:
        mapped["total_tokens"] = int(usage.get("total_tokens") or 0)
    cached = ((usage.get("input_tokens_details") or {}).get("cached_tokens"))
    if cached:
        mapped["prompt_cache_hit_tokens"] = int(cached)
    return mapped


def parse_responses_result(result: dict) -> LLMResponse:
    """把一次非流式 Responses 响应解析成 ``LLMResponse``。"""
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict] = []
    for item in (result or {}).get("output") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type", ""))
        if kind == "message":
            for block in item.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "output_text":
                    text_parts.append(str(block.get("text", "")))
                elif block.get("type") == "refusal":
                    text_parts.append(str(block.get("refusal", "")))
        elif kind == "reasoning":
            for block in item.get("summary") or []:
                if isinstance(block, dict):
                    reasoning_parts.append(str(block.get("text", "")))
            for block in item.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "reasoning_text":
                    reasoning_parts.append(str(block.get("text", "")))
        elif kind == "function_call":
            tool_calls.append({
                "id": str(item.get("call_id") or item.get("id") or ""),
                "type": "function",
                "function": {
                    "name": str(item.get("name", "")),
                    "arguments": _tool_arguments(item.get("arguments")),
                },
            })
    text = "".join(text_parts).strip() or str(result.get("output_text") or "").strip()
    return LLMResponse(
        text=text,
        reasoning="".join(reasoning_parts).strip(),
        usage=responses_usage(result),
        raw=result,
        tool_calls=tool_calls,
    )


def stream_events_from_responses(chunk: dict, state: dict) -> list[StreamEvent]:
    """一条 Responses SSE data → 统一 ``StreamEvent`` 列表（纯函数，便于单测）。

    ``state`` 在整条流里共享，用于把 ``item_id`` 映射成稳定的 tool_call index，
    并记录"这个 item 是否已经通过 delta 吐过正文/参数"——部分中转不逐字流式，
    只在 ``output_item.done`` 里给完整内容，要在那里兜底，否则整轮看起来零产出。
    """
    events: list[StreamEvent] = []
    if not isinstance(chunk, dict):
        return events
    kind = str(chunk.get("type", "") or "")
    if kind == "response.output_text.delta" and chunk.get("delta"):
        item_id = str(chunk.get("item_id") or "")
        state.setdefault("text_delta_items", set()).add(item_id)
        events.append(StreamEvent(type="text", text=str(chunk.get("delta"))))
    elif kind in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
        if chunk.get("delta"):
            events.append(StreamEvent(type="reasoning", text=str(chunk.get("delta"))))
    elif kind == "response.output_item.added":
        item = chunk.get("item") or {}
        if isinstance(item, dict) and item.get("type") == "function_call":
            index = state.setdefault("next_index", 0)
            state["next_index"] = index + 1
            state.setdefault("item_index", {})[str(item.get("id") or chunk.get("item_id") or "")] = index
            state.setdefault("arg_delta_items", set())
            events.append(StreamEvent(type="tool_call", tool_call={
                "index": index,
                "id": str(item.get("call_id") or item.get("id") or ""),
                "type": "function",
                "function": {"name": str(item.get("name") or ""), "arguments": ""},
            }))
    elif kind == "response.function_call_arguments.delta":
        item_id = str(chunk.get("item_id") or "")
        index = state.setdefault("item_index", {}).get(item_id)
        if index is not None:
            state.setdefault("arg_delta_items", set()).add(item_id)
            events.append(StreamEvent(type="tool_call", tool_call={
                "index": index,
                "function": {"arguments": str(chunk.get("delta") or "")},
            }))
    elif kind == "response.output_text.done" and chunk.get("text"):
        # 有的中转只给 done 不给 delta：这时把完整正文补出来，避免整轮被当成零产出
        item_id = str(chunk.get("item_id") or "")
        if item_id not in state.get("text_delta_items", set()):
            events.append(StreamEvent(type="text", text=str(chunk.get("text"))))
    elif kind == "response.function_call_arguments.done":
        item_id = str(chunk.get("item_id") or "")
        index = state.setdefault("item_index", {}).get(item_id)
        if index is not None and item_id not in state.get("arg_delta_items", set()) and chunk.get("arguments"):
            events.append(StreamEvent(type="tool_call", tool_call={
                "index": index,
                "function": {"arguments": str(chunk.get("arguments"))},
            }))
    elif kind == "response.output_item.done":
        item = chunk.get("item") or {}
        if not isinstance(item, dict):
            return events
        item_id = str(item.get("id") or chunk.get("item_id") or "")
        if item.get("type") == "message":
            # 上游没发 delta 时，正文只在 done 里出现
            if item_id not in state.get("text_delta_items", set()):
                text = "".join(
                    str(block.get("text", ""))
                    for block in item.get("content") or []
                    if isinstance(block, dict) and block.get("type") == "output_text"
                )
                if text:
                    events.append(StreamEvent(type="text", text=text))
        elif item.get("type") == "function_call":
            index = state.setdefault("item_index", {}).get(item_id)
            if index is None:
                index = state.setdefault("next_index", 0)
                state["next_index"] = index + 1
                state.setdefault("item_index", {})[item_id] = index
                events.append(StreamEvent(type="tool_call", tool_call={
                    "index": index,
                    "id": str(item.get("call_id") or item.get("id") or ""),
                    "type": "function",
                    "function": {"name": str(item.get("name") or ""), "arguments": ""},
                }))
            if item_id not in state.get("arg_delta_items", set()) and item.get("arguments"):
                events.append(StreamEvent(type="tool_call", tool_call={
                    "index": index,
                    "function": {"arguments": str(item.get("arguments"))},
                }))
    elif kind in ("response.completed", "response.incomplete"):
        response = chunk.get("response") or {}
        usage = responses_usage(response)
        if usage:
            events.append(StreamEvent(type="usage", usage=usage))
        finish_reason = "stop"
        details = response.get("incomplete_details") or {}
        if kind == "response.incomplete" or details.get("reason") == "max_output_tokens":
            finish_reason = "length"
        events.append(StreamEvent(type="done", finish_reason=finish_reason))
    elif kind == "error":
        message = str(chunk.get("message") or (chunk.get("error") or {}).get("message") or "Responses API error")
        events.append(StreamEvent(type="error", text=message))
    elif kind == "response.failed":
        error = (chunk.get("response") or {}).get("error") or {}
        message = str(error.get("message") or "Responses API 请求失败")
        events.append(StreamEvent(type="error", text=message))
    return events


class OpenAIResponsesProvider(OpenAICompatProvider):
    """OpenAI Responses API 适配器（复用兼容适配器的鉴权、重试与错误分类）。"""

    name = "openai-responses"
    alias_names = ("openai-responses", "responses", "openai_responses", "openai-response")

    # ── 端点 ───────────────────────────────────────────
    def _normalize_base(self) -> str:
        base = self.api_base.rstrip("/")
        if base.endswith("/responses"):
            base = base[: -len("/responses")].rstrip("/")
        if base.endswith("/chat/completions"):
            base = base[: -len("/chat/completions")].rstrip("/")
        if not _VERSIONED_BASE_RE.search(base):
            base += "/v1"
        return base

    def _endpoint(self) -> str:
        return f"{self._normalize_base()}/responses"

    # ── 请求体 ─────────────────────────────────────────
    def _build_payload(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        tools: list[dict] | None,
        stream: bool,
    ) -> dict:
        input_items, instructions = to_responses_input(messages)
        payload: dict = {
            "model": model,
            "input": input_items,
            # OpenAI 拒收 < 16 的 max_output_tokens
            "max_output_tokens": max(int(max_tokens or 0), _MIN_OUTPUT_TOKENS),
            "temperature": temperature,
            "store": False,
        }
        if instructions:
            payload["instructions"] = instructions
        converted_tools = to_responses_tools(tools)
        if converted_tools:
            payload["tools"] = converted_tools
        payload.update(self._extra_body())
        payload["stream"] = stream
        return payload

    # ── 非流式 ─────────────────────────────────────────
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
        """单次 Responses 请求；工具调用通过 ``LLMResponse.tool_calls`` 交出去。"""
        model = model or self.config.get("model", "gpt-5.2")
        payload = self._build_payload(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            stream=False,
        )
        result = await self._request(payload, timeout)
        return parse_responses_result(result)

    # ── 流式 ───────────────────────────────────────────
    async def _stream_responses(self, api_key: str, payload: dict, timeout: int):
        """发起一次流式 HTTP 请求，把 Responses typed event 转成统一事件。"""
        if not api_key and not self._extra_headers():
            raise _FatalError("API 密钥未配置", code="NO-API-KEY")
        headers = {"Content-Type": "application/json", **self._extra_headers()}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        state: dict = {"next_index": 0, "item_index": {}}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._endpoint(), headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    if resp.status in (401, 403):
                        raise _AuthError(
                            f"HTTP {resp.status}: {body}",
                            code=f"HTTP {resp.status}",
                            status_code=resp.status,
                        )
                    if resp.status in RETRYABLE_STATUS:
                        raise _RetryableError(
                            f"HTTP {resp.status}: {body}",
                            code=f"HTTP {resp.status}",
                            retry_after=_retry_after_of(resp),
                            status_code=resp.status,
                        )
                    raise _FatalError(
                        f"HTTP {resp.status}: {body}",
                        code=f"HTTP {resp.status}",
                        status_code=resp.status,
                    )
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
                        dropped += 1
                        continue
                    for event in stream_events_from_responses(chunk, state):
                        yield event
                if dropped:
                    logger.add_info("Api").warning(
                        f"Responses 流有 {dropped} 个 SSE 数据块无法解析（上游报错或连接被截断），已丢弃"
                    )

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
        """流式 Responses 请求；工具循环仍由调用方（``loop/tool_step``）负责。"""
        model = model or self.config.get("model", "gpt-5.2")
        payload = self._build_payload(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            stream=True,
        )
        for attempt in range(self.max_retries):
            key = self.api_keys[attempt % len(self.api_keys)]
            started = False
            try:
                async for event in self._stream_responses(key, payload, timeout):
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
                yield StreamEvent(type="error", text=format_llm_error(e))
                return
            except Exception as e:  # noqa: BLE001 —— 流式入口统一转事件，不把异常抛给消费端
                if started or attempt >= self.max_retries - 1:
                    yield StreamEvent(type="error", text=format_llm_error(e))
                    return
                delay = getattr(e, "retry_after", None) or (2 ** attempt)
                logger.add_info("Api").warning(
                    f"Responses 流式请求失败，{delay}s 后重试 ({attempt + 1}/{self.max_retries}): {format_llm_error(e)}"
                )
                await asyncio.sleep(delay)

        yield StreamEvent(type="error", text="Responses 流式请求最终失败")


__all__ = [
    "OpenAIResponsesProvider",
    "to_responses_input",
    "to_responses_tools",
    "responses_usage",
    "parse_responses_result",
    "stream_events_from_responses",
]
