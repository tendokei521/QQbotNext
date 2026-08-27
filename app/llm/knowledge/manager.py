"""知识库管理器：向量化、存取、检索。

依赖 Provider 预设中的 embedding 模型实例；未配置时相关工具返回可读错误。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger
from app.llm.providers import get_provider
from app.llm.knowledge.store import KnowledgeStore


class KnowledgeManager:
    """按 AgentRuntime 持有的知识库管理器。"""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.bot_id = getattr(runtime, "bot_id", "?")
        self.store = KnowledgeStore(self.bot_id)

    def _get(self, key: str, default: Any = None) -> Any:
        try:
            return self.runtime.config.get(key, default)
        except Exception:
            return default

    def enabled(self) -> bool:
        return bool(self._get("knowledge_enable", False))

    async def _embedding_provider(self):
        """返回可用的 embedding Provider；找不到返回 None。"""
        provider_manager = getattr(self.runtime, "provider_runtime_manager", None)
        if provider_manager is None:
            return None
        model_id = str(self._get("knowledge_embedding_model_id", "") or "")
        config = None
        if model_id:
            config = provider_manager.resolve_provider_config(model_id)
        else:
            models = self.runtime.config_service.list_provider_models()
            for model in models:
                if model.get("provider_type") == "embedding" and model.get("enabled", True):
                    config = provider_manager.resolve_provider_config(model.get("id", ""))
                    if config:
                        break
        if not config:
            return None
        try:
            return get_provider(config)
        except Exception as e:
            logger.add_info(f"#{self.bot_id}").warning(f"[知识库] 创建 Embedding Provider 失败: {e}")
            return None

    async def add_text(self, content: str, *, title: str = "", source: str = "manual") -> tuple[str | None, str]:
        provider = await self._embedding_provider()
        if provider is None:
            return None, "未配置 embedding 模型（请在 Provider 预设中创建能力类型为 Embedding 的模型）"
        content = str(content or "").strip()
        if not content:
            return None, "内容不能为空"
        try:
            vectors = await provider.embed([content])
            if not vectors:
                return None, "Embedding 返回为空"
        except Exception as e:
            return None, f"Embedding 失败: {e}"
        cid = self.store.add(content, title=title, embedding=vectors[0], source=source)
        return cid, "success"

    async def search(self, query: str, *, limit: int = 5) -> list[dict]:
        provider = await self._embedding_provider()
        if provider is None:
            return []
        query = str(query or "").strip()
        if not query:
            return []
        try:
            vectors = await provider.embed([query])
        except Exception as e:
            logger.add_info(f"#{self.bot_id}").warning(f"[知识库] 检索向量化失败: {e}")
            return []
        if not vectors:
            return []
        return self.store.search(vectors[0], limit=limit)

    def list(self, limit: int = 100) -> list[dict]:
        return self.store.list(limit)

    def delete(self, cid: str) -> bool:
        return self.store.delete(cid)

    def stop(self) -> None:
        try:
            self.store.close()
        except Exception:
            pass
