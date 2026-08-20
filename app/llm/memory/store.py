"""长期记忆存储层（P0）：SQLite 单文件，按 bot_id 隔离。

数据布局：``data/llm/<bot_id>/memory/memory.db``（与 history / tasks_data 同级）。

- ``memories``：事实表。``owner`` 是隔离维度的核心：
    - ``user_<uid>``            私聊用户画像（私聊可见、跨群可选）
    - ``group_<gid>``           群公共事实（全群可见）
    - ``user_<uid>@group_<gid>`` 群内某成员画像（仅该群可见）
    - ``global``                全局设定（不使用用户内容）
- ``memory_events``：审计表（write/read/inject/delete/forget/clear/distill）。

线程模型：单连接 + RLock（记忆操作都是毫秒级小事务）；事件循环内请用
``asyncio.to_thread`` 包装（与 session.history.save_session 同样风格）。
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
import uuid

from typing import Any, Iterable

from app.llm import logger, llm_data_dir, safe_bot_id

# owner 段白名单（群号 / QQ 号 / 通用 key），防路径穿越与非法字符
_SAFE_PART_RE = re.compile(r"^[0-9a-zA-Z_\-]+$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    bot_id      TEXT NOT NULL,
    owner       TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'fact',
    content     TEXT NOT NULL,
    keywords    TEXT NOT NULL DEFAULT '',
    importance  REAL NOT NULL DEFAULT 0.5,
    visibility  TEXT NOT NULL DEFAULT 'group',
    source      TEXT NOT NULL DEFAULT 'tool',
    source_user TEXT NOT NULL DEFAULT '',
    source_task TEXT NOT NULL DEFAULT '',
    embedding   BLOB,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    hit_count   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mem_owner ON memories(bot_id, owner, updated_at);

CREATE TABLE IF NOT EXISTS memory_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id      TEXT NOT NULL,
    ts          INTEGER NOT NULL,
    owner       TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,
    user_id     TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    source_task TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_owner ON memory_events(bot_id, owner, ts);
"""


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
    """按 session_id 推导 owner（会话级记忆落点）。

    - private_<uid> → user_<uid>
    - group_<gid>（无 user_id）→ group_<gid>
    - group_<gid> + user_id → user_<uid>@group_<gid>（群内成员画像）
    """
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
    """审计摘要：截断单行。"""
    s = str(text or "").replace("\n", " ").strip()
    if len(s) > limit:
        return s[:limit] + "…"
    return s


