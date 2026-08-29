"""知识库向量后端适配器。

默认实现 SQLite 余弦扫描；当环境中安装了 ``sqlite-vec`` 时，
自动启用 ANN 虚拟表检索，避免大数据量下的全表扫描。

用法（无需改动业务代码）：
    if store.vector_backend.enabled():
        store.vector_backend.add(...)
        store.vector_backend.search(...)
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


class SQLiteVecVectorStore:
    """sqlite-vec 可选后端（按 bot_id 共享同一个 SQLite 文件）。

    enabled() 在扩展不可用或初始化失败时返回 False，调用方应回退到默认余弦检索。
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._dim: int | None = None
        self._enabled = False
        try:
            import sqlite_vec  # type: ignore

            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vector_rows (
                    rowid    INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id   TEXT NOT NULL,
                    chunk_id TEXT NOT NULL UNIQUE
                )
                """
            )
            conn.commit()
            self._conn = conn
            self._enabled = True
        except Exception:
            self._conn = None
            self._enabled = False

    def enabled(self) -> bool:
        return self._enabled

    def _get_dim(self) -> int | None:
        if self._dim is not None:
            return self._dim
        if self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='vec_chunks'"
            ).fetchone()
            if row and "float[" in row[0]:
                import re

                m = re.search(r"float\[(\d+)\]", row[0])
                if m:
                    self._dim = int(m.group(1))
        except Exception:
            pass
        return self._dim

    def _ensure_table(self, dim: int) -> bool:
        if self._conn is None:
            return False
        if not dim:
            return False
        if self._get_dim() and self._get_dim() != dim:
            raise ValueError(f"sqlite-vec 维度不一致：已有 {self._get_dim()}，新增 {dim}")
        try:
            self._conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(embedding FLOAT[{dim}])"
            )
            self._conn.commit()
            self._dim = dim
            return True
        except Exception:
            self._enabled = False
            return False

    def add(self, bot_id: Any, chunk_id: str, embedding: list[float]) -> bool:
        if not self._enabled or self._conn is None:
            return False
        if not self._ensure_table(len(embedding)):
            return False
        try:
            with self._conn:
                cur = self._conn.execute(
                    "INSERT OR REPLACE INTO vector_rows(bot_id, chunk_id) VALUES (?, ?)",
                    (str(bot_id), str(chunk_id)),
                )
                rowid = cur.lastrowid
                if cur.lastrowid is None:
                    # 已存在时通过唯一约束回读
                    row = self._conn.execute(
                        "SELECT rowid FROM vector_rows WHERE chunk_id=?", (str(chunk_id),)
                    ).fetchone()
                    rowid = row[0] if row else None
                if rowid is None:
                    return False
                self._conn.execute(
                    "INSERT OR REPLACE INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                    (int(rowid), json.dumps(list(embedding), ensure_ascii=False)),
                )
            return True
        except Exception:
            return False

    def search(
        self,
        bot_id: Any,
        embedding: list[float],
        *,
        limit: int = 5,
    ) -> list[dict]:
        """返回 [{chunk_id, distance, score}]，score 范围约 [-1,1]（cosine 近似）。"""
        if not self._enabled or self._conn is None:
            return []
        if not self._ensure_table(len(embedding)):
            return []
        vec_json = json.dumps(list(embedding), ensure_ascii=False)
        try:
            rows = self._conn.execute(
                "SELECT vr.chunk_id, vc.distance "
                "FROM vec_chunks vc "
                "JOIN vector_rows vr ON vr.rowid = vc.rowid "
                "WHERE vr.bot_id = ? AND vc.embedding MATCH ? "
                "ORDER BY vc.distance LIMIT ?",
                (str(bot_id), vec_json, int(limit)),
            ).fetchall()
        except Exception:
            return []
        results = []
        for chunk_id, distance in rows:
            # sqlite-vec 返回 L2 距离；转换为便于 UI 展示的相似度（0~1）
            score = max(0.0, 1.0 - float(distance or 0.0) / 10.0)
            results.append({"chunk_id": str(chunk_id), "distance": float(distance or 0.0), "_score": score})
        return results

    def delete(self, chunk_id: str) -> None:
        if not self._enabled or self._conn is None:
            return
        try:
            with self._conn:
                row = self._conn.execute(
                    "SELECT rowid FROM vector_rows WHERE chunk_id=?", (str(chunk_id),)
                ).fetchone()
                if row:
                    self._conn.execute("DELETE FROM vec_chunks WHERE rowid=?", (row[0],))
                    self._conn.execute("DELETE FROM vector_rows WHERE rowid=?", (row[0],))
        except Exception:
            pass

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
