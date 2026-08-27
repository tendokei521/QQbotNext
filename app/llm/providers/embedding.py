"""OpenAI 兼容 Embedding Provider。

通过 ``POST /v1/embeddings`` 将文本转成向量，供知识库 / 记忆检索使用。
"""

from __future__ import annotations

import aiohttp

from app.llm import logger
from .base import BaseProvider


class OpenAIEmbeddingProvider(BaseProvider):
    name = "openai_embedding"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.api_key = config.get("api_key", "")
        self.api_base = (config.get("api_base", "") or "https://api.deepseek.com").rstrip("/")
        self.model = config.get("model", "text-embedding-3-small")
        self.timeout = int(config.get("timeout", 30) or 30)

    def _base(self) -> str:
        base = self.api_base.rstrip("/")
        if base.endswith("/embeddings"):
            base = base[: -len("/embeddings")].rstrip("/")
        if base.endswith("/v1"):
            return base
        return base + "/v1"

    def _endpoint(self) -> str:
        base = self.api_base.rstrip("/")
        if base.endswith("/embeddings"):
            return base
        return f"{self._base()}/embeddings"

    async def get_models(self) -> list[str]:
        if not self.api_key:
            return []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._base()}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
            return [str(item.get("id")) for item in (data.get("data") or []) if item.get("id")]
        except Exception:
            return []

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        if not self.api_key:
            raise ValueError("Embedding API 密钥未配置")
        payload = {
            "model": model or self.model,
            "input": texts,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._endpoint(),
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise ValueError(f"Embedding 请求失败 HTTP {resp.status}: {body[:200]}")
                result = await resp.json()
        items = (result.get("data") or [])
        items.sort(key=lambda x: int(x.get("index", 0) or 0))
        vectors = []
        for item in items:
            vectors.append([float(v) for v in (item.get("embedding") or [])])
        if len(vectors) != len(texts):
            logger.add_info("Embedding").warning(
                f"Embedding 返回数量 {len(vectors)} != 输入 {len(texts)}"
            )
        return vectors
