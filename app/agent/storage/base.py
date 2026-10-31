"""存储抽象：为会话历史/长期记忆定义可替换后端接口。

当前默认实现均为 SQLite：
- SQLiteHistoryStore（app.agent.session.history）
- MemoryStore（app.plugins.llm_memory.src.store）

后续新增 Redis / Postgres / 向量库时，只需实现本模块中的接口并在工厂处注册。
"""

from __future__ import annotations

from abc import ABC


class HistoryStore(ABC):
    """会话历史存储接口。"""

    def save_session(self, session) -> None: ...

    def save_conversation(self, session, conv, saved_at=None) -> None: ...

    def load_history(self, task_id: str) -> dict | None: ...

    def find_all_by_session(self, session_id: str) -> list[dict]: ...

    def list_tasks(self, session_id: str | None = None) -> list[dict]: ...

    def export_text(self, task_id: str) -> str | None: ...

    def update_history(self, task_id: str, *, title: str | None = None, messages: list | None = None) -> dict | None: ...

    def import_conversation(self, data: dict) -> dict: ...

    def delete_history(self, task_id: str) -> bool: ...

    def close(self) -> None: ...


class MemoryStore(ABC):
    """长期记忆存储接口（当前 MemoryStore 已按此接口实现）。"""

    def search(self, *args, **kwargs): ...

    def add(self, *args, **kwargs): ...

    def delete(self, *args, **kwargs): ...

    def close(self) -> None: ...


__all__ = ["HistoryStore", "MemoryStore"]
