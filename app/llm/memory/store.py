"""长期记忆存储层（v2）：SQLite 单文件，按 bot_id 隔离。

v2 新增（S1）：
- 记忆**状态**：``status = active | negative | superseded``（expired 为按时间计算的过滤态，不落库）；
- 记忆**置信度**：``confidence`` + ``confirmed`` + ``evidence_count``（同一事实再次出现累计证据）；
- 失效时间：``expires_at``（NULL=长期）；
- owner 级重置线：``memory_owners.last_reset_at``（会话重置后旧记忆默认挂起不注入）；
- 纠错闭环：``correct / deny / confirm / supersede``，状态迁移全部写审计。

隔离仍由 ``owner`` 决定（私聊=用户、群公共、群成员、跨群）。线程模型不变：单连接 + RLock，
事件循环内请用 ``asyncio.to_thread`` 或 manager 封装。
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
import uuid
from difflib import SequenceMatcher
from typing import Any, Iterable

from app.llm import logger, llm_data_dir, safe_bot_id

# owner 段白名单（群号 / QQ 号 / 通用 key），防路径穿越与非法字符
_SAFE_PART_RE = re.compile(r"^[0-9a-zA-Z_\-]+$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id            TEXT PRIMARY KEY,
    bot_id        TEXT NOT NULL,
    owner         TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'fact',
    content       TEXT NOT NULL,
    keywords      TEXT NOT NULL DEFAULT '',
    importance    REAL NOT NULL DEFAULT 0.5,
    visibility    TEXT NOT NULL DEFAULT 'group',
    source        TEXT NOT NULL DEFAULT 'tool',
    source_user   TEXT NOT NULL DEFAULT '',
    source_task   TEXT NOT NULL DEFAULT '',
    embedding     BLOB,
    status        TEXT NOT NULL DEFAULT 'active',
    confidence    REAL NOT NULL DEFAULT 0.5,
    confirmed     INTEGER NOT NULL DEFAULT 0,
    expires_at    INTEGER,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    hit_count     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mem_owner ON memories(bot_id, owner, updated_at);
CREATE INDEX IF NOT EXISTS idx_mem_status ON memories(bot_id, owner, status);

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

CREATE TABLE IF NOT EXISTS memory_owners (
    bot_id        TEXT NOT NULL,
    owner         TEXT NOT NULL,
    last_reset_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bot_id, owner)
);
"""

# 仅 active 状态
_STATUS_ACTIVE = "active"
# 用户明确否认 → 下架不注入（可恢复）
_STATUS_NEGATIVE = "negative"
# 被 correct / 矛盾改口替换 → 不再注入（可审计）
_STATUS_SUPERSEDED = "superseded"

# 语义冲突判定：命中「否定极性对」即视为说法冲突（“喜欢喝美式” vs “喝不惯美式”）
_NEGATION_PAIRS = (
    ("喜欢", "不喜欢"), ("喜欢", "讨厌"), ("喜欢", "讨厌喝"), ("喜欢", "不喝"),
    ("喜欢", "喝不惯"), ("喜欢", "喝不了"), ("喜欢", "不习惯"), ("喜欢", "不爱"),
    ("喜欢", "戒"), ("爱", "不爱"), ("爱", "不喜欢"),
    ("住在", "搬"), ("住在", "离开"), ("住", "不住"), ("是", "不是"), ("有", "没有"),
    ("要吃", "不吃"), ("接受", "拒绝"), ("同意", "不同意"), ("要", "不要"),
)

# 近义合并阈值：改口/同义词改动（未命中否定对）→ 合并为一条（保留原 id）
_SIM_MERGE = 0.85


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
    """审计摘要：截断单行。"""
    s = str(text or "").replace("\n", " ").strip()
    if len(s) > limit:
        return s[:limit] + "…"
    return s


