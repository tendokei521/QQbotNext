"""长期记忆存储层（v3）：SQLite 单文件，按 bot_id 隔离。

v3 相对 v2 的新增（契约：``docs/plan/memory-v3-rewrite.md`` §4 / §5.2 / §10）：

- **时间有效性**（Zep 式）：``valid_from`` / ``invalid_at``——新说法让旧说法失效而不是覆盖，
  被取代的行保留可审计；``invalid_at`` 只在状态置 ``superseded`` 时写；
- **原始语料缓冲** ``episodes``：聊天原文先去重落库，后台 LLM 巩固后再标记；
- **embedding**：``embedding``（float32 BLOB，编解码走 ``vector.py``）+
  ``embedding_model`` / ``embedding_dim``，供混合召回；
- **kind 分层**：``fact | profile | preference``，精确判重与近义合并都按 kind 隔离；
- 审计行增加 ``ref_id``（指向被操作的记忆 id），写路径全部留痕；
- 旧库（v1/v2）在 ``__init__`` 里做**幂等增量迁移**：``ALTER TABLE ADD COLUMN`` + 建新表，
  用 ``PRAGMA table_info`` 探测缺列，不删数据；迁移失败整体回滚并留痕。

线程模型：单连接（``check_same_thread=False``）+ ``threading.RLock``；``store`` 本身同步，
事件循环内由 manager 用 ``asyncio.to_thread`` 包装。每条写语句立即 commit。

写入语义（``upsert_fact``，顺序固定）：
1) 同 owner + kind 精确同内容（任意 status）→ update：confidence=MAX、evidence+1、
   ``updated_at=now``，**status 保持原值**（negative / superseded 不复活）；
2) ``supersede_conflicts=True`` 且命中否定极性 → 旧 active 行置 superseded（写 ``invalid_at``
   + 审计 ``supersede``），本行再插入（``action="superseded"``）；
3) 词面相似 ≥ 0.92 且**极性相同**（``has_negation_conflict`` 为假）→ merge：覆盖 content、
   保留 id、confidence=MAX、evidence+1；
4) 否则 insert（``action="add"``）。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from app.agent import llm_data_dir, logger, safe_bot_id

from .vector import pack as pack_vector

# owner 段白名单（群号 / QQ 号 / 通用 key），防路径穿越与非法字符
_SAFE_PART_RE = re.compile(r"^[0-9a-zA-Z_\-]+$")

# 内容上限（字符）：写入时截断；episode 原文上限另计
_CONTENT_MAX = 500
_EPISODE_MAX = 2000

# 近义合并阈值：≥ 该相似度且极性相同才允许合并（v2 的 0.85 会误并否定句，v3 收紧到 0.92）
_SIM_MERGE = 0.92
# 近义 / 冲突扫描的行数上限（避免超大 owner 全表在 Python 侧比较）
_SIM_SCAN_LIMIT = 500
# delete_by_query 参与匹配的最小 token 长度：单字命中会误删大量记忆
_DELETE_TOKEN_MIN = 2

# 仅 active 状态可被召回；negative=用户否认（可恢复）；superseded=被新说法取代
_STATUS_ACTIVE = "active"
_STATUS_NEGATIVE = "negative"
_STATUS_SUPERSEDED = "superseded"

# 否定极性对：命中「肯定分句 + 否定分句」即视为说法冲突（如「喜欢美式」vs「不喜欢美式」）
_NEGATION_PAIRS: tuple[tuple[str, str], ...] = (
    ("喜欢", "不喜欢"), ("喜欢", "讨厌"), ("喜欢", "讨厌喝"), ("喜欢", "不喝"),
    ("喜欢", "喝不惯"), ("喜欢", "喝不了"), ("喜欢", "不习惯"), ("喜欢", "不爱"),
    ("喜欢", "戒"), ("爱", "不爱"), ("爱", "不喜欢"),
    ("住在", "搬"), ("住在", "离开"), ("住", "不住"), ("是", "不是"), ("有", "没有"),
    ("要吃", "不吃"), ("接受", "拒绝"), ("同意", "不同意"), ("要", "不要"),
)

# 建表：新库一次到位；旧库靠 _migrate 补列（CREATE TABLE IF NOT EXISTS 不会改已有表）
_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id             TEXT PRIMARY KEY,
    bot_id         TEXT NOT NULL,
    owner          TEXT NOT NULL,
    kind           TEXT NOT NULL DEFAULT 'fact',
    content        TEXT NOT NULL,
    keywords       TEXT NOT NULL DEFAULT '',
    importance     REAL NOT NULL DEFAULT 0.5,
    visibility     TEXT NOT NULL DEFAULT 'group',
    source         TEXT NOT NULL DEFAULT 'tool',
    source_user    TEXT NOT NULL DEFAULT '',
    source_task    TEXT NOT NULL DEFAULT '',
    embedding      BLOB,
    status         TEXT NOT NULL DEFAULT 'active',
    confidence     REAL NOT NULL DEFAULT 0.5,
    confirmed      INTEGER NOT NULL DEFAULT 0,
    expires_at     INTEGER,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL,
    hit_count      INTEGER NOT NULL DEFAULT 0,
    valid_from     INTEGER NOT NULL DEFAULT 0,
    invalid_at     INTEGER,
    last_used_at   INTEGER NOT NULL DEFAULT 0,
    embedding_model TEXT NOT NULL DEFAULT '',
    embedding_dim  INTEGER NOT NULL DEFAULT 0,
    meta           TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS episodes (
    id              TEXT PRIMARY KEY,
    bot_id          TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    owner           TEXT NOT NULL,
    user_id         TEXT NOT NULL DEFAULT '',
    role            TEXT NOT NULL DEFAULT 'user',
    content         TEXT NOT NULL,
    message_id      TEXT NOT NULL DEFAULT '',
    created_at      INTEGER NOT NULL,
    consolidated_at INTEGER
);

CREATE TABLE IF NOT EXISTS memory_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id      TEXT NOT NULL,
    ts          INTEGER NOT NULL,
    owner       TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,
    ref_id      TEXT NOT NULL DEFAULT '',
    user_id     TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    source_task TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS memory_owners (
    bot_id        TEXT NOT NULL,
    owner         TEXT NOT NULL,
    last_reset_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bot_id, owner)
);
"""

