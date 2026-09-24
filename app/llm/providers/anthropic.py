"""Anthropic Claude Provider（Messages API）。

当前实现覆盖基础文本对话/流式输出；
`tools` 会转换成 Anthropic tools 格式，工具执行结果仍以文本块回传。
若后续要完整的 function calling 多轮循环，可复用 OpenAICompatProvider 的 tool_loop。
"""

from __future__ import annotations

import json
import re
from typing import Any

import aiohttp

from app.llm import logger
from .base import BaseProvider, LLMResponse, StreamEvent, format_llm_error, merge_usage

_DEFAULT_BASE = "https://api.anthropic.com"
_API_VERSION = "2023-06-01"
_DATA_URL_RE = re.compile(r"^data:image/([a-z0-9.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)


def _parse_data_url(url: str) -> tuple[str, str] | None:
    """``data:image/png;base64,xxxx`` → (media_type, base64 数据)。"""
    match = _DATA_URL_RE.match(str(url or "").strip())
    if not match:
        return None
    return f"image/{match.group(1).lower()}", match.group(2)


def _to_anthropic_content(content: Any) -> list[dict]:
    """把统一的 content（字符串 / OpenAI 风格块数组）转成 Anthropic 内容块。"""
    if not isinstance(content, list):
        return [{"type": "text", "text": str(content or "")}]
    blocks: list[dict] = []
    for part in content:
        if not isinstance(part, dict):
            blocks.append({"type": "text", "text": str(part)})
            continue
        part_type = str(part.get("type", "")).lower()
        if part_type in ("text", "input_text"):
            blocks.append({"type": "text", "text": str(part.get("text", ""))})
        elif part_type == "tool_result":
            blocks.append({"type": "text", "text": f"[工具结果] {str(part.get('content', ''))}"})
        elif part_type in ("image_url", "image"):
            image_url = part.get("image_url", part)
            url = ""
            if isinstance(image_url, dict):
                url = str(image_url.get("url", "") or "")
            elif isinstance(image_url, str):
                url = image_url
            parsed = _parse_data_url(url)
            if parsed:
                blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": parsed[0], "data": parsed[1]},
                })
            elif url.lower().startswith(("http://", "https://")):
                blocks.append({"type": "image", "source": {"type": "url", "url": url}})
            else:
                # 无法识别的图片载荷：降级为占位文本，避免整轮请求 400
                blocks.append({"type": "text", "text": "[Image]"})
        else:
            blocks.append({"type": "text", "text": str(part)})
    return blocks or [{"type": "text", "text": ""}]


def _normalize_messages(messages: list[dict]) -> tuple[str | None, list[dict]]:
    system_parts: list[str] = []
    chat_messages: list[dict] = []
    for raw in messages or []:
        role = str(raw.get("role", "") or "")
        content = raw.get("content", "")
        text = ""
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") in ("text", "input_text"):
                        parts.append(str(part.get("text", "")))
                    elif part.get("type") == "tool_result":
                        parts.append(f"[工具结果] {str(part.get('content', ''))}")
                    else:
                        parts.append(str(part))
                else:
                    parts.append(str(part))
            text = "\n".join(p for p in parts if p)
        else:
            text = str(content or "")
        if role == "system":
            system_parts.append(text)
            continue
        if role == "tool":
            chat_messages.append({"role": "user", "content": f"[工具结果] {text}"})
            continue
        chat_messages.append({
            "role": role if role in ("user", "assistant") else "user",
            "content": _to_anthropic_content(content),
        })
    system = "\n".join(p for p in system_parts if p) or None
    return system, chat_messages


def _to_anthropic_tools(tools: list[dict] | None) -> list[dict]:
    if not tools:
        return []
    result = []
    for tool in tools:
        fn = tool.get("function", {}) or {}
        params = fn.get("parameters") or {"type": "object", "properties": {}}
        result.append({
            "name": str(fn.get("name", "")),
            "description": str(fn.get("description", "") or ""),
            "input_schema": params,
        })
    return result


def _tool_use_blocks(blocks: list[dict]) -> list[dict]:
    result = []
    for b in blocks or []:
        if isinstance(b, dict) and b.get("type") == "tool_use":
            result.append({
                "id": str(b.get("id", "")),
                "name": str(b.get("name", "")),
                "input": b.get("input", {}) or {},
            })
    return result