def text_similarity(a: str, b: str) -> float:
    """两个文本的相似度（0~1），用于近义合并。"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, str(a), str(b)).ratio()


def has_negation_conflict(a: str, b: str) -> bool:
    """判断两条说法是否构成语义冲突（一分句在 a、否定分句在 b，或反之）。"""
    if not a or not b or a == b:
        return False
    aa = str(a)
    bb = str(b)
    for x, y in _NEGATION_PAIRS:
        if (x in aa and y in bb) or (y in aa and x in bb):
            return True
    return False


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
            self._ensure_columns()

    def _ensure_columns(self) -> None:
        """老库迁移：为 memories 补齐 v2 新增列（幂等）。"""
        cols = {r["name"] for r in self._fetch("PRAGMA table_info(memories)")}
        additions = {
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "confidence": "REAL NOT NULL DEFAULT 0.5",
            "confirmed": "INTEGER NOT NULL DEFAULT 0",
            "expires_at": "INTEGER",
            "evidence_count": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, ddl in additions.items():
            if name not in cols:
                self._execute(f"ALTER TABLE memories ADD COLUMN {name} {ddl}")
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_owner_reset ON memory_owners(bot_id, owner)"
        )
        self._execute("CREATE INDEX IF NOT EXISTS idx_mem_status ON memories(bot_id, owner, status)")

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
        confidence: float = 0.5,
        status: str = _STATUS_ACTIVE,
        confirmed: int = 0,
        expires_at: int | None = None,
        evidence_count: int = 1,
        supersede_conflicts: bool = False,
    ) -> str:
        """写入/更新一条记忆。

        - 同 owner 同内容 → 同一 id，更新并累计 ``evidence_count``（再次出现=证据+1）；
        - 近义（未命中否定对，相似 ≥0.85）→ 合并到已有行（换成最新措辞，保留 id）；
        - ``supersede_conflicts=True``（用户明确/工具写入时）→ 先对同 owner 中
          「语义冲突」的 active 记忆判 superseded，避免新旧两条并存；
        - 原 negative/superseded 被再次说出 → 重新置 active。
        """
        content = str(content or "").strip()
        if not content:
            raise ValueError("记忆内容不能为空")
        content = content[:1000]
        keywords = (str(keywords or "").strip())[:300]
        now = int(time.time())
        confidence = max(0.0, min(1.0, float(confidence)))

        with self._lock:
            # 1) 精确判重
            existing = self._fetch_one(
                "SELECT * FROM memories WHERE bot_id=? AND owner=? AND content=?",
                (self.bot_id, owner, content),
            )
            if existing:
                self._update_existing(existing, content, owner, importance, keywords,
                                      source, source_user, source_task, visibility,
                                      confidence, status, confirmed, expires_at, now)
                return existing["id"]

            # 2) 语义冲突 → 先下架旧说（优先于近义合并，避免“我不喜欢”被并到“我喜欢”）
            if supersede_conflicts:
                self._supersede_conflicts(owner, content)

            # 3) 近义合并：命中 → 替换措辞并合并（仅 active 行）
            dup = self._find_near_dupe(owner, content)
            if dup:
                self._execute(
                    "UPDATE memories SET content=?, keywords=?, importance=?,"
                    " source=?, source_user=?, source_task=?, visibility=?,"
                    " confidence=MAX(confidence,?), status='active', confirmed=?,"
                    " expires_at=?, evidence_count=MIN(evidence_count+1,99), updated_at=? WHERE id=?",
                    (content, keywords, float(importance), source, str(source_user or ""),
                     str(source_task or ""), visibility, confidence, int(confirmed or 0),
                     expires_at, now, dup["id"]),
                )
                return dup["id"]

            # 4) 新写入
            mid = uuid.uuid4().hex
            self._execute(
                "INSERT INTO memories (id, bot_id, owner, kind, content, keywords,"
                " importance, visibility, source, source_user, source_task,"
                " status, confidence, confirmed, expires_at, evidence_count,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mid, self.bot_id, owner, kind, content, keywords, float(importance),
                 visibility, source, str(source_user or ""), str(source_task or ""),
                 status, confidence, int(confirmed or 0), expires_at,
                 max(1, int(evidence_count or 1)), now, now),
            )
            return mid

    def _update_existing(self, row, content, owner, importance, keywords, source,
                         source_user, source_task, visibility, confidence, status,
                         confirmed, expires_at, now) -> None:
        self._execute(
            "UPDATE memories SET importance=?, keywords=?, source=?, source_user=?,"
            " source_task=?, visibility=?, status=?, confidence=MAX(confidence,?),"
            " confirmed=?, expires_at=COALESCE(?, expires_at),"
            " evidence_count=MIN(evidence_count+1,99), updated_at=? WHERE id=?",
            (float(importance), keywords, source, str(source_user or ""),
             str(source_task or ""), visibility,
             _STATUS_ACTIVE if status != _STATUS_ACTIVE else row.get("status") or _STATUS_ACTIVE,
             confidence, int(confirmed or 0), expires_at, now, row["id"]),
        )

    def _find_near_dupe(self, owner: str, content: str) -> dict | None:
        rows = self.list_by_owner(owner, limit=500)
        for r in rows:
            if r["content"] == content:
                continue
            if text_similarity(r["content"], content) >= _SIM_MERGE:
                return r
        return None

    def _supersede_conflicts(self, owner: str, content: str) -> int:
        """把同 owner 内与 content 语义冲突的 active 记忆置为 superseded。"""
        conflicts = self.find_conflicts(owner, content)
        for row in conflicts:
            self._execute(
                "UPDATE memories SET status=?, updated_at=? WHERE id=?",
                (_STATUS_SUPERSEDED, int(time.time()), row["id"]),
            )
            self.audit("supersede", owner=owner, user_id=str(row.get("source_user") or ""),
                       summary=f"{row['content'][:60]} -> {content[:60]}", source="conflict")
        return len(conflicts)

    def find_conflicts(self, owner: str, content: str) -> list[dict]:
        """返回同 owner 中与给定内容存在语义冲突的 active 记忆（并不含自身措辞）。"""
        result = []
        for r in self.list_by_owner(owner, limit=1000):
            if r.get("status") != _STATUS_ACTIVE:
                continue
            if r["content"] == content:
                continue
            if has_negation_conflict(r["content"], content):
                result.append(r)
        return result

    # ── 读取 ─────────────────────────────────────────────
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

    def _status_clause(self, include_all: bool) -> str:
        return "" if include_all else " AND status='active'"

    def list_by_owner(self, owner: str, limit: int = 50, include_all: bool = False) -> list[dict]:
        return self._fetch(
            "SELECT * FROM memories WHERE bot_id=? AND owner=?" + self._status_clause(include_all)
            + " ORDER BY updated_at DESC, importance DESC LIMIT ?",
            (self.bot_id, owner, int(limit)),
        )

    def list_for_owners(self, owners: list[str], limit: int = 100, include_all: bool = False) -> list[dict]:
        """跨 owner 批量拉取（默认仅 active）。"""
        owners = [o for o in owners if o]
        if not owners:
            return []
        marks = ",".join("?" for _ in owners)
        return self._fetch(
            "SELECT * FROM memories WHERE bot_id=? AND owner IN (" + marks + ")"
            + self._status_clause(include_all)
            + " ORDER BY updated_at DESC LIMIT ?",
            (self.bot_id, *owners, int(limit)),
        )

    def search_in_owners(self, owners: list[str], query: str, limit: int = 100,
                         include_all: bool = False) -> list[dict]:
        """关键词搜索（content/keywords 模糊匹配），召回得分由 recall 层计算。"""
        owners = [o for o in owners if o]
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
        result = []
        for row in rows:
            hay = (row.get("content") or "") + " " + (row.get("keywords") or "")
            if all(t in hay for t in tokens):
                result.append(row)
        return result[: int(limit)]

    def find_by_query(self, owner: str, target: str, include_all: bool = False) -> list[dict]:
        """按 id 或内容/关键词命中查找（active 优先），供 correct/deny/confirm 使用。"""
        if not target:
            return []
        direct = self.get_owned(target, owner)
        if direct:
            return [direct]
        rows = self.list_by_owner(owner, limit=1000, include_all=include_all)
        tokens = _tokenize(target)
        matched = []
        for r in rows:
            hay = (r.get("content") or "") + " " + (r.get("keywords") or "")
            if any(t in hay for t in tokens):
                matched.append(r)
        matched.sort(key=lambda r: 0 if r.get("status") == _STATUS_ACTIVE else 1)
        return matched

    # ── 状态管理（v2） ───────────────────────────────────
    def set_status(self, mid: str, status: str, owner: str | None = None) -> bool:
        if owner:
            cur = self._execute(
                "UPDATE memories SET status=?, updated_at=? WHERE bot_id=? AND id=? AND owner=?",
                (status, int(time.time()), self.bot_id, mid, owner),
            )
        else:
            cur = self._execute(
                "UPDATE memories SET status=?, updated_at=? WHERE bot_id=? AND id=?",
                (status, int(time.time()), self.bot_id, mid),
            )
        return cur.rowcount > 0

    def deny(self, owner: str, target: str) -> int:
        """用户否认 → 命中记忆置 negative（下架不注入，可恢复）。"""
        rows = [r for r in self.find_by_query(owner, target) if r.get("status") == _STATUS_ACTIVE]
        for r in rows:
            self.set_status(r["id"], _STATUS_NEGATIVE, owner=owner)
            self.audit("deny", owner=owner, user_id=str(r.get("source_user") or ""),
                       summary=r["content"], source="manual")
        return len(rows)

    def confirm(self, owner: str, target: str) -> int:
        """用户确认 → 置信度上调并置 confirmed=1。"""
        rows = [r for r in self.find_by_query(owner, target) if r.get("status") == _STATUS_ACTIVE]
        for r in rows:
            self._execute(
                "UPDATE memories SET confidence=MIN(confidence+0.2, 1.0), confirmed=1,"
                " status='active', updated_at=? WHERE id=?",
                (int(time.time()), r["id"]),
            )
            self.audit("confirm", owner=owner, user_id=str(r.get("source_user") or ""),
                       summary=r["content"], source="manual")
        return len(rows)

    def supersede(self, owner: str, target: str) -> int:
        """把匹配的 active 记忆置 superseded（correct 的旧条处理）。"""
        rows = [r for r in self.find_by_query(owner, target) if r.get("status") == _STATUS_ACTIVE]
        for r in rows:
            self.set_status(r["id"], _STATUS_SUPERSEDED, owner=owner)
        return len(rows)

    def correct(self, owner: str, old_target: str, new_content: str,
                new_confidence: float = 0.85, source_user: str = "") -> str | None:
        """纠错：旧说置 superseded，写入新说 active。返回新 id；无旧说时仅写入。"""
        new_content = (new_content or "").strip()
        if not new_content:
            return None
        old = self.supersede(owner, old_target)
        mid = self.upsert_fact(
            new_content, owner,
            importance=0.8, source="correct", source_user=str(source_user or ""),
            confidence=float(new_confidence), supersede_conflicts=True,
        )
        self.audit("correct", owner=owner, user_id=str(source_user or ""),
                   summary=f"{old} 条旧记忆 -> {new_content}", source="manual")
        return mid

    # ── owner 重置线（v2） ───────────────────────────────
    def set_reset(self, owner: str) -> None:
        self._execute(
            "INSERT INTO memory_owners (bot_id, owner, last_reset_at) VALUES (?,?,?)"
            " ON CONFLICT(bot_id, owner) DO UPDATE SET last_reset_at=excluded.last_reset_at",
            (self.bot_id, owner, int(time.time())),
        )

    def get_reset(self, owner: str) -> int:
        row = self._fetch_one(
            "SELECT last_reset_at FROM memory_owners WHERE bot_id=? AND owner=?",
            (self.bot_id, owner),
        )
        return int(row["last_reset_at"]) if row else 0

    # ── 删除 ─────────────────────────────────────────────
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
        """物理删除该 owner 下内容包含任一查询词的记忆，返回删除条数。"""
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

    def count_active_by_owner(self, owner: str) -> int:
        row = self._fetch_one(
            "SELECT COUNT(*) AS n FROM memories WHERE bot_id=? AND owner=? AND status='active'",
            (self.bot_id, owner),
        )
        return int(row["n"]) if row else 0

    # ── 淘汰 ─────────────────────────────────────────────
    def enforce_limit(self, owner: str, max_per_owner: int) -> int:
        """超过上限时按「重要度低 + 旧」优先物理淘汰 active 记忆。"""
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
