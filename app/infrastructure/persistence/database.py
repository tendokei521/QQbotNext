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

-- 作用域绑定：模块配置/权限/开关的统一存储（docs/multi-account-design.md §4）。
-- scope_type: global（scope_id=''） / account（scope_id=account_id） / channel（scope_id=群号）；
-- enabled 为 NULL 表示"本层不覆盖"，解析时沿用更宽作用域。
CREATE TABLE IF NOT EXISTS bindings (
    capability_id   TEXT NOT NULL,
    scope_type      TEXT NOT NULL,
    scope_id        TEXT NOT NULL DEFAULT '',
    enabled         INTEGER,
    config_json     TEXT NOT NULL DEFAULT '{}',
    permission_json TEXT NOT NULL DEFAULT '{}',
    priority        INTEGER NOT NULL DEFAULT 100,
    updated_at      INTEGER NOT NULL,
    PRIMARY KEY (capability_id, scope_type, scope_id)
);

-- ==================== 套装应用（P3） ====================
-- 一次「应用到账号」= 一条激活记录；快照用于回滚，状态机：
-- applying → active / failed；active → rolled_back（手动回滚或卸载）
CREATE TABLE IF NOT EXISTS bundle_activations (
    id            TEXT PRIMARY KEY,
    bundle_id     TEXT NOT NULL,
    account_id    TEXT NOT NULL,
    status        TEXT NOT NULL,
    plan_json     TEXT NOT NULL DEFAULT '{}',
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    applied_json  TEXT NOT NULL DEFAULT '{}',
    error         TEXT NOT NULL DEFAULT '',
    started_at    INTEGER NOT NULL,
    finished_at   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_bundle_act_account ON bundle_activations(account_id, status);

-- 本套装创建的对象（卸载/回滚时只删这些，用户后建的不动）
CREATE TABLE IF NOT EXISTS bundle_objects (
    bundle_id  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    object_id  TEXT NOT NULL,
    account_id TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    PRIMARY KEY (bundle_id, kind, object_id)
);

-- 账号当前激活的套装（纯元数据：UI 显示 / 卸载前置检查；不参与运行时解析）
CREATE TABLE IF NOT EXISTS account_active_bundle (
    account_id    TEXT PRIMARY KEY,
    bundle_id     TEXT NOT NULL,
    activation_id TEXT NOT NULL,
    updated_at    INTEGER NOT NULL
);

-- 群级覆盖（对话策略）：账号 × 能力 × 群。
-- capability='agent' 或模块名；channel_id='' 表示该账号的默认层。
-- enabled 为 NULL 表示"本层不覆盖"；config_json 只存白名单键（domain/dialogue_policy.py）。
CREATE TABLE IF NOT EXISTS channel_overrides (
    account_id      TEXT NOT NULL,
    capability      TEXT NOT NULL DEFAULT 'agent',
    channel_id      TEXT NOT NULL DEFAULT '',
    enabled         INTEGER,
    config_json     TEXT NOT NULL DEFAULT '{}',
    permission_json TEXT NOT NULL DEFAULT '{}',
    muted_until     INTEGER NOT NULL DEFAULT 0,
    updated_at      INTEGER NOT NULL,
    PRIMARY KEY (account_id, capability, channel_id)
);

-- 群列表缓存：get_group_list 的结果落库，离线也能看到"上次有哪些群"。
-- in_group=0 表示"上次刷新时已不在该群"，保留行以便历史设置不丢。
CREATE TABLE IF NOT EXISTS known_groups (
    account_id   TEXT NOT NULL,
    group_id     TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT '',
    member_count INTEGER NOT NULL DEFAULT 0,
    max_member_count INTEGER NOT NULL DEFAULT 0,
    in_group     INTEGER NOT NULL DEFAULT 1,
    updated_at   INTEGER NOT NULL,
    PRIMARY KEY (account_id, group_id)
);

-- 群响应权策略：同一群里由哪个账号响应某能力（docs/multi-account-design.md §6）。
CREATE TABLE IF NOT EXISTS channel_policies (
    capability_id      TEXT NOT NULL,
    channel_id         TEXT NOT NULL,
    policy             TEXT NOT NULL DEFAULT 'all',
    primary_account_id TEXT,
    fallback           TEXT NOT NULL DEFAULT 'open',
    updated_at         INTEGER NOT NULL,
    PRIMARY KEY (capability_id, channel_id)
);

CREATE TABLE IF NOT EXISTS bots (
    -- account_id：用户「添加账号」时自己填的唯一 id；前后端判断与所有持久化 key 都用它。
    -- 创建后不可改（改名只改 name），因此可以安全地当外部引用。
    account_id   TEXT PRIMARY KEY,
    -- name：纯展示名。日志、WebUI 一律显示它；id 只用于判断，不给用户看。
    name         TEXT NOT NULL DEFAULT '',
    ws_url      TEXT NOT NULL DEFAULT '',
    owner_id    TEXT,
    auto_connect INTEGER NOT NULL DEFAULT 0,
    -- 连接方向：forward=正向 WS（框架连出）/ reverse=反向 WS（框架监听、OneBot 实现连入）
    conn_mode   TEXT NOT NULL DEFAULT 'forward',
    -- 仅用于界面排序：id 不可变，所以顺序必须单独存，不能靠数组下标
    sort_order  INTEGER NOT NULL DEFAULT 0
);

-- 每个账号上一次成功登录的 QQ 快照：
-- 断开后运行时状态会清空，前端据此回退展示「上次连接的账号」，连接成功时刷新。
CREATE TABLE IF NOT EXISTS bot_accounts (
    account_id    TEXT PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS role_presets (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    system_prompt TEXT NOT NULL,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

-- ==================== 数据统计（单表，kind 区分三类同级统计）====================
-- 设计见 docs/DATA_STATS_DESIGN.md
CREATE TABLE IF NOT EXISTS stat_event (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    ts              INTEGER NOT NULL,          -- UTC 秒
    day_local       TEXT    NOT NULL,          -- 'YYYY-MM-DD'（本地日，进索引）
    kind            TEXT    NOT NULL,          -- llm | msg_in | msg_out

    bot_id          TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL DEFAULT '',  -- 模块名；绕过总线的为 'unknown'
    api             TEXT NOT NULL DEFAULT '',  -- provider 名
    model           TEXT NOT NULL DEFAULT '',  -- 实际服务模型
    session_id      TEXT NOT NULL DEFAULT '',
    session_label   TEXT NOT NULL DEFAULT '',  -- '名字前5字…group123456'
    group_id        TEXT NOT NULL DEFAULT '',
    user_id         TEXT NOT NULL DEFAULT '',
    event_type      TEXT NOT NULL DEFAULT '',
    direction       TEXT NOT NULL DEFAULT '',  -- in | out
    outcome         TEXT NOT NULL DEFAULT '',  -- msg_in: ignored|command|module|llm

    requests        INTEGER NOT NULL DEFAULT 0,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_hit       INTEGER,                   -- NULL = 上游未报（≠ 0）
    cache_miss      INTEGER,                   -- NULL = 上游未报（≠ 0）
    tokens_total    INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    messages        INTEGER NOT NULL DEFAULT 0,
    segments        INTEGER NOT NULL DEFAULT 0,
    bytes           INTEGER NOT NULL DEFAULT 0,

    ok              INTEGER NOT NULL DEFAULT 1,
    estimated       INTEGER NOT NULL DEFAULT 0,
    err_kind        TEXT    NOT NULL DEFAULT '',
    latency_ms      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_stat_day_kind       ON stat_event(day_local, kind);
CREATE INDEX IF NOT EXISTS idx_stat_day_kind_model ON stat_event(day_local, kind, model);
CREATE INDEX IF NOT EXISTS idx_stat_day_kind_src   ON stat_event(day_local, kind, source);
CREATE INDEX IF NOT EXISTS idx_stat_day_kind_api   ON stat_event(day_local, kind, api);
CREATE INDEX IF NOT EXISTS idx_stat_day_session    ON stat_event(day_local, session_id);
CREATE INDEX IF NOT EXISTS idx_stat_ts             ON stat_event(ts);
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
