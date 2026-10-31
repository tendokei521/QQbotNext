"""知识库管理器：向量化、存取、检索。

依赖 Provider 预设中的 embedding 模型实例；未配置时相关工具返回可读错误。
"""

from __future__ import annotations

from typing import Any

from app.agent.providers import get_provider
from app.core.logger import account_label, logger

from .store import KnowledgeStore


class KnowledgeManager:
    """按 AgentRuntime 持有的知识库管理器。"""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.bot_id = getattr(runtime, "bot_id", "?")
        self.store = KnowledgeStore(self.bot_id)

    def _get(self, key: str, default: Any = None) -> Any:
        # 知识库策略键归 `llm_knowledge` 模块所有：接管时视图挂在
        # `runtime.knowledge_options`，未接管时回退 agent 配置（行为不变）。
        from app.agent.policy import policy_get

        return policy_get(self.runtime, "knowledge", key, default)

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
            logger.add_info(account_label(self.bot_id)).warning(f"[知识库] 创建 Embedding Provider 失败: {e}")
            return None

    async def add_text(self, content: str, *, title: str = "", source: str = "manual") -> tuple[str | None, str]:
        # 归属：embedding 也烧 token，记账时要能看出是知识库花的
        from app.agent.usage.context import usage_scope

        with usage_scope(bot_id=self.bot_id, source="llm_knowledge"):
            return await self._add_text_inner(content, title=title, source=source)

    async def _add_text_inner(
        self, content: str, *, title: str = "", source: str = "manual"
    ) -> tuple[str | None, str]:
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
            logger.add_info(account_label(self.bot_id)).warning(f"[知识库] 检索向量化失败: {e}")
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
        except Exception as e:
            logger.debug(f"[Knowledge] 关闭知识库存储失败（已忽略）: {e}")
