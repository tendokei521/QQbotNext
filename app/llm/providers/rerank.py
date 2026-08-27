"""Rerank Provider：Jina / Cohere 兼容。

返回按相关度从高到低排序后的原文档索引；用于知识库检索二次排序。
"""

from __future__ import annotations

import aiohttp

from .base import BaseProvider


class JinaRerankProvider(BaseProvider):
    name = "jina_rerank"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.api_key = config.get("api_key", "")
        self.api_base = (
            config.get("api_base", "") or "https://api.jina.ai"
        ).rstrip("/")
        self.model = config.get("model", "jina-reranker-v2-base-multilingual")
        self.timeout = int(config.get("timeout", 30) or 30)

    def _endpoint(self) -> str:
        base = self.api_base.rstrip("/")
        if base.endswith("/rerank"):
            return base
        if base.endswith("/v1"):
            return base + "/rerank"
        return base + "/v1/rerank"

    async def rerank(self, query: str, documents: list[str], *, top_n: int | None = None) -> list[int]:
        if not self.api_key:
            raise ValueError("Rerank API 密钥未配置")
        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
        }
        if top_n:
            payload["top_n"] = top_n
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._endpoint(),
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise ValueError(f"Rerank 请求失败 HTTP {resp.status}: {body[:200]}")
                result = await resp.json()
        results = result.get("results") or []
        return [int(item.get("index", 0) or 0) for item in results if isinstance(item, dict)]


class CohereRerankProvider(BaseProvider):
    name = "cohere_rerank"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.api_key = config.get("api_key", "")
        self.api_base = (
            config.get("api_base", "") or "https://api.cohere.ai"
        ).rstrip("/")
        self.model = config.get("model", "rerank-multilingual-v3.0")
        self.timeout = int(config.get("timeout", 30) or 30)

    def _endpoint(self) -> str:
        base = self.api_base.rstrip("/")
        if base.endswith("/rerank"):
            return base
        return base + "/v2/rerank"

    async def rerank(self, query: str, documents: list[str], *, top_n: int | None = None) -> list[int]:
        if not self.api_key:
            raise ValueError("Rerank API 密钥未配置")
        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
        }
        if top_n:
            payload["top_n"] = top_n
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._endpoint(),
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise ValueError(f"Rerank 请求失败 HTTP {resp.status}: {body[:200]}")
                result = await resp.json()
        results = result.get("results") or []
        return [int(item.get("index", 0) or 0) for item in results if isinstance(item, dict)]


def get_rerank_provider(config: dict) -> BaseProvider:
    provider = str((config or {}).get("provider", "") or "").lower()
    if "cohere" in provider:
        return CohereRerankProvider(config)
    return JinaRerankProvider(config)
