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


def _temperature_unsupported(error: Exception) -> bool:
    """上游是否明确拒绝 ``temperature``（Responses 的推理模型常见：只收默认值）。"""
    text = str(error).lower()
    return "temperature" in text and (
        "unsupported" in text or "not supported" in text or "does not support" in text
    )


def _track_call_id(state: dict, call_id: str) -> None:
    """流里按出现顺序收集工具调用 id（reasoning 回放缓存按这串 id 建索引）。"""
    call_id = str(call_id or "")
    if not call_id:
        return
    ids = state.setdefault("call_ids", [])
    if call_id not in ids:
        ids.append(call_id)


def to_responses_input(
    messages: list[dict],
    reasoning_lookup=None,
) -> tuple[list[dict], str]:
    """``messages[]`` → ``(input items, instructions)``。

    system / developer 消息折进顶层 ``instructions``（Responses 的惯用位置）；
    其余按角色转成 message item；工具调用/结果转成独立 item。

    ``reasoning_lookup``：回放 assistant 工具调用时，查这一轮对应的 reasoning
    item（带 ``encrypted_content``）。OpenAI 在 ``store:false`` 下要求
    function_call 必须配着产生它的 reasoning item 一起回放，缺了会 400。
    查不到（进程重启 / 回放旧历史）时**降级成文本形式**：把「调用了什么工具、
    返回了什么」写成普通消息，模型照样能接上下文，总比整轮 400 好。
    """
    items: list[dict] = []
    system_parts: list[str] = []
    tool_text_fallback = False
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
            text = _content_text(content) or "(no output)"
            if tool_text_fallback:
                # 上一轮工具调用已经降级成文本，这里也必须用文本回执
                name = str(message.get("name") or message.get("tool_name") or "").strip()
                label = f"[工具 {name} 结果]" if name else "[工具结果]"
                items.append({"role": "user", "content": f"{label}\n{text}"})
                continue
            call_id = str(message.get("tool_call_id") or message.get("call_id") or message.get("id") or "")
            items.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": text,
            })
            continue
        if role == "assistant":
            text = _content_text(content)
            calls = [call for call in message.get("tool_calls") or [] if isinstance(call, dict)]
            if calls:
                call_ids = [str(call.get("id") or call.get("call_id") or "") for call in calls]
                replayed = reasoning_lookup(call_ids) if callable(reasoning_lookup) else None
                if replayed is None:
                    # 没有可回放的 reasoning item：改成文本，避免 store:false 下的 400
                    tool_text_fallback = True
                    lines: list[str] = []
                    if text.strip():
                        lines.append(text)
                    for call in calls:
                        function = call.get("function") or {}
                        lines.append(
                            f"[调用工具] {function.get('name') or call.get('name') or ''}"
                            f"({_tool_arguments(function.get('arguments', call.get('arguments')))})"
                        )
                    items.append({"role": "assistant", "content": [{"type": "output_text", "text": "\n".join(lines)}]})
                    continue
                tool_text_fallback = False
                # reasoning item 必须排在它产生的 function_call 之前
                items.extend(replayed)
            else:
                tool_text_fallback = False
            if text.strip():
                items.append({
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                })
            for call in calls:
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
        tool_text_fallback = False
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
    if str(result.get("status", "") or "") == "incomplete":
        reason = str((result.get("incomplete_details") or {}).get("reason", "") or "unknown")
        # 回退层靠 raw.finish_reason 判断"被预算截断"：Responses 没有 finish_reason，
        # 这里补一个（同时不改调用方传进来的原始 dict）
        result = {**result, "finish_reason": "length"}
        logger.add_info("Api").warning(f"Responses 回复未完成（{reason}），按截断处理")
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
        state["any_text_delta"] = True
        events.append(StreamEvent(type="text", text=str(chunk.get("delta"))))
    elif kind in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
        if chunk.get("delta"):
            events.append(StreamEvent(type="reasoning", text=str(chunk.get("delta"))))
    elif kind == "response.output_item.added":
        item = chunk.get("item") or {}
        if isinstance(item, dict) and item.get("type") == "function_call":
            index = state.setdefault("next_index", 0)
            state["next_index"] = index + 1
            item_key = str(item.get("id") or chunk.get("item_id") or "")
            state.setdefault("item_index", {})[item_key] = index
            state.setdefault("arg_delta_items", set())
            arguments = str(item.get("arguments") or "")
            if arguments:
                # 少数上游在 added 里就给了完整参数：标记为"已给过"，done 不再补一遍
                state["arg_delta_items"].add(item_key)
                state["any_arg_delta"] = True
            _track_call_id(state, str(item.get("call_id") or item.get("id") or ""))
            events.append(StreamEvent(type="tool_call", tool_call={
                "index": index,
                "id": str(item.get("call_id") or item.get("id") or ""),
                "type": "function",
                "function": {"name": str(item.get("name") or ""), "arguments": arguments},
            }))
    elif kind == "response.refusal.delta" and chunk.get("delta"):
        # 上游拒绝回答时正文在 refusal 里，别丢掉——用户至少该看到原因
        events.append(StreamEvent(type="text", text=str(chunk.get("delta"))))
    elif kind == "response.function_call_arguments.delta":
        item_id = str(chunk.get("item_id") or "")
        index = state.setdefault("item_index", {}).get(item_id)
        if index is not None:
            state.setdefault("arg_delta_items", set()).add(item_id)
            state["any_arg_delta"] = True
            events.append(StreamEvent(type="tool_call", tool_call={
                "index": index,
                "function": {"arguments": str(chunk.get("delta") or "")},
            }))
    elif kind == "response.output_text.done" and chunk.get("text"):
        # 有的中转只给 done 不给 delta：把完整正文补出来，避免整轮被当成零产出。
        # 补完要记进 text_delta_items，否则紧随其后的 output_item.done 会再补一遍，
        # 正文就重复了。
        item_id = str(chunk.get("item_id") or "")
        if item_id not in state.get("text_delta_items", set()) and not state.get("any_text_delta"):
            state.setdefault("text_delta_items", set()).add(item_id)
            state["any_text_delta"] = True
            events.append(StreamEvent(type="text", text=str(chunk.get("text"))))
    elif kind == "response.function_call_arguments.done":
        item_id = str(chunk.get("item_id") or "")
        index = state.setdefault("item_index", {}).get(item_id)
        if (
            index is not None
            and item_id not in state.get("arg_delta_items", set())
            and not state.get("any_arg_delta")
            and chunk.get("arguments")
        ):
            # 同理：补完记为"已给过参数"，避免 output_item.done 再拼一遍导致 JSON 坏掉
            state.setdefault("arg_delta_items", set()).add(item_id)
            state["any_arg_delta"] = True
            events.append(StreamEvent(type="tool_call", tool_call={
                "index": index,
                "function": {"arguments": str(chunk.get("arguments"))},
            }))
    elif kind == "response.output_item.done":
        item = chunk.get("item") or {}
        if not isinstance(item, dict):
            return events
        item_id = str(item.get("id") or chunk.get("item_id") or "")
        if item.get("type") == "reasoning":
            # 记下来给下一轮工具回放用（带 encrypted_content 才算数）
            state.setdefault("reasoning_items", []).append(item)
            return events
        if item.get("type") == "message":
            # 上游没发 delta 时，正文只在 done 里出现（delta 已经给过就不能再补）
            if item_id not in state.get("text_delta_items", set()) and not state.get("any_text_delta"):
                text = "".join(
                    str(block.get("text", ""))
                    for block in item.get("content") or []
                    if isinstance(block, dict) and block.get("type") == "output_text"
                )
                if text:
                    state["any_text_delta"] = True
                    events.append(StreamEvent(type="text", text=text))
        elif item.get("type") == "function_call":
            _track_call_id(state, str(item.get("call_id") or item.get("id") or ""))
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
            if (
                item_id not in state.get("arg_delta_items", set())
                and not state.get("any_arg_delta")
                and item.get("arguments")
            ):
                state["any_arg_delta"] = True
                events.append(StreamEvent(type="tool_call", tool_call={
                    "index": index,
                    "function": {"arguments": str(item.get("arguments"))},
                }))
    elif kind in ("response.completed", "response.incomplete"):
        response = chunk.get("response") or {}
        # 有的中转不发增量事件，只在 completed 里给完整 output：
        # 这里按非流式补发，避免整条流看起来「成功但零产出」。
        if not state.get("any_text_delta") or not state.get("next_index"):
            parsed = parse_responses_result(response)
            if not state.get("any_text_delta"):
                if parsed.reasoning:
                    events.append(StreamEvent(type="reasoning", text=parsed.reasoning))
                if parsed.text:
                    state["any_text_delta"] = True
                    events.append(StreamEvent(type="text", text=parsed.text))
            if not state.get("next_index"):
                for index, call in enumerate(parsed.tool_calls):
                    _track_call_id(state, call["id"])
                    events.append(StreamEvent(type="tool_call", tool_call={
                        "index": index,
                        "id": call["id"],
                        "type": "function",
                        "function": call["function"],
                    }))
        # 完整 output 里也可能带 reasoning（completed-only 的中转），一并记下供回放
        for item in response.get("output") or []:
            if isinstance(item, dict) and item.get("type") == "reasoning":
                state.setdefault("reasoning_items", []).append(item)
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

    #: 回放 reasoning item 的缓存条数：够覆盖一轮多工具调用的历史即可
    _REASONING_CACHE_LIMIT = 32

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        #: ``{(call_id, ...): reasoning items}``；None = 这一轮有思考但拿不到密文
        self._reasoning_cache: dict[tuple[str, ...], list[dict] | None] = {}

    # ── reasoning 回放（store:false 下 function_call 必须配对 reasoning）──
    def _remember_reasoning(self, call_ids: list[str], output: list[dict] | None) -> None:
        """记住这一轮「工具调用 → 产生它的 reasoning item」，供下一轮回放。"""
        key = tuple(str(call_id) for call_id in call_ids or [] if call_id)
        if not key:
            return
        reasoning_items = [
            item for item in (output or [])
            if isinstance(item, dict) and item.get("type") == "reasoning"
        ]
        if reasoning_items and not any(item.get("encrypted_content") for item in reasoning_items):
            cached: list[dict] | None = None  # 有思考但没有密文：不能回放，宁可走文本降级
        else:
            cached = [item for item in reasoning_items if item.get("encrypted_content")]
        self._reasoning_cache.pop(key, None)
        self._reasoning_cache[key] = cached
        while len(self._reasoning_cache) > self._REASONING_CACHE_LIMIT:
            self._reasoning_cache.pop(next(iter(self._reasoning_cache)), None)

    def _lookup_reasoning(self, call_ids: list[str]) -> list[dict] | None:
        """按工具调用 id 取回 reasoning item；``None`` = 不可回放（调用方降级文本）。"""
        return self._reasoning_cache.get(tuple(str(call_id) for call_id in call_ids or []))

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
        input_items, instructions = to_responses_input(messages, reasoning_lookup=self._lookup_reasoning)
        payload: dict = {
            "model": model,
            "input": input_items,
            # OpenAI 拒收 < 16 的 max_output_tokens
            "max_output_tokens": max(int(max_tokens or 0), _MIN_OUTPUT_TOKENS),
            "temperature": temperature,
            "store": False,
            # 让上游把 reasoning 的加密串一起给回来，下一轮工具回放要用（store:false 必须带）
            "include": ["reasoning.encrypted_content"],
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
        try:
            result = await self._request(payload, timeout)
        except _FatalError as e:
            if "temperature" not in payload or not _temperature_unsupported(e):
                raise
            # 推理模型只收默认温度：去掉该参数重发一次
            payload.pop("temperature", None)
            logger.add_info("Api").warning(
                "上游不接受 temperature（Responses 推理模型常见），已去掉该参数重试"
            )
            result = await self._request(payload, timeout)
        response = parse_responses_result(result)
        # 记住这轮工具调用对应的 reasoning item，下一轮 tool result 回放时要用
        self._remember_reasoning(
            [call["id"] for call in response.tool_calls],
            result.get("output") if isinstance(result, dict) else None,
        )
        return response

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
        # 流式结束后把工具调用 ↔ reasoning 配对记下来，供下一轮工具回放
        self._remember_reasoning(state.get("call_ids") or [], state.get("reasoning_items") or [])

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
        attempt = 0
        temperature_retried = False
        while attempt < self.max_retries:
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
                    attempt += 1
                    continue
                yield StreamEvent(type="error", text=format_llm_error(e))
                return
            except _FatalError as e:
                if not temperature_retried and "temperature" in payload and _temperature_unsupported(e):
                    # 推理模型只收默认温度：去掉该参数重发一次（不占重试额度）
                    temperature_retried = True
                    payload.pop("temperature", None)
                    logger.add_info("Api").warning(
                        "上游不接受 temperature（Responses 推理模型常见），已去掉该参数重试"
                    )
                    continue
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
                attempt += 1

        yield StreamEvent(type="error", text="Responses 流式请求最终失败")


__all__ = [
    "OpenAIResponsesProvider",
    "to_responses_input",
    "to_responses_tools",
    "responses_usage",
    "parse_responses_result",
    "stream_events_from_responses",
]
