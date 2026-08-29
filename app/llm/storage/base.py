"""存储抽象：为会话历史/长期记忆/知识库定义可替换后端接口。

当前默认实现均为 SQLite：
- SQLiteHistoryStore（app.llm.history）
- MemoryStore（app.llm.memory.store）
- KnowledgeStore（app.llm.knowledge.store）

后续新增 Redis / Postgres / 向量库时，只需实现本模块中的接口并在工厂处注册。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class HistoryStore(ABC):
    """会话历史存储接口。"""

    def save_session(self, session) -> None: ...

    def save_conversation(self, session, conv, saved_at=None) -> None: ...

    def load_history(self, task_id: str) -> dict | None: ...

    def find_all_by_session(self, session_id: str) -> list[dict]: ...

    def list_tasks(self, session_id: str | None = None) -> list[dict]: ...

    def export_text(self, task_id: str) -> str | None: ...

    def update_history(self, task_id: str, *, title: str | None = None, messages: list | None = None) -> dict | None: ...

    def delete_history(self, task_id: str) -> bool: ...

    def close(self) -> None: ...


class MemoryStore(ABC):
    """长期记忆存储接口（当前 MemoryStore 已按此接口实现）。"""

    def search(self, *args, **kwargs): ...

    def add(self, *args, **kwargs): ...

    def delete(self, *args, **kwargs): ...

    def close(self) -> None: ...


class KnowledgeStoreInterface(ABC):
    """知识库存储接口（当前 KnowledgeStore 已按此接口实现）。"""

    def add(self, content: str, *, title: str = "", embedding: list[float] | None = None, source: str = "manual") -> str: ...

    def delete(self, cid: str) -> bool: ...

    def get(self, cid: str) -> dict | None: ...

    def list(self, limit: int = 100) -> list[dict]: ...

    def search(self, embedding: list[float], *, limit: int = 5) -> list[dict]: ...

    def close(self) -> None: ...


__all__ = ["HistoryStore", "MemoryStore", "KnowledgeStoreInterface"]
