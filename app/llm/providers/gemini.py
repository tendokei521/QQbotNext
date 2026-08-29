"""Google Gemini Provider（Generative Language API 兼容层）。

覆盖：
- generateContent 非流式
- streamGenerateContent 流式
- 基础 tools 声明（functionDeclarations）

API 形态说明：Gemini 的流式默认按 `alt=sse` 返回，可被本适配器解析。
"""

from __future__ import annotations

import json

import aiohttp

from app.llm import logger
from .base import BaseProvider, LLMResponse, StreamEvent, format_llm_error

_DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"


def _normalize_messages(messages: list[dict]) -> tuple[list[dict], str | None]:
    system_parts: list[str] = []
    contents: list[dict] = []
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
            contents.append({"role": "user", "parts": [{"text": f"[工具结果] {text}"}]})
            continue
        gemini_role = "model" if role == "assistant" else "user"
        contents.append({"role": gemini_role, "parts": [{"text": text}]})
    system = "\n".join(p for p in system_parts if p) or None
    return contents, system


def _to_gemini_tools(tools: list[dict] | None) -> list[dict]:
    if not tools:
        return []
    declarations = []
    for tool in tools:
        fn = tool.get("function", {}) or {}
        declarations.append({
            "name": str(fn.get("name", "")),
            "description": str(fn.get("description", "") or ""),
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return [{"functionDeclarations": declarations}] if declarations else []


class GeminiProvider(BaseProvider):
    name = "gemini"
    alias_names = ("gemini", "google")
    capabilities = ("chat", "stream")

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.api_key = str(config.get("api_key", "") or "").strip()
        self.api_base = (config.get("api_base", "") or _DEFAULT_BASE).rstrip("/")

    def _url(self, model: str, stream: bool = False) -> str:
        if self.api_base.endswith("/v1beta"):
            base = self.api_base
        else:
            base = self.api_base.rstrip("/") + "/v1beta"
        action = "streamGenerateContent?alt=sse" if stream else "generateContent"
        return f"{base}/models/{model}:{action}&key={self.api_key}"

    async def get_models(self) -> list[str]:
        if not self.api_key:
            return []
        url = f"{self.api_base.rstrip('/')}/v1beta/models?key={self.api_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
            return [str(m.get("name", "")).split("/")[-1] for m in data.get("models", []) if isinstance(m, dict)]
        except Exception:
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
        model = model or self.config.get("model", "gemini-2.0-flash")
        contents, system = _normalize_messages(messages)
        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        gemini_tools = _to_gemini_tools(tools)
        if gemini_tools:
            payload["tools"] = gemini_tools

        if not self.api_key:
            return LLMResponse(text="", raw=None)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._url(model, stream=False),
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.add_info("Gemini").error(
                            f"Gemini 请求失败 HTTP {resp.status}: {body[:200]}"
                        )
                        return LLMResponse(text="", raw=None)
                    data = await resp.json()
        except Exception as e:
            logger.add_info("Gemini").error(f"Gemini 请求异常: {format_llm_error(e)}")
            return LLMResponse(text="", raw=None)

        candidates = data.get("candidates", []) or []
        parts = []
        if candidates:
            content = (candidates[0].get("content", {}) or {})
            parts = content.get("parts", []) or []
        text = "".join(
            str(p.get("text", ""))
            for p in parts
            if isinstance(p, dict) and p.get("text")
        ).strip()
        usage = data.get("usageMetadata", {}) or {}
        normalized_usage = {
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
        }
        return LLMResponse(text=text, usage=normalized_usage, raw=data)

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
        model = model or self.config.get("model", "gemini-2.0-flash")
        contents, system = _normalize_messages(messages)
        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        gemini_tools = _to_gemini_tools(tools)
        if gemini_tools:
            payload["tools"] = gemini_tools

        if not self.api_key:
            yield StreamEvent(type="error", text="API 密钥未配置")
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._url(model, stream=True),
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.add_info("Gemini").error(
                            f"Gemini 流式请求失败 HTTP {resp.status}: {body[:200]}"
                        )
                        yield StreamEvent(type="error", text=f"HTTP {resp.status}: {body[:200]}")
                        return
                    async for line in resp.content:
                        line_text = line.decode("utf-8", errors="ignore").strip()
                        if not line_text.startswith("data:"):
                            continue
                        raw = line_text[5:].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        candidates = data.get("candidates", []) or []
                        if candidates:
                            content = (candidates[0].get("content", {}) or {})
                            parts = content.get("parts", []) or []
                            for p in parts:
                                if isinstance(p, dict) and p.get("text"):
                                    yield StreamEvent(type="text", text=str(p["text"]))
        except Exception as e:
            logger.add_info("Gemini").error(f"Gemini 流式请求异常: {format_llm_error(e)}")
            yield StreamEvent(type="error", text=format_llm_error(e))