# memories 表在 v1/v2/v3 各版本可能缺少的列：迁移时按需 ADD COLUMN（幂等）
_MEMORY_COLUMNS: dict[str, str] = {
    "kind": "TEXT NOT NULL DEFAULT 'fact'",
    "keywords": "TEXT NOT NULL DEFAULT ''",
    "importance": "REAL NOT NULL DEFAULT 0.5",
    "visibility": "TEXT NOT NULL DEFAULT 'group'",
    "source": "TEXT NOT NULL DEFAULT 'tool'",
    "source_user": "TEXT NOT NULL DEFAULT ''",
    "source_task": "TEXT NOT NULL DEFAULT ''",
    "embedding": "BLOB",
    "status": "TEXT NOT NULL DEFAULT 'active'",
    "confidence": "REAL NOT NULL DEFAULT 0.5",
    "confirmed": "INTEGER NOT NULL DEFAULT 0",
    "expires_at": "INTEGER",
    "evidence_count": "INTEGER NOT NULL DEFAULT 0",
    "created_at": "INTEGER NOT NULL DEFAULT 0",
    "updated_at": "INTEGER NOT NULL DEFAULT 0",
    "hit_count": "INTEGER NOT NULL DEFAULT 0",
    "valid_from": "INTEGER NOT NULL DEFAULT 0",
    "invalid_at": "INTEGER",
    "last_used_at": "INTEGER NOT NULL DEFAULT 0",
    "embedding_model": "TEXT NOT NULL DEFAULT ''",
    "embedding_dim": "INTEGER NOT NULL DEFAULT 0",
    "meta": "TEXT NOT NULL DEFAULT '{}'",
}

# memory_events 在 v3 新增的列（旧库审计表缺 ref_id）
_EVENT_COLUMNS: dict[str, str] = {
    "ref_id": "TEXT NOT NULL DEFAULT ''",
}

# 索引统一在补列之后创建（旧库缺列时 CREATE INDEX 会失败）
_INDEX_STATEMENTS: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_mem_owner ON memories(bot_id, owner, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_mem_status ON memories(bot_id, owner, status)",
    "CREATE INDEX IF NOT EXISTS idx_mem_profile ON memories(bot_id, owner, kind)",
    "CREATE INDEX IF NOT EXISTS idx_events_owner ON memory_events(bot_id, owner, ts)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_owner_reset ON memory_owners(bot_id, owner)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_ep_dedupe ON episodes(bot_id, session_id, message_id, role)"
    " WHERE message_id != ''",
    "CREATE INDEX IF NOT EXISTS idx_ep_session ON episodes(bot_id, session_id, consolidated_at)",
)


def _safe_part(value: Any) -> str:
    """把 owner 段（qq/群号/key）规整为安全片段。"""
    s = str(value or "").strip()
    if not _SAFE_PART_RE.match(s):
        s = re.sub(r"[^0-9a-zA-Z_\-]", "_", s) or "unknown"
    return s


def owner_private(user_id: Any) -> str:
    """私聊用户 owner：user_<uid>。"""
    return f"user_{_safe_part(user_id)}"


def owner_group(group_id: Any) -> str:
    """群公共事实 owner：group_<gid>。"""
    return f"group_{_safe_part(group_id)}"


def owner_group_member(group_id: Any, user_id: Any) -> str:
    """群内某成员画像 owner：user_<uid>@group_<gid>。"""
    return f"user_{_safe_part(user_id)}@group_{_safe_part(group_id)}"


def owner_global() -> str:
    """全局 owner（不使用用户内容）。"""
    return "global"


def session_owner(session_id: str, user_id: Any = None) -> str:
    """按 session_id 推导 owner（会话级记忆落点）。"""
    session_id = str(session_id or "")
    if session_id.startswith("private_"):
        return owner_private(session_id[len("private_"):])
    if session_id.startswith("group_"):
        gid = session_id[len("group_"):]
        if user_id is not None:
            return owner_group_member(gid, user_id)
        return owner_group(gid)
    return owner_global()


def summarize(text: Any, limit: int = 200) -> str:
    """审计摘要：压成单行并截断到 limit 字符。"""
    s = str(text or "").replace("\n", " ").strip()
    if len(s) > limit:
        return s[:limit] + "…"
    return s


