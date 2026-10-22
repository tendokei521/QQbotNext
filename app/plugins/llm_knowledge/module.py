"""知识库模块：接管知识库策略配置（3 键）。

机制仍是框架的 `app/agent/knowledge/*`（向量化/存取/检索），
本模块提供策略配置：`on_load` 挂 `runtime.knowledge_options`，
`KnowledgeManager._get()` 走 `policy_config(runtime, "knowledge")`。
"""

from __future__ import annotations

from typing import Any

from app.plugin import BaseModule

from .config_schema import SCHEMA as KNOWLEDGE_SCHEMA

#: 本模块持有的键（与 `config_partition.MODULE_KEYS["llm_knowledge"]` 一致）
OWNED_KEYS: tuple[str, ...] = (
    "knowledge_enable",
    "knowledge_embedding_model_id",
    "knowledge_recall_limit",
)


class Module(BaseModule):
    name = "知识库"
    sign = "LlmKnowledge"
    description = "把资料向量化后按需检索，作为回答的依据"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    order = 60
    provides = ("knowledge",)
    supersedes = ("knowledge",)
    default_config = {
        "knowledge_enable": False,
        "knowledge_embedding_model_id": "",
        "knowledge_recall_limit": 5,
    }
    config_schema = KNOWLEDGE_SCHEMA

    async def on_load(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        from app.agent.delivery.output import ConfigView

        runtime.knowledge_options = ConfigView(
            getattr(runtime, "config", None), self.config, OWNED_KEYS
        )

    async def on_unload(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        runtime.knowledge_options = None

    def _runtime(self) -> Any:
        services = getattr(getattr(self, "ctx", None), "services", None)
        getter = getattr(services, "runtime", None)
        return getter(self.bot_id) if callable(getter) else None

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("knowledge_enable", False))


def owned_keys() -> tuple[str, ...]:
    """本模块持有的配置键（供迁移脚本与契约测试核对）。"""
    return OWNED_KEYS
