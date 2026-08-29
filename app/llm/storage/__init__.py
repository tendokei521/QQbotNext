"""存储后端工厂与导出。"""

from __future__ import annotations

from typing import Any

from .base import HistoryStore, KnowledgeStoreInterface, MemoryStore


def create_history_store(bot_id: Any, backend: str = "sqlite", db_path: str | None = None) -> HistoryStore:
    """创建历史存储实例。

    当前只实现 SQLite；后续可扩展 redis / postgres 等 backend。
    """
    if backend != "sqlite":
        raise ValueError(f"不支持的历史存储后端: {backend}")
    from app.llm.history import SQLiteHistoryStore

    return SQLiteHistoryStore(bot_id, db_path=db_path)


def create_memory_store(bot_id: Any, backend: str = "sqlite", db_path: str | None = None) -> MemoryStore:
    """创建长期记忆存储实例。"""
    if backend != "sqlite":
        raise ValueError(f"不支持的记忆存储后端: {backend}")
    from app.llm.memory.store import MemoryStore as SQLiteMemoryStore

    return SQLiteMemoryStore(bot_id, db_path=db_path)


def create_knowledge_store(bot_id: Any, backend: str = "sqlite", db_path: str | None = None) -> KnowledgeStoreInterface:
    """创建知识库存储实例。"""
    if backend != "sqlite":
        raise ValueError(f"不支持的知识库存储后端: {backend}")
    from app.llm.knowledge.store import KnowledgeStore

    return KnowledgeStore(bot_id, db_path=db_path)


__all__ = [
    "HistoryStore",
    "MemoryStore",
    "KnowledgeStoreInterface",
    "create_history_store",
    "create_memory_store",
    "create_knowledge_store",
]
