"""知识库存储层：SQLite + 向量 BLOB + 余弦相似度。

每个 bot 一个文件：data/llm/knowledge/<bot_id>_knowledge.db
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.llm import llm_data_dir, safe_bot_id
from app.llm.knowledge.vector import SQLiteVecVectorStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id          TEXT PRIMARY KEY,
    bot_id      TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'manual',
    embedding   BLOB,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_bot ON knowledge_chunks(bot_id, updated_at);
"""


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v)) or 1.0


def cosine(a: list[float], b: list[float]) -> float:
    return _dot(a, b) / (_norm(a) * _norm(b) or 1.0)


class KnowledgeStore:
    """SQLite 单文件知识库，线程安全。"""

    def __init__(self, bot_id: Any, db_path: str | None = None) -> None:
        self.bot_id = safe_bot_id(bot_id)
        if db_path is None:
            kb_dir = Path(llm_data_dir()) / "knowledge"
            kb_dir.mkdir(parents=True, exist_ok=True)
            self.path = kb_dir / f"{self.bot_id}_knowledge.db"
        else:
            self.path = Path(db_path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self.vector_backend = SQLiteVecVectorStore(self.path)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
        try:
            self.vector_backend.close()
        except Exception:
            pass

    def add(
        self,
        content: str,
        *,
        title: str = "",
        embedding: list[float] | None = None,
        source: str = "manual",
    ) -> str:
        cid = uuid.uuid4().hex[:16]
        now = int(time.time())
        blob = json.dumps(embedding).encode("utf-8") if embedding else None
        with self._lock:
            self._conn.execute(
                "INSERT INTO knowledge_chunks (id, bot_id, title, content, source, embedding, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (cid, self.bot_id, title, content, source, blob, now, now),
            )
            self._conn.commit()
        if embedding:
            self.vector_backend.add(self.bot_id, cid, embedding)
        return cid

    def delete(self, cid: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM knowledge_chunks WHERE id=?", (cid,))
            self._conn.commit()
        self.vector_backend.delete(cid)
        return cur.rowcount > 0

    def get(self, cid: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM knowledge_chunks WHERE id=?", (cid,)
            ).fetchone()
        return dict(row) if row else None

    def list(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, content, source, created_at, updated_at FROM knowledge_chunks "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def search(
        self,
        embedding: list[float],
        *,
        limit: int = 5,
    ) -> list[dict]:
        # 优先使用 sqlite-vec ANN 后端（安装时自动启用）
        if self.vector_backend.enabled():
            hits = self.vector_backend.search(self.bot_id, embedding, limit=limit)
            if hits:
                chunk_ids = [str(h.get("chunk_id", "")) for h in hits]
                rows_by_id = {}
                if chunk_ids:
                    placeholders = ",".join("?" for _ in chunk_ids)
                    with self._lock:
                        rows = self._conn.execute(
                            f"SELECT * FROM knowledge_chunks WHERE id IN ({placeholders})",
                            chunk_ids,
                        ).fetchall()
                    rows_by_id = {str(r["id"]): dict(r) for r in rows}
                result = []
                for hit in hits:
                    row = rows_by_id.get(str(hit.get("chunk_id", "")))
                    if row is None:
                        continue
                    result.append({**row, "_score": hit.get("_score", 0.0), "_distance": hit.get("distance")})
                return result[:limit]

        # 默认 SQLite 余弦扫描（数据量不大时性能足够）
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM knowledge_chunks WHERE embedding IS NOT NULL ORDER BY updated_at DESC"
            ).fetchall()
        scored = []
        for row in rows:
            try:
                vec = json.loads(row["embedding"].decode("utf-8"))
            except Exception:
                continue
            score = cosine(embedding, vec)
            scored.append({**dict(row), "_score": score})
        scored.sort(key=lambda x: x["_score"], reverse=True)
        return scored[:limit]