class AnthropicProvider(BaseProvider):
    name = "anthropic"
    alias_names = ("anthropic", "claude")
    capabilities = ("chat", "stream")

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.api_key = str(config.get("api_key", "") or "").strip()
        self.api_base = (config.get("api_base", "") or _DEFAULT_BASE).rstrip("/")
        if self.api_base.endswith("/v1"):
            self.api_base = self.api_base[:-3]

    def _endpoint(self) -> str:
        return f"{self.api_base}/v1/messages"

    def _headers(self) -> dict:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": _API_VERSION,
            "Content-Type": "application/json",
        }

    async def get_models(self) -> list[str]:
        # Anthropic Messages API 未提供公开 /models；返回空表示由用户手动填写。
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
        from app.llm.tool_loop import normalize_and_execute_tool_calls

        model = model or self.config.get("model", "claude-3-5-sonnet-20241022")
        system, chat_messages = _normalize_messages(messages)
        anth_tools = _to_anthropic_tools(tools)
        tool_results: list[dict] = []
        total_usage: dict = {}  # 工具多轮的 token 消耗累计（本次请求结束后统一记一次）

        if not self.api_key:
            return LLMResponse(text="", raw=None)

        for _round in range(max_tool_rounds):
            payload: dict = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": chat_messages,
            }
            if system:
                payload["system"] = system
            if anth_tools:
                payload["tools"] = anth_tools

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self._endpoint(),
                        headers=self._headers(),
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                    ) as resp:
                        if resp.status != 200:
                            body = await resp.text()
                            logger.add_info("Anthropic").error(
                                f"Anthropic 请求失败 HTTP {resp.status}: {body}"
                            )
                            return LLMResponse(text="", raw=None, usage=total_usage)
                        data = await resp.json()
            except Exception as e:
                logger.add_info("Anthropic").error(f"Anthropic 请求异常: {format_llm_error(e)}")
                return LLMResponse(text="", raw=None, usage=total_usage)

            # usage：Anthropic 每轮都会报 input/output（缓存读写在 message.usage 里）
            merge_usage(total_usage, data.get("usage", {}) or {})
            content_blocks = data.get("content", []) or []
            text = "".join(
                str(block.get("text", ""))
                for block in content_blocks
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()
            tool_uses = _tool_use_blocks(content_blocks)
            if not tool_uses or tool_executor is None:
                return LLMResponse(text=text, usage=total_usage, raw=data, tool_results=tool_results)

            # Anthropic tool_use → OpenAI-style tool_calls → 共享工具执行器
            tool_calls = [{
                "id": tu["id"],
                "type": "function",
                "function": {
                    "name": tu["name"],
                    "arguments": json.dumps(tu["input"], ensure_ascii=False),
                },
            } for tu in tool_uses]
            normalized, result_messages = await normalize_and_execute_tool_calls(
                tool_calls, tool_executor, tool_results
            )

            # 构造 assistant 内容块 + user tool_result 内容块
            assistant_content = []
            if text:
                assistant_content.append({"type": "text", "text": text})
            for tu in tool_uses:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tu["id"],
                    "name": tu["name"],
                    "input": tu["input"],
                })
            chat_messages.append({"role": "assistant", "content": assistant_content})
            result_content = []
            for msg in result_messages:
                result_content.append({
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                })
            chat_messages.append({"role": "user", "content": result_content})

        logger.add_info("Anthropic").warning(f"工具循环超过 {max_tool_rounds} 轮，强制结束")
        return LLMResponse(text=text, usage=total_usage, raw=data, tool_results=tool_results)

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
        model = model or self.config.get("model", "claude-3-5-sonnet-20241022")
        system, chat_messages = _normalize_messages(messages)
        payload: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "messages": chat_messages,
        }
        if system:
            payload["system"] = system
        anth_tools = _to_anthropic_tools(tools)
        if anth_tools:
            payload["tools"] = anth_tools

        if not self.api_key:
            yield StreamEvent(type="error", text="API 密钥未配置")
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._endpoint(),
                    headers=self._headers(),
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.add_info("Anthropic").error(
                            f"Anthropic 流式请求失败 HTTP {resp.status}: {body}"
                        )
                        yield StreamEvent(type="error", text=f"HTTP {resp.status}: {body}")
                        return
                    # Anthropic 的 usage 分两处：message_start 给 input（含缓存读写），
                    # message_delta 给 output。分别取，不累加（否则 output 会被算两次）。
                    input_usage: dict = {}
                    output_usage: dict = {}
                    async for line in resp.content:
                        line_text = line.decode("utf-8", errors="ignore").strip()
                        if not line_text.startswith("data:"):
                            continue
                        raw = line_text[5:].strip()
                        if not raw:
                            continue
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if data.get("type") == "content_block_delta":
                            delta = data.get("delta", {}) or {}
                            if delta.get("type") == "text_delta":
                                yield StreamEvent(type="text", text=str(delta.get("text", "")))
                        elif data.get("type") == "message_start":
                            message = data.get("message", {}) or {}
                            merge_usage(input_usage, message.get("usage", {}) or {})
                        elif data.get("type") == "message_delta":
                            merge_usage(output_usage, data.get("usage", {}) or {})
                            merged = dict(input_usage)
                            if output_usage.get("output_tokens") is not None:
                                merged["output_tokens"] = output_usage["output_tokens"]
                            if merged:
                                yield StreamEvent(type="usage", usage=merged)
                            yield StreamEvent(type="done", finish_reason=str(data.get("delta", {}).get("stop_reason", "")))
        except Exception as e:
            logger.add_info("Anthropic").error(f"Anthropic 流式请求异常: {format_llm_error(e)}")
            yield StreamEvent(type="error", text=format_llm_error(e))