def text_similarity(a: str, b: str) -> float:
    """两个文本的相似度（0~1），用于近义合并判定。"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, str(a), str(b)).ratio()


def has_negation_conflict(a: str, b: str) -> bool:
    """判断两条说法是否构成否定冲突（肯定分句在一边、否定分句在另一边）。"""
    if not a or not b or a == b:
        return False
    aa = str(a)
    bb = str(b)
    for x, y in _NEGATION_PAIRS:
        if (x in aa and y in bb) or (y in aa and x in bb):
            return True
    return False


def _tokenize(text: str) -> list[str]:
    """查询 token：按空白/符号切词 + 中文整段（不做分词），最多 8 个防超长 query。"""
    text = str(text or "")
    parts = re.split(r"[\s，。！？、;；:：,.!?/\\|]+", text)
    out = [p for p in parts if len(p) >= 1]
    return out[:8]


def _clamp(value: Any, low: float = 0.0, high: float = 1.0) -> float:
    """把数值钳到 [low, high]；非法值按 0.5 处理并 debug 留痕。"""
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError) as e:
        logger.debug(f"[Memory] 非法数值 {value!r}（按 0.5 处理）: {e}")
        return 0.5


def _meta_json(meta: dict[str, Any] | str | None) -> str:
    """把 meta（dict → JSON 字符串）规整为落库文本；序列化失败存空对象并留痕。"""
    if meta is None or meta == "":
        return "{}"
    if isinstance(meta, str):
        return meta[:4000] or "{}"
    try:
        return json.dumps(meta, ensure_ascii=False)[:4000]
    except (TypeError, ValueError) as e:
        logger.debug(f"[Memory] meta 序列化失败（存空对象）: {e}")
        return "{}"


@dataclass
class WriteOutcome:
    """一次 ``upsert_fact`` 的结果。

    - ``id``：目标记忆 id；
    - ``action``：``add`` | ``update`` | ``merge`` | ``superseded``；
    - ``superseded``：本次被置为 superseded 的旧记忆 id 列表。
    """

    id: str
    action: str
    superseded: list[str] = field(default_factory=list)


class MemoryStore:
    """SQLite 记忆库（每个 bot 一个实例；owner 路由见模块级 helper）。"""

    db_path: str
    bot_id: str

    def __init__(self, bot_id: Any, db_path: str | None = None) -> None:
        self.bot_id = safe_bot_id(bot_id)
        if db_path is None:
            db_path = os.path.join(llm_data_dir(), self.bot_id, "memory", "memory.db")
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            parent = os.path.dirname(self.db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)

        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            try:
                self._conn.executescript(_SCHEMA)
            except sqlite3.Error as e:
                # 建表失败时仍尝试迁移/使用已有结构（可能是只读盘/损坏库）
                logger.warning(f"[Memory] 建表失败（继续尝试使用现有结构）: {e}")
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error as e:
                # 某些文件系统（如网络盘）不支持 WAL：退回默认 journal 模式仍可正常工作
                logger.debug(f"[Memory] 启用 WAL 失败，退回默认日志模式: {e}")
            self._conn.commit()
            self._migrate()

    # ── 生命周期 ─────────────────────────────────────────
    def close(self) -> None:
        """关闭 SQLite 连接（幂等，失败只 debug）。"""
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error as e:
                logger.debug(f"[Memory] 关闭 SQLite 连接失败（已忽略）: {e}")

    def _execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        """执行写语句并立即 commit。"""
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def _fetch(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        """执行查询并返回 dict 行列表。"""
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]

    def _fetch_one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        """查询单行；无结果返回 None。"""
        rows = self._fetch(sql, params)
        return rows[0] if rows else None

    # ── 迁移 ─────────────────────────────────────────────
    def _migrate(self) -> None:
        """幂等增量迁移（v1/v2 → v3）。

        步骤：``PRAGMA table_info`` 探测缺列 → ``ALTER TABLE ADD COLUMN`` →
        建 episodes 表索引 → 回填旧行 ``valid_from=created_at``。
        整个过程在一个事务里，任一步失败则**整体回滚**并 warning 留痕，
        库仍保持迁移前可用的结构（不删数据、不改已有行）。
        """
        with self._lock:
            try:
                if self._conn.in_transaction:
                    # 理论上不该发生（executescript / commit 后无事务）；保险起见先收尾
                    self._conn.commit()
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    self._add_missing_columns("memories", _MEMORY_COLUMNS)
                    self._add_missing_columns("memory_events", _EVENT_COLUMNS)
                    for sql in _INDEX_STATEMENTS:
                        self._conn.execute(sql)
                    # 旧数据语义：旧实现没有 valid_from，按 created_at 回填（幂等）
                    self._conn.execute(
                        "UPDATE memories SET valid_from=created_at WHERE valid_from=0 AND created_at>0"
                    )
                    self._conn.commit()
                except sqlite3.Error as e:
                    logger.warning(f"[Memory] 旧库迁移失败（本次已回滚，继续使用现有结构）: {e}")
                    try:
                        self._conn.rollback()
                    except sqlite3.Error as re:
                        logger.debug(f"[Memory] 迁移回滚失败（已忽略）: {re}")
            except sqlite3.Error as e:
                # BEGIN 失败（例如连接已处于事务中）：不做迁移，库仍可用
                logger.warning(f"[Memory] 迁移事务启动失败（跳过迁移）: {e}")

    def _add_missing_columns(self, table: str, additions: dict[str, str]) -> None:
        """按 ``PRAGMA table_info`` 探测并 ``ALTER TABLE ADD COLUMN`` 补齐缺列（幂等）。"""
        cols = self._table_columns(table)
        if not cols:
            # 表不存在（建表失败）→ 无法补列；后续建索引会失败并由 _migrate 回滚留痕
            return
        for name, ddl in additions.items():
            if name not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    def _table_columns(self, table: str) -> set[str]:
        """读取表现有列名集合；表不存在/读取失败返回空集（并 debug 留痕）。"""
        try:
            rows = self._fetch(f"PRAGMA table_info({table})")
        except sqlite3.Error as e:
            logger.debug(f"[Memory] 读取表结构失败 {table}: {e}")
            return set()
        return {str(r["name"]) for r in rows}

    # ── 写入 ─────────────────────────────────────────────
    def upsert_fact(
        self,
        content: str,
        owner: str,
        *,
        importance: float = 0.5,
        keywords: str = "",
        source: str = "tool",
        source_user: str = "",
        source_task: str = "",
        confidence: float = 0.5,
        kind: str = "fact",
        valid_from: int | None = None,
        expires_at: int | None = None,
        meta: dict[str, Any] | str | None = None,
        embedding: list[float] | None = None,
        embedding_model: str = "",
        supersede_conflicts: bool = False,
    ) -> WriteOutcome:
        """写入 / 更新 / 合并 / 取代一条记忆，返回 ``WriteOutcome``。

        四步顺序固定（契约 §5.2）：精确同内容 → 否定冲突下架 → 同极性近义合并 → 新增。
        negative / superseded 同措辞再次出现只累计证据、**不复活**（保持原 status）。
        """
        text = str(content or "").strip()
        if not text:
            raise ValueError("记忆内容不能为空")
        text = text[: _CONTENT_MAX]
        owner = str(owner or "")
        kind = str(kind or "fact").strip() or "fact"
        keywords = str(keywords or "").strip()[:300]
        source = str(source or "tool")
        source_user = str(source_user or "")
        source_task = str(source_task or "")
        now = int(time.time())
        confidence = _clamp(confidence)
        importance = _clamp(importance)
        valid_from = int(valid_from) if valid_from is not None else now
        expires_at = int(expires_at) if expires_at is not None else None
        meta_json = _meta_json(meta)
        blob: bytes | None = None
        emb_dim = 0
        if embedding:
            blob = pack_vector(embedding)
            emb_dim = len(embedding)

        with self._lock:
            # 1) 精确同内容（同 owner + kind，任意 status）→ update，status 保持原值
            existing = self._fetch_one(
                "SELECT * FROM memories WHERE bot_id=? AND owner=? AND kind=? AND content=?"
                " ORDER BY updated_at DESC LIMIT 1",
                (self.bot_id, owner, kind, text),
            )
            if existing:
                self._update_existing(
                    existing,
                    importance=importance,
                    keywords=keywords,
                    source=source,
                    source_user=source_user,
                    source_task=source_task,
                    confidence=confidence,
                    expires_at=expires_at,
                    blob=blob,
                    emb_dim=emb_dim,
                    embedding_model=str(embedding_model or ""),
                    meta_json=meta_json,
                    now=now,
                )
                return WriteOutcome(str(existing["id"]), "update", [])

            # 2) 否定极性冲突 → 旧 active 行下架（supersede + invalid_at + 审计），本行再插入
            superseded: list[str] = []
            if supersede_conflicts:
                superseded = self._supersede_conflicts(owner, text, source_user=source_user)
                if superseded:
                    mid = self._insert_row(
                        text, owner, kind,
                        importance=importance, keywords=keywords, source=source,
                        source_user=source_user, source_task=source_task,
                        confidence=confidence, valid_from=valid_from, expires_at=expires_at,
                        blob=blob, emb_dim=emb_dim,
                        embedding_model=str(embedding_model or ""),
                        meta_json=meta_json, now=now,
                    )
                    return WriteOutcome(mid, "superseded", superseded)

            # 3) 词面相似 ≥ 0.92 且极性相同 → merge（保留 id、换最新措辞）
            dup = self._find_near_dupe(owner, kind, text)
            if dup:
                self._merge_into(
                    dup,
                    text,
                    importance=importance,
                    keywords=keywords,
                    source=source,
                    source_user=source_user,
                    source_task=source_task,
                    confidence=confidence,
                    expires_at=expires_at,
                    blob=blob,
                    emb_dim=emb_dim,
                    embedding_model=str(embedding_model or ""),
                    meta_json=meta_json,
                    now=now,
                )
                return WriteOutcome(str(dup["id"]), "merge", [])

            # 4) 新写入
            mid = self._insert_row(
                text, owner, kind,
                importance=importance, keywords=keywords, source=source,
                source_user=source_user, source_task=source_task,
                confidence=confidence, valid_from=valid_from, expires_at=expires_at,
                blob=blob, emb_dim=emb_dim,
                embedding_model=str(embedding_model or ""),
                meta_json=meta_json, now=now,
            )
            return WriteOutcome(mid, "add", [])

    def _insert_row(
        self,
        text: str,
        owner: str,
        kind: str,
        *,
        importance: float,
        keywords: str,
        source: str,
        source_user: str,
        source_task: str,
        confidence: float,
        valid_from: int,
        expires_at: int | None,
        blob: bytes | None,
        emb_dim: int,
        embedding_model: str,
        meta_json: str,
        now: int,
    ) -> str:
        """INSERT 一条新的 active 记忆，返回新 id。"""
        mid = uuid.uuid4().hex
        row: dict[str, Any] = {
            "id": mid,
            "bot_id": self.bot_id,
            "owner": owner,
            "kind": kind,
            "content": text,
            "keywords": keywords,
            "importance": importance,
            "visibility": "group",
            "source": source,
            "source_user": source_user,
            "source_task": source_task,
            "embedding": blob,
            "status": _STATUS_ACTIVE,
            "confidence": confidence,
            "confirmed": 0,
            "expires_at": expires_at,
            "evidence_count": 1,
            "created_at": now,
            "updated_at": now,
            "hit_count": 0,
            "valid_from": valid_from,
            "invalid_at": None,
            "last_used_at": 0,
            "embedding_model": embedding_model,
            "embedding_dim": emb_dim,
            "meta": meta_json,
        }
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self._execute(
            f"INSERT INTO memories ({cols}) VALUES ({marks})",
            tuple(row.values()),
        )
        return mid

    def _update_existing(
        self,
        row: dict,
        *,
        importance: float,
        keywords: str,
        source: str,
        source_user: str,
        source_task: str,
        confidence: float,
        expires_at: int | None,
        blob: bytes | None,
        emb_dim: int,
        embedding_model: str,
        meta_json: str,
        now: int,
    ) -> None:
        """精确同内容再次出现：累计证据、抬高置信度，**不碰 status**（不复活负数/被取代行）。"""
        sets = [
            "confidence=MAX(confidence,?)",
            "importance=MAX(importance,?)",
            "evidence_count=MIN(evidence_count+1,99)",
            "updated_at=?",
            "source=?",
            "source_user=?",
            "source_task=?",
        ]
        params: list[Any] = [
            confidence, importance, now, source, source_user, source_task,
        ]
        if keywords:
            sets.append("keywords=?")
            params.append(keywords)
        if expires_at is not None:
            sets.append("expires_at=?")
            params.append(expires_at)
        if meta_json != "{}":
            sets.append("meta=?")
            params.append(meta_json)
        if blob is not None:
            sets.append("embedding=?")
            params.append(blob)
            sets.append("embedding_dim=?")
            params.append(emb_dim)
            if embedding_model:
                sets.append("embedding_model=?")
                params.append(embedding_model)
        params.extend([self.bot_id, row["id"]])
        self._execute(
            "UPDATE memories SET " + ", ".join(sets) + " WHERE bot_id=? AND id=?",
            params,
        )

    def _merge_into(
        self,
        dup: dict,
        text: str,
        *,
        importance: float,
        keywords: str,
        source: str,
        source_user: str,
        source_task: str,
        confidence: float,
        expires_at: int | None,
        blob: bytes | None,
        emb_dim: int,
        embedding_model: str,
        meta_json: str,
        now: int,
    ) -> None:
        """同极性近义合并：用新措辞覆盖 content（保留 id / valid_from）、累计证据。

        content 变了旧 embedding 即失效：未提供新向量时清空 ``embedding`` 相关列，
        让后台回填（``missing_embeddings``）重新生成，避免召回用旧措辞的向量算分。
        """
        sets = [
            "content=?",
            "confidence=MAX(confidence,?)",
            "importance=MAX(importance,?)",
            "evidence_count=MIN(evidence_count+1,99)",
            "updated_at=?",
            "source=?",
            "source_user=?",
            "source_task=?",
        ]
        params: list[Any] = [
            text,
            confidence,
            importance,
            now,
            source,
            source_user,
            source_task,
        ]
        if keywords:
            sets.append("keywords=?")
            params.append(keywords)
        if expires_at is not None:
            sets.append("expires_at=?")
            params.append(expires_at)
        if meta_json != "{}":
            sets.append("meta=?")
            params.append(meta_json)
        if blob is not None:
            sets.append("embedding=?")
            params.append(blob)
            sets.append("embedding_dim=?")
            params.append(emb_dim)
            if embedding_model:
                sets.append("embedding_model=?")
                params.append(embedding_model)
        else:
            sets.append("embedding=NULL")
            sets.append("embedding_dim=0")
            sets.append("embedding_model=''")
        params.extend([self.bot_id, dup["id"]])
        self._execute(
            "UPDATE memories SET " + ", ".join(sets) + " WHERE bot_id=? AND id=?",
            params,
        )

    def _find_near_dupe(self, owner: str, kind: str, text: str) -> dict | None:
        """同 owner + kind 的 active 行里找相似度最高且无否定冲突的一条（≥ 0.92）。"""
        rows = self._fetch(
            "SELECT * FROM memories WHERE bot_id=? AND owner=? AND kind=? AND status='active'"
            " AND content<>? ORDER BY updated_at DESC LIMIT ?",
            (self.bot_id, owner, kind, text, _SIM_SCAN_LIMIT),
        )
        best: dict | None = None
        best_score = 0.0
        for row in rows:
            old = str(row.get("content") or "")
            if has_negation_conflict(old, text):
                continue
            score = text_similarity(old, text)
            if score < _SIM_MERGE:
                continue
            if best is None or score > best_score:
                best = row
                best_score = score
        return best

    def _supersede_conflicts(self, owner: str, text: str, *, source_user: str = "") -> list[str]:
        """把同 owner 内与该说法否定冲突的 active 记忆置 superseded，返回其 id 列表。"""
        now = int(time.time())
        ids: list[str] = []
        for row in self.find_conflicts(owner, text):
            mid = str(row["id"])
            self._execute(
                "UPDATE memories SET status=?, invalid_at=?, updated_at=? WHERE bot_id=? AND id=?",
                (_STATUS_SUPERSEDED, now, now, self.bot_id, mid),
            )
            self.audit(
                "supersede",
                owner=owner,
                ref_id=mid,
                user_id=str(source_user or ""),
                summary=f"{str(row.get('content') or '')[:60]} -> {text[:60]}",
                source="conflict",
            )
            ids.append(mid)
        return ids

    def find_conflicts(self, owner: str, content: str) -> list[dict]:
        """返回同 owner 中与给定说法存在否定冲突的 active 记忆。"""
        rows = self._fetch(
            "SELECT * FROM memories WHERE bot_id=? AND owner=? AND status='active'"
            " ORDER BY updated_at DESC LIMIT ?",
            (self.bot_id, owner, _SIM_SCAN_LIMIT),
        )
        return [
            row for row in rows
            if str(row.get("content") or "") != content
            and has_negation_conflict(str(row.get("content") or ""), content)
        ]

    # ── 读取 ─────────────────────────────────────────────
    def get(self, mid: str) -> dict | None:
        """按 id 取一条（bot 内，不限 owner）。"""
        return self._fetch_one(
            "SELECT * FROM memories WHERE bot_id=? AND id=?",
            (self.bot_id, mid),
        )

    def get_owned(self, mid: str, owner: str) -> dict | None:
        """按 id + owner 取一条（owner 隔离校验）。"""
        return self._fetch_one(
            "SELECT * FROM memories WHERE bot_id=? AND id=? AND owner=?",
            (self.bot_id, mid, owner),
        )

    @staticmethod
    def _status_clause(include_all: bool) -> str:
        """列表过滤：默认仅 active。"""
        return "" if include_all else " AND status='active'"

    def list_by_owner(
        self,
        owner: str,
        limit: int = 200,
        include_all: bool = False,
        kind: str | None = None,
    ) -> list[dict]:
        """某 owner 的记忆列表（默认仅 active；可按 kind 过滤）。"""
        sql = "SELECT * FROM memories WHERE bot_id=? AND owner=?" + self._status_clause(include_all)
        params: list[Any] = [self.bot_id, owner]
        if kind:
            sql += " AND kind=?"
            params.append(str(kind))
        sql += " ORDER BY updated_at DESC, importance DESC LIMIT ?"
        params.append(int(limit))
        return self._fetch(sql, params)

    def list_for_owners(
        self,
        owners: list[str],
        limit: int = 500,
        include_all: bool = False,
    ) -> list[dict]:
        """跨 owner 批量拉取（召回候选集；默认仅 active）。"""
        owners = [str(o) for o in owners if o]
        if not owners:
            return []
        marks = ",".join("?" for _ in owners)
        return self._fetch(
            "SELECT * FROM memories WHERE bot_id=? AND owner IN (" + marks + ")"
            + self._status_clause(include_all)
            + " ORDER BY updated_at DESC LIMIT ?",
            (self.bot_id, *owners, int(limit)),
        )

    def list_profiles(self, owner: str, limit: int = 10) -> list[dict]:
        """用户画像：该 owner 下 kind='profile' 且 active 的记忆。"""
        return self._fetch(
            "SELECT * FROM memories WHERE bot_id=? AND owner=? AND kind='profile'"
            " AND status='active' ORDER BY importance DESC, updated_at DESC LIMIT ?",
            (self.bot_id, owner, int(limit)),
        )

    def find_by_query(self, owner: str, query: str, limit: int = 20) -> list[dict]:
        """按 id 或内容/关键词 token 命中查找（active 优先），供 deny/confirm/correct 使用。"""
        target = str(query or "").strip()
        if not target:
            return []
        direct = self.get_owned(target, owner)
        if direct:
            return [direct]
        rows = self.list_by_owner(owner, limit=1000, include_all=True)
        tokens = _tokenize(target)
        if not tokens:
            return []
        matched = [
            r for r in rows
            if any(t in ((r.get("content") or "") + " " + (r.get("keywords") or "")) for t in tokens)
        ]
        matched.sort(key=lambda r: 0 if r.get("status") == _STATUS_ACTIVE else 1)
        return matched[: max(1, int(limit))]

    def search_in_owners(
        self,
        owners: list[str],
        query: str,
        limit: int = 100,
        include_all: bool = False,
    ) -> list[dict]:
        """跨 owner 关键词过滤（兼容旧调用方；召回评分由 recall 层负责）。"""
        owners = [str(o) for o in owners if o]
        if not owners:
            return []
        marks = ",".join("?" for _ in owners)
        rows = self._fetch(
            "SELECT * FROM memories WHERE bot_id=? AND owner IN (" + marks + ")"
            + self._status_clause(include_all)
            + " ORDER BY updated_at DESC LIMIT ?",
            (self.bot_id, *owners, max(int(limit), 200)),
        )
        tokens = _tokenize(query)
        if not tokens:
            return rows
        matched = [
            row for row in rows
            if any(t in ((row.get("content") or "") + " " + (row.get("keywords") or "")) for t in tokens)
        ]
        return matched[: int(limit)]

    def count_by_owner(self, owner: str) -> int:
        """某 owner 记忆条数（全部状态）。"""
        row = self._fetch_one(
            "SELECT COUNT(*) AS n FROM memories WHERE bot_id=? AND owner=?",
            (self.bot_id, owner),
        )
        return int(row["n"]) if row else 0

    def count_active_by_owner(self, owner: str) -> int:
        """某 owner 的 active 记忆条数。"""
        row = self._fetch_one(
            "SELECT COUNT(*) AS n FROM memories WHERE bot_id=? AND owner=? AND status='active'",
            (self.bot_id, owner),
        )
        return int(row["n"]) if row else 0

    # ── 状态 / 更正 ──────────────────────────────────────
    def _set_status_raw(self, mid: str, status: str, owner: str | None = None) -> bool:
        """状态迁移底层实现（不写审计）；``status=superseded`` 时同步写 ``invalid_at``。"""
        now = int(time.time())
        sets = ["status=?", "updated_at=?"]
        params: list[Any] = [status, now]
        if status == _STATUS_SUPERSEDED:
            sets.append("invalid_at=?")
            params.append(now)
        sql = "UPDATE memories SET " + ", ".join(sets)
        if owner:
            sql += " WHERE bot_id=? AND id=? AND owner=?"
            params.extend([self.bot_id, mid, owner])
        else:
            sql += " WHERE bot_id=? AND id=?"
            params.extend([self.bot_id, mid])
        cur = self._execute(sql, params)
        return bool(cur.rowcount)

    def set_status(self, mid: str, status: str, owner: str | None = None, reason: str = "") -> bool:
        """显式状态迁移（negative/superseded/active），成功时写审计 ``set_status``。"""
        status = str(status or "").strip()
        if not status:
            return False
        if not self._set_status_raw(mid, status, owner=owner):
            return False
        self.audit(
            "set_status",
            owner=str(owner or ""),
            ref_id=str(mid),
            summary=f"{status} {str(reason or '').strip()}".strip(),
            source="manual",
        )
        return True

    def deny(self, owner: str, target: str, reason: str = "user_deny") -> int:
        """用户否认 → 命中的 active 记忆置 negative（下架不删、可恢复），返回条数。"""
        count = 0
        for row in self.find_by_query(owner, target):
            if row.get("status") != _STATUS_ACTIVE:
                continue
            mid = str(row["id"])
            if not self._set_status_raw(mid, _STATUS_NEGATIVE, owner=owner):
                continue
            count += 1
            self.audit(
                "deny",
                owner=owner,
                ref_id=mid,
                user_id=str(row.get("source_user") or ""),
                summary=f"{str(row.get('content') or '')[:60]} ({reason})",
                source="manual",
            )
        return count

    def confirm(self, owner: str, target: str) -> int:
        """用户确认 → 命中的 active 记忆 confidence+0.2（上限 1）且 confirmed=1。"""
        count = 0
        now = int(time.time())
        for row in self.find_by_query(owner, target):
            if row.get("status") != _STATUS_ACTIVE:
                continue
            mid = str(row["id"])
            cur = self._execute(
                "UPDATE memories SET confidence=MIN(confidence+0.2, 1.0), confirmed=1,"
                " updated_at=? WHERE bot_id=? AND id=?",
                (now, self.bot_id, mid),
            )
            if not cur.rowcount:
                continue
            count += 1
            self.audit(
                "confirm",
                owner=owner,
                ref_id=mid,
                user_id=str(row.get("source_user") or ""),
                summary=str(row.get("content") or ""),
                source="manual",
            )
        return count

    def supersede(self, owner: str, target: str, reason: str = "supersede") -> int:
        """把命中的 active 记忆置 superseded（写 invalid_at + 审计），返回条数。"""
        count = 0
        for row in self.find_by_query(owner, target):
            if row.get("status") != _STATUS_ACTIVE:
                continue
            mid = str(row["id"])
            if not self._set_status_raw(mid, _STATUS_SUPERSEDED, owner=owner):
                continue
            count += 1
            self.audit(
                "supersede",
                owner=owner,
                ref_id=mid,
                user_id=str(row.get("source_user") or ""),
                summary=f"{str(row.get('content') or '')[:60]} ({reason})",
                source="manual",
            )
        return count

    def correct(
        self,
        owner: str,
        old_target: str,
        new_content: str,
        *,
        confidence: float = 0.85,
        source_user: str = "",
    ) -> str | None:
        """纠错：旧说置 superseded，写入新说（source=correct），返回新 id；空内容 → None。"""
        text = str(new_content or "").strip()
        if not text:
            return None
        superseded = self.supersede(owner, old_target, reason="correct")
        outcome = self.upsert_fact(
            text,
            owner,
            importance=0.8,
            source="correct",
            source_user=str(source_user or ""),
            confidence=_clamp(confidence),
            supersede_conflicts=True,
        )
        self.audit(
            "correct",
            owner=owner,
            ref_id=outcome.id,
            user_id=str(source_user or ""),
            summary=f"{superseded} 条旧记忆 -> {text}",
            source="manual",
        )
        return outcome.id

    # ── 删除 ─────────────────────────────────────────────
    def delete_fact(self, mid: str, owner: str | None = None) -> bool:
        """物理删除一条记忆（可限定 owner 防越权）。"""
        if owner:
            cur = self._execute(
                "DELETE FROM memories WHERE bot_id=? AND id=? AND owner=?",
                (self.bot_id, mid, owner),
            )
        else:
            cur = self._execute(
                "DELETE FROM memories WHERE bot_id=? AND id=?",
                (self.bot_id, mid),
            )
        return bool(cur.rowcount)

    def delete_by_query(self, owner: str, query: str) -> int:
        """物理删除该 owner 下内容/关键词命中**任一** token 的记忆，返回条数。

        token 至少 2 个字符才参与匹配（避免「猫」「他」这类单字把大量记忆误删）。
        """
        tokens = [t for t in _tokenize(query) if len(t) >= _DELETE_TOKEN_MIN]
        if not tokens:
            return 0
        rows = self._fetch(
            "SELECT id, content, keywords FROM memories WHERE bot_id=? AND owner=?",
            (self.bot_id, owner),
        )
        targets = [
            str(row["id"]) for row in rows
            if any(
                t in ((row.get("content") or "") + " " + (row.get("keywords") or ""))
                for t in tokens
            )
        ]
        for mid in targets:
            self.delete_fact(mid, owner=owner)
        return len(targets)

    def clear(self, owner: str) -> int:
        """清空该 owner 的全部记忆（含 negative/superseded），返回删除条数。"""
        cur = self._execute(
            "DELETE FROM memories WHERE bot_id=? AND owner=?",
            (self.bot_id, owner),
        )
        return max(0, int(cur.rowcount or 0))

    def enforce_limit(self, owner: str, max_per_owner: int) -> int:
        """超过上限时淘汰 active 记忆：score=importance/(1+age_days/7)，升序删除。

        只淘汰 active（negative/superseded 保留可审计）；age 以 ``updated_at`` 计算。
        """
        max_per_owner = max(1, int(max_per_owner))
        rows = self.list_by_owner(owner, limit=10_000)
        if len(rows) <= max_per_owner:
            return 0
        now = int(time.time())
        scored: list[tuple[float, str]] = []
        for row in rows:
            age_days = max(0.0, (now - int(row.get("updated_at") or now)) / 86400.0)
            score = float(row.get("importance") or 0.0) / (1.0 + age_days / 7.0)
            scored.append((score, str(row["id"])))
        scored.sort(key=lambda item: item[0])  # 分低（不重要 + 旧）先删
        overflow = len(scored) - max_per_owner
        removed = 0
        for _score, mid in scored[:overflow]:
            if self.delete_fact(mid, owner=owner):
                removed += 1
        return removed

    # ── owner 重置线 ─────────────────────────────────────
    def set_reset(self, owner: str) -> None:
        """写 owner 级重置线（会话重置后，重置线之前的旧记忆默认挂起）。"""
        self._execute(
            "INSERT INTO memory_owners (bot_id, owner, last_reset_at) VALUES (?,?,?)"
            " ON CONFLICT(bot_id, owner) DO UPDATE SET last_reset_at=excluded.last_reset_at",
            (self.bot_id, str(owner or ""), int(time.time())),
        )

    def get_reset(self, owner: str) -> int:
        """读 owner 级重置线（epoch 秒；无记录返回 0）。"""
        row = self._fetch_one(
            "SELECT last_reset_at FROM memory_owners WHERE bot_id=? AND owner=?",
            (self.bot_id, str(owner or "")),
        )
        return int(row["last_reset_at"]) if row else 0

    # ── episodes（原始语料缓冲） ─────────────────────────
    def add_episode(
        self,
        session_id: str,
        owner: str,
        content: str,
        *,
        role: str = "user",
        user_id: str = "",
        message_id: str = "",
        ts: int | None = None,
    ) -> str:
        """写入一条原始语料 episode，返回 id（内容空 → ``""``；重复上报幂等）。"""
        text = str(content or "").strip()
        if not text:
            return ""
        text = text[: _EPISODE_MAX]
        mid = uuid.uuid4().hex
        now = int(ts) if ts is not None else int(time.time())
        cur = self._execute(
            "INSERT OR IGNORE INTO episodes (id, bot_id, session_id, owner, user_id, role,"
            " content, message_id, created_at, consolidated_at) VALUES (?,?,?,?,?,?,?,?,?,NULL)",
            (
                mid,
                self.bot_id,
                str(session_id or ""),
                str(owner or ""),
                str(user_id or ""),
                str(role or "user"),
                text,
                str(message_id or ""),
                now,
            ),
        )
        if cur.rowcount:
            return mid
        # 命中唯一索引（同 bot+session+message_id+role 重复上报）→ 返回已有行 id
        if message_id:
            row = self._fetch_one(
                "SELECT id FROM episodes WHERE bot_id=? AND session_id=? AND message_id=? AND role=?",
                (self.bot_id, str(session_id or ""), str(message_id), str(role or "user")),
            )
            if row:
                return str(row["id"])
        return mid

    def list_episodes(
        self,
        session_id: str,
        *,
        limit: int = 40,
        unconsolidated_only: bool = True,
    ) -> list[dict]:
        """按会话取 episode（时间正序，便于拼 prompt）；默认只取未巩固的。"""
        sql = (
            "SELECT id, owner, user_id, role, content, message_id, created_at, consolidated_at"
            " FROM episodes WHERE bot_id=? AND session_id=?"
        )
        if unconsolidated_only:
            sql += " AND consolidated_at IS NULL"
        # rowid 保证同一秒内写入的多条 episode 仍按落库顺序排（created_at 只有秒级精度）
        sql += " ORDER BY created_at ASC, rowid ASC LIMIT ?"
        return self._fetch(sql, (self.bot_id, str(session_id or ""), int(limit)))

    def mark_episodes_consolidated(self, ids: list[str]) -> int:
        """把一批 episode 标记为已巩固（只标记尚未标记的），返回标记条数。"""
        clean = [str(i) for i in ids if i]
        if not clean:
            return 0
        marks = ",".join("?" for _ in clean)
        cur = self._execute(
            "UPDATE episodes SET consolidated_at=? WHERE bot_id=? AND consolidated_at IS NULL"
            " AND id IN (" + marks + ")",
            (int(time.time()), self.bot_id, *clean),
        )
        return max(0, int(cur.rowcount or 0))

    def prune_episodes(self, *, keep_days: int = 7, max_per_session: int = 200) -> int:
        """清理 episode：删除超过 keep_days 的已巩固行 + 每会话只留最新 max_per_session 条。

        超出上限时优先删「已巩固且更旧」的行，再删未巩固的旧行（硬上限，防库无限增长）。
        """
        keep_days = max(0, int(keep_days))
        max_per_session = max(1, int(max_per_session))
        now = int(time.time())
        removed = 0
        # keep_days 按字面语义：删除 created_at 早于 now-keep_days*86400 的**已巩固**行
        cur = self._execute(
            "DELETE FROM episodes WHERE bot_id=? AND consolidated_at IS NOT NULL AND created_at<?",
            (self.bot_id, now - keep_days * 86400),
        )
        removed += max(0, int(cur.rowcount or 0))
        groups = self._fetch(
            "SELECT session_id, COUNT(*) AS n FROM episodes WHERE bot_id=?"
            " GROUP BY session_id HAVING n > ?",
            (self.bot_id, max_per_session),
        )
        for group in groups:
            overflow = int(group["n"]) - max_per_session
            if overflow <= 0:
                continue
            stale = self._fetch(
                "SELECT id FROM episodes WHERE bot_id=? AND session_id=?"
                " ORDER BY (consolidated_at IS NULL) ASC, created_at ASC, rowid ASC LIMIT ?",
                (self.bot_id, group["session_id"], overflow),
            )
            for row in stale:
                cur = self._execute(
                    "DELETE FROM episodes WHERE bot_id=? AND id=?",
                    (self.bot_id, str(row["id"])),
                )
                removed += max(0, int(cur.rowcount or 0))
        return removed

    # ── embedding ────────────────────────────────────────
    def set_embedding(
        self,
        mid: str,
        vector: list[float],
        *,
        model: str = "",
        dim: int | None = None,
    ) -> None:
        """写入 embedding（float32 BLOB）+ 模型名/维度；空向量视为失败，不回写（§9）。"""
        if not vector:
            logger.debug(f"[Memory] 空向量不落库（id={mid}）")
            return
        blob = pack_vector(vector)
        emb_dim = int(dim) if dim is not None else len(vector)
        self._execute(
            "UPDATE memories SET embedding=?, embedding_model=?, embedding_dim=?"
            " WHERE bot_id=? AND id=?",
            (blob, str(model or ""), emb_dim, self.bot_id, mid),
        )

    def missing_embeddings(self, limit: int = 100) -> list[dict]:
        """需要回填 embedding 的 active 记忆（embedding IS NULL），新的优先。"""
        return self._fetch(
            "SELECT id, content FROM memories WHERE bot_id=? AND status='active'"
            " AND embedding IS NULL ORDER BY updated_at DESC LIMIT ?",
            (self.bot_id, max(1, int(limit))),
        )

    def touch_used(self, ids: list[str]) -> None:
        """召回命中后累计使用：``hit_count+1`` 与 ``last_used_at=now``（批量 IN 一条 SQL）。"""
        clean = [str(i) for i in ids if i]
        if not clean:
            return
        marks = ",".join("?" for _ in clean)
        self._execute(
            "UPDATE memories SET hit_count=hit_count+1, last_used_at=? WHERE bot_id=? AND id IN ("
            + marks + ")",
            (int(time.time()), self.bot_id, *clean),
        )

    # ── 审计 / 统计 ──────────────────────────────────────
    def audit(
        self,
        action: str,
        *,
        owner: str = "",
        ref_id: str = "",
        user_id: str = "",
        summary: str = "",
        source: str = "",
        source_task: str = "",
    ) -> None:
        """写一条审计事件（summary 压单行截断 200；ref_id 指向被操作记忆）。"""
        self._execute(
            "INSERT INTO memory_events (bot_id, ts, owner, action, ref_id, user_id,"
            " summary, source, source_task) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                self.bot_id,
                int(time.time()),
                str(owner or ""),
                str(action),
                str(ref_id or ""),
                str(user_id or ""),
                summarize(summary),
                str(source or ""),
                str(source_task or ""),
            ),
        )

    def recent_audit(self, owner: str | None = None, limit: int = 200) -> list[dict]:
        """最近的审计事件（时间倒序；可按 owner 过滤）。"""
        if owner:
            return self._fetch(
                "SELECT * FROM memory_events WHERE bot_id=? AND owner=?"
                " ORDER BY ts DESC, id DESC LIMIT ?",
                (self.bot_id, str(owner), int(limit)),
            )
        return self._fetch(
            "SELECT * FROM memory_events WHERE bot_id=? ORDER BY ts DESC, id DESC LIMIT ?",
            (self.bot_id, int(limit)),
        )

    def stats(self) -> dict:
        """记忆库统计：memories / owners / events / profiles / episodes。"""
        mem = self._fetch_one(
            "SELECT COUNT(*) AS total, COUNT(DISTINCT owner) AS owners FROM memories WHERE bot_id=?",
            (self.bot_id,),
        )
        events = self._fetch_one(
            "SELECT COUNT(*) AS n FROM memory_events WHERE bot_id=?",
            (self.bot_id,),
        )
        profiles = self._fetch_one(
            "SELECT COUNT(*) AS n FROM memories WHERE bot_id=? AND kind='profile' AND status='active'",
            (self.bot_id,),
        )
        episodes = self._fetch_one(
            "SELECT COUNT(*) AS n FROM episodes WHERE bot_id=?",
            (self.bot_id,),
        )
        return {
            "memories": int(mem["total"]) if mem else 0,
            "owners": int(mem["owners"]) if mem else 0,
            "events": int(events["n"]) if events else 0,
            "profiles": int(profiles["n"]) if profiles else 0,
            "episodes": int(episodes["n"]) if episodes else 0,
        }
