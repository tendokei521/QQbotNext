"""SQLite 持久化：数据库连接与建表。

所有有状态数据（模块配置、权限、Bot 配置、WebUI 偏好）统一落库，
临时/可丢数据仍走缓存（app/infrastructure/cache.py）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional, Sequence

import aiosqlite

from app.core.logger import logger

SCHEMA = """
CREATE TABLE IF NOT EXISTS module_config (
    module_name TEXT NOT NULL,
    bot_id      TEXT,                 -- NULL 表示全局默认
    config_json TEXT NOT NULL,
    updated_at  INTEGER NOT NULL,
    PRIMARY KEY (module_name, bot_id)
);

CREATE TABLE IF NOT EXISTS module_authority (
    module_name   TEXT NOT NULL,
    bot_id        TEXT,
    enabled       INTEGER NOT NULL DEFAULT 1,
    group_mode    TEXT NOT NULL DEFAULT 'blacklist',
    group_list    TEXT NOT NULL DEFAULT '[]',
    user_mode     TEXT NOT NULL DEFAULT 'blacklist',
    user_list     TEXT NOT NULL DEFAULT '[]',
    updated_at    INTEGER NOT NULL,
    PRIMARY KEY (module_name, bot_id)
);

CREATE TABLE IF NOT EXISTS bots (
    bot_index   INTEGER PRIMARY KEY,
    ws_url      TEXT NOT NULL DEFAULT '',
    owner_id    TEXT,
    auto_connect INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS webui_config (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
"""


class Database:
    """AIOSQLite 封装：单一异步连接 + 便捷查询/事务。"""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        logger.debug(f"[DB] SQLite 已初始化: {self.path}")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            await self._conn.commit()
            return cur.rowcount

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            row = await cur.fetchone()
            return dict(row) if row else None

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def run_in_transaction(self, sqls: Sequence[tuple[str, Sequence[Any]]]) -> None:
        """批量执行并原子提交。"""
        async with self._lock:
            await self._conn.execute("BEGIN")
            try:
                for sql, params in sqls:
                    await self._conn.execute(sql, params)
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise

    @staticmethod
    def dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def loads(value: str, default: Any = None) -> Any:
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default if default is not None else {}