class MemoryStore:
    """SQLite 记忆库（每 bot 一个实例；owner 路由见模块级 helper）。"""

    def __init__(self, bot_id: Any, db_path: str | None = None) -> None:
        self.bot_id = safe_bot_id(bot_id)
        if db_path is None:
            db_path = os.path.join(
                llm_data_dir(), self.bot_id, "memory", "memory.db"
            )
        self.db_path = str(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except Exception:
                pass
            self._conn.commit()

    # ── 生命周期 ─────────────────────────────────────────
    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def _execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def _fetch(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]

    def _fetch_one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self._fetch(sql, params)
        return rows[0] if rows else None

    # ── 事实 CRUD ────────────────────────────────────────
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
        kind: str = "fact",
        visibility: str = "group",
    ) -> str:
        """写入/更新一条事实。同 owner 同内容视为同一条（更新时间与重要度）。返回 id。"""
        content = str(content or "").strip()
        if not content:
            raise ValueError("记忆内容不能为空")
        content = content[:1000]
        keywords = (str(keywords or "").strip())[:300]
        now = int(time.time())
        existing = self._fetch_one(
            "SELECT id FROM memories WHERE bot_id=? AND owner=? AND content=?",
            (self.bot_id, owner, content),
        )
        if existing:
            self._execute(
                "UPDATE memories SET importance=?, keywords=?, source=?,"
                " source_user=?, source_task=?, visibility=?, updated_at=? WHERE id=?",
                (float(importance), keywords, source, str(source_user or ""),
                 str(source_task or ""), visibility, now, existing["id"]),
            )
            return existing["id"]
        mid = uuid.uuid4().hex
        self._execute(
            "INSERT INTO memories (id, bot_id, owner, kind, content, keywords,"
            " importance, visibility, source, source_user, source_task,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, self.bot_id, owner, kind, content, keywords, float(importance),
             visibility, source, str(source_user or ""), str(source_task or ""),
             now, now),
        )
        return mid

    def get(self, mid: str) -> dict | None:
        return self._fetch_one(
            "SELECT * FROM memories WHERE bot_id=? AND id=?",
            (self.bot_id, mid),
        )

    def get_owned(self, mid: str, owner: str) -> dict | None:
        return self._fetch_one(
            "SELECT * FROM memories WHERE bot_id=? AND id=? AND owner=?",
            (self.bot_id, mid, owner),
        )

    def list_by_owner(self, owner: str, limit: int = 50) -> list[dict]:
        return self._fetch(
            "SELECT * FROM memories WHERE bot_id=? AND owner=? "
            "ORDER BY updated_at DESC, importance DESC LIMIT ?",
            (self.bot_id, owner, int(limit)),
        )

    def list_for_owners(self, owners: list[str], limit: int = 100) -> list[dict]:
        """跨 owner 批量拉取（不超过 SQLite 变量上限时用 IN）。"""
        owners = [o for o in owners if o]
        if not owners:
            return []
        marks = ",".join("?" for _ in owners)
        return self._fetch(
            f"SELECT * FROM memories WHERE bot_id=? AND owner IN ({marks}) "
            f"ORDER BY updated_at DESC LIMIT ?",
            (self.bot_id, *owners, int(limit)),
        )

    def search_in_owners(self, owners: list[str], query: str, limit: int = 100) -> list[dict]:
        """关键词搜索（content/keywords 模糊匹配），召回得分由 recall 层计算。"""
        owners = [o for o in owners if o]
        if not owners:
            return []
        marks = ",".join("?" for _ in owners)
        rows = self._fetch(
            f"SELECT * FROM memories WHERE bot_id=? AND owner IN ({marks}) "
            f"ORDER BY updated_at DESC LIMIT ?",
            (self.bot_id, *owners, max(int(limit), 200)),
        )
        tokens = _tokenize(query)
        if not tokens:
            return rows
        result = []
        for row in rows:
            hay = (row.get("content") or "") + " " + (row.get("keywords") or "")
            if all(t in hay for t in tokens):
                result.append(row)
        return result[: int(limit)]

    def delete_fact(self, mid: str, owner: str | None = None) -> bool:
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
        return cur.rowcount > 0

    def delete_by_query(self, owner: str, query: str) -> int:
        """删除该 owner 下内容包含任一查询词的记忆，返回删除条数。"""
        tokens = _tokenize(query)
        if not tokens:
            return 0
        rows = self._fetch(
            "SELECT id, content, keywords FROM memories WHERE bot_id=? AND owner=?",
            (self.bot_id, owner),
        )
        target = []
        for r in rows:
            hay = (r.get("content") or "") + " " + (r.get("keywords") or "")
            if any(t in hay for t in tokens):
                target.append(r["id"])
        for mid in target:
            self.delete_fact(mid, owner=owner)
        return len(target)

    def clear(self, owner: str) -> int:
        cur = self._execute(
            "DELETE FROM memories WHERE bot_id=? AND owner=?",
            (self.bot_id, owner),
        )
        return cur.rowcount

    def count_by_owner(self, owner: str) -> int:
        row = self._fetch_one(
            "SELECT COUNT(*) AS n FROM memories WHERE bot_id=? AND owner=?",
            (self.bot_id, owner),
        )
        return int(row["n"]) if row else 0

    # ── 淘汰 ─────────────────────────────────────────────
    def enforce_limit(self, owner: str, max_per_owner: int) -> int:
        """超过上限时按「重要度低 + 旧」优先淘汰，返回淘汰条数。"""
        max_per_owner = max(1, int(max_per_owner))
        rows = self.list_by_owner(owner, limit=10_000)
        if len(rows) <= max_per_owner:
            return 0
        removed = 0
        scored = []
        now = int(time.time())
        for r in rows:
            age_days = max(0.0, (now - int(r.get("updated_at") or now)) / 86400.0)
            score = float(r.get("importance") or 0) / (1.0 + age_days / 7.0)
            scored.append((score, r["id"]))
        scored.sort(key=lambda x: x[0])  # 小的先删
        overflow = len(scored) - max_per_owner
        for _score, mid in scored[:overflow]:
            self.delete_fact(mid, owner=owner)
            removed += 1
        return removed

    # ── 审计 ─────────────────────────────────────────────
    def audit(
        self,
        action: str,
        *,
        owner: str = "",
        user_id: str = "",
        summary: str = "",
        source: str = "",
        source_task: str = "",
    ) -> None:
        self._execute(
            "INSERT INTO memory_events (bot_id, ts, owner, action, user_id,"
            " summary, source, source_task) VALUES (?,?,?,?,?,?,?,?)",
            (self.bot_id, int(time.time()), str(owner or ""), str(action),
             str(user_id or ""), summarize(summary), str(source or ""),
             str(source_task or "")),
        )

    def recent_audit(self, owner: str | None = None, limit: int = 200) -> list[dict]:
        if owner:
            return self._fetch(
                "SELECT * FROM memory_events WHERE bot_id=? AND owner=? "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (self.bot_id, owner, int(limit)),
            )
        return self._fetch(
            "SELECT * FROM memory_events WHERE bot_id=? ORDER BY ts DESC, id DESC LIMIT ?",
            (self.bot_id, int(limit)),
        )

    def stats(self) -> dict:
        row = self._fetch_one(
            "SELECT COUNT(*) AS total, COUNT(DISTINCT owner) AS owners FROM memories WHERE bot_id=?",
            (self.bot_id,),
        )
        ev = self._fetch_one(
            "SELECT COUNT(*) AS n FROM memory_events WHERE bot_id=?",
            (self.bot_id,),
        )
        return {
            "memories": int(row["total"]) if row else 0,
            "owners": int(row["owners"]) if row else 0,
            "events": int(ev["n"]) if ev else 0,
        }


def _tokenize(text: str) -> list[str]:
    """召回 token：按空白/符号切词 + 中文整句（不做分词），过滤 <1 字符。"""
    text = str(text or "")
    parts = re.split(r"[\s，。！？、;；:：,.!?/\\|]+", text)
    out = [p for p in parts if len(p) >= 1]
    return out[:8]  # 防止超长 query 撑爆匹配
