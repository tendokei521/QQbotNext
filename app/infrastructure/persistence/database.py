"""SQLite 持久化：数据库连接与建表。

所有有状态数据（模块配置、权限、Bot 配置、WebUI 偏好）统一落库，
临时/可丢数据仍走缓存（app/infrastructure/cache.py）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Sequence

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

-- 每个连接（index）上一次成功登录的账号快照：
-- 断开后运行时状态会清空，前端据此回退展示「上次连接的账号」，连接成功时刷新。
CREATE TABLE IF NOT EXISTS bot_accounts (
    bot_index     INTEGER PRIMARY KEY,
    user_id       TEXT,
    nickname      TEXT NOT NULL DEFAULT '',
    last_login_at INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS webui_config (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_presets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    provider    TEXT NOT NULL DEFAULT 'openai',
    config_json TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_models (
    id          TEXT PRIMARY KEY,
    preset_id   TEXT NOT NULL,
    model       TEXT NOT NULL,
    provider_type TEXT NOT NULL DEFAULT 'chat',
    enabled     INTEGER NOT NULL DEFAULT 1,
    config_json TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_settings (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config_profiles (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    config_json TEXT NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS config_routes (
    umo        TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS role_presets (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    system_prompt TEXT NOT NULL,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);
"""


class Database:
    """AIOSQLite 封装：单一异步连接 + 便捷查询/事务。"""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        logger.debug(f"[DB] SQLite 已初始化") #: "){self.path}")

    async def table_columns(self, table: str) -> set[str]:
        """返回表的列名集合（老库迁移探测用）。"""
        rows = await self.fetchall(f"PRAGMA table_info({table})")
        return {r["name"] for r in rows}

    async def ensure_columns(self, table: str, additions: dict[str, str]) -> None:
        """老库迁移：为既有表补齐新增列（幂等，列已存在则跳过）。"""
        cols = await self.table_columns(table)
        for name, ddl in additions.items():
            if name not in cols:
                await self.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                logger.info(f"[DB] 迁移：{table} 新增列 {name}")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            await self._conn.commit()
            return cur.rowcount

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
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
