"""通用 Repository 基类：为有状态数据提供统一的 CRUD 骨架。"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from app.infrastructure.persistence.database import Database

T = TypeVar("T")


class BaseRepository(Generic[T]):
    """仓库基类：持有 Database 引用，子类实现具体表操作。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def save(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError

    async def get(self, *args: Any, **kwargs: Any) -> T | None:
        raise NotImplementedError
