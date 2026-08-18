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

from app.llm import logger
from .base import BaseProvider, LLMResponse, StreamEvent

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


class _FatalError(Exception):
    """不可恢复错误（认证/参数）：不重试。"""


class _AuthError(Exception):
    """认证失败（401/403）：当前 key 无效，轮换到下一个 key。"""


def _split_keys(api_key: str) -> list[str]:
    if not api_key:
        return []
    if isinstance(api_key, str):
        return [k.strip() for k in api_key.replace("，", ",").replace("\n", ",").split(",") if k.strip()]
    return [str(api_key)]


class OpenAICompatProvider(BaseProvider):
    name = "openai"
    alias_names = ("openai", "deepseek", "openrouter", "moonshot", "zhipu", "ollama", "lm_studio", "siliconflow")

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.api_keys = _split_keys(config.get("api_key", "")) or [""]
        self.api_base = (config.get("api_base", "") or "https://api.deepseek.com").rstrip("/")
        self.max_retries = max(1, int(config.get("retry_attempts", 3) or 3))

    async def get_models(self) -> list[str]:
        """拉取 OpenAI 兼容 /models 列表。"""
        if not self.api_keys or not self.api_keys[0]:
            return []
        base = self.api_base
        if not base.endswith("/v1"):
            base += "/v1"
        url = f"{base}/models"
        headers = {
            "Authorization": f"Bearer {self.api_keys[0]}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    logger.add_info("Api").error(f"拉取模型列表失败 HTTP {resp.status}")
                    return []
                data = await resp.json()
        return [str(item.get("id")) for item in (data.get("data") or []) if isinstance(item, dict) and item.get("id")]

    def _endpoint(self) -> str:
        base = self.api_base
        if not base.endswith("/v1"):
            base += "/v1"
        return f"{base}/chat/completions"

    # ---------- 底层请求 ----------
    async def _post(self, api_key: str, payload: dict, timeout: int) -> dict:
        """发送单次请求，返回解析后的 JSON。非可重试错误抛 _FatalError。"""
        if not api_key:
            raise _FatalError("API 密钥未配置")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._endpoint(), headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    if resp.status in (401, 403):
                        raise _AuthError(f"HTTP {resp.status}: {body[:200]}")
                    if resp.status in RETRYABLE_STATUS:
                        raise ConnectionError(f"HTTP {resp.status}: {body[:200]}")
                    raise _FatalError(f"HTTP {resp.status}: {body[:200]}")
                return await resp.json()

    async def _stream_once(self, api_key: str, payload: dict, timeout: int):
        """发起一次流式 HTTP 请求，逐块产出 StreamEvent。"""
        if not api_key:
            raise _FatalError("API 密钥未配置")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._endpoint(), headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    if resp.status in (401, 403):
                        raise _AuthError(f"HTTP {resp.status}: {body[:200]}")
                    if resp.status in RETRYABLE_STATUS:
                        raise ConnectionError(f"HTTP {resp.status}: {body[:200]}")
                    raise _FatalError(f"HTTP {resp.status}: {body[:200]}")
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
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {}) or {}
                    finish_reason = choices[0].get("finish_reason", "")
                    if delta.get("content"):
                        yield StreamEvent(type="text", text=delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        yield StreamEvent(type="tool_call", tool_call=tc)
                    if finish_reason:
                        yield StreamEvent(type="done", finish_reason=finish_reason)

    async def _request(self, payload: dict, timeout: int) -> dict | None:
        """带重试的请求。认证失败自动轮换 key；最终失败返回 None。"""
        last_error = ""
        for attempt in range(self.max_retries):
            key = self.api_keys[attempt % len(self.api_keys)]
            try:
                return await self._post(key, payload, timeout)
            except _AuthError as e:
                last_error = str(e)
                if attempt < self.max_retries - 1:
                    logger.add_info("Api").warning(
                        f"API key {attempt % len(self.api_keys) + 1} 认证失败，轮换下一个: {e}"
                    )
                    await asyncio.sleep(0.5)
                else:
                    logger.add_info("Api").error(f"所有 API key 认证均失败: {last_error}")
                    return None
            except _FatalError as e:
                logger.add_info("Api").error(f"API 不可恢复错误: {e}")
                return None
            except Exception as e:
                last_error = str(e)
                if attempt < self.max_retries - 1:
                    delay = 2 ** attempt
                    logger.add_info("Api").warning(
                        f"API 请求失败，{delay}s 后重试 ({attempt + 1}/{self.max_retries}): {last_error}"
                    )
                    await asyncio.sleep(delay)
        logger.add_info("Api").error(f"API 请求最终失败: {last_error}")
        return None

    # ---------- 响应解析 ----------
    def _to_response(self, result: dict, tool_results: list[dict] | None = None) -> LLMResponse:
        usage = result.get("usage", {}) or {}
        choices = result.get("choices", []) or []
        content = ""
        reasoning = ""
        if choices:
            first = choices[0]
            message = first.get("message", {}) or {}
            content = (message.get("content") or "").strip()
            reasoning = (message.get("reasoning_content") or "") or ""
            if first.get("finish_reason") == "length":
                logger.add_info("Api").warning("回复因 max_tokens 限制被截断")
        logger.add_info("Api").info(
            f"回复 {len(content)} 字符 | 输入 {usage.get('prompt_tokens', '?')} / "
            f"输出 {usage.get('completion_tokens', '?')} tokens"
        )
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

        tool_results: list[dict] = []
        for _round in range(max_tool_rounds):
            result = await self._request(payload, timeout)
            if result is None:
                return LLMResponse(text="", raw=None)
            choices = result.get("choices", []) or []
            if not choices:
                return LLMResponse(text="", raw=None)
            message = choices[0].get("message", {}) or {}
            tool_calls = message.get("tool_calls") or []
            if not tool_calls or tool_executor is None:
                return self._to_response(result, tool_results)

            # 工具循环：保留 assistant tool_calls，追加各工具结果，重发
            payload["messages"].append({
                "role": "assistant",
                "content": message.get("content") or None,
                "tool_calls": tool_calls,
            })
            for tc in tool_calls:
                fn = tc.get("function", {}) or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                try:
                    exec_result = await tool_executor(name, args)
                except Exception as e:
                    exec_result = f"error: 工具执行异常 {e}"
                if not isinstance(exec_result, str):
                    exec_result = str(exec_result)
                tool_results.append({"name": name, "args": args, "result": exec_result})
                payload["messages"].append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": exec_result,
                })

        logger.add_info("Api").warning(f"工具循环超过 {max_tool_rounds} 轮，强制结束")
        # 保留已执行工具结果与最后一次响应，避免已完成的工具调用静默丢失
        return self._to_response(result, tool_results)

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
                        f"API key {attempt % len(self.api_keys) + 1} 认证失败，轮换下一个: {e}"
                    )
                    await asyncio.sleep(0.5)
                    continue
                yield StreamEvent(type="error", text=str(e))
                return
            except _FatalError as e:
                yield StreamEvent(type="error", text=str(e))
                return
            except Exception as e:
                if started or attempt >= self.max_retries - 1:
                    yield StreamEvent(type="error", text=str(e))
                    return
                logger.add_info("Api").warning(
                    f"流式请求失败，{2 ** attempt}s 后重试 ({attempt + 1}/{self.max_retries}): {e}"
                )
                await asyncio.sleep(2 ** attempt)

        yield StreamEvent(type="error", text="流式请求最终失败")
