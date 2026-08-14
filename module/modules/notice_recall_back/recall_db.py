"""防撤回持久化消息库（吸收自 astrbot NapcatPrerequisite message_db）。

- 消息事件到达时异步落盘（JSON，module/data/notice_recall_back/message_db_<bot>.json）；
- 撤回时先查内存缓存、未命中查磁盘——重启后仍能恢复被撤回消息；
- 支持按总量/时长自动清理（db_max_messages / db_retention_minutes）。
"""

from __future__ import annotations

import asyncio
import json
import os
import time

from app.core.logger import module_logger


class RecallDB:
    """按 message_id 存储消息快照的 JSON 持久化库。"""

    def __init__(self, file_path: str) -> None:
        self._file = file_path
        self._lock = asyncio.Lock()
        self._db: dict = {}
        self._loaded = False

    # ── 内部 ──────────────────────────────────────────────────

    def _ensure(self) -> None:
        if self._loaded:
            return
        if os.path.exists(self._file):
            try:
                with open(self._file, encoding="utf-8") as f:
                    self._db = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._db = {}
        if "data" not in self._db:
            self._db["data"] = {"total": 0, "updated": time.time()}
        self._loaded = True

    def _write(self) -> None:
        self._db["data"]["updated"] = time.time()
        try:
            os.makedirs(os.path.dirname(self._file), exist_ok=True)
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump(self._db, f, ensure_ascii=False)
        except OSError as e:
            module_logger.error(f"[RecallDB] 写入失败: {e}")

    # ── CRUD ──────────────────────────────────────────────────

    async def store(self, message_id: str, data: dict, max_per_group: int = 0) -> bool:
        """存入一条消息快照。已存在则跳过；超过每群上限时淘汰该群最旧消息。"""
        self._ensure()
        async with self._lock:
            if message_id in self._db:
                return False
            data["time"] = time.time()
            self._db[message_id] = data
            self._db["data"]["total"] += 1
            # 每群上限（旧版 max_messages_per_group 语义）：淘汰该群最旧消息
            if max_per_group > 0:
                group_id = str(data.get("group_id", "") or "")
                group_ids = [
                    k for k, v in self._db.items()
                    if k != "data" and str(v.get("group_id", "") or "") == group_id
                ]
                while len(group_ids) > max_per_group:
                    oldest = min(group_ids, key=lambda k: self._db[k].get("time", 0))
                    del self._db[oldest]
                    group_ids.remove(oldest)
                    self._db["data"]["total"] = max(0, self._db["data"]["total"] - 1)
            self._write()
        return True

    async def get(self, message_id: str) -> dict | None:
        """按 message_id 查询消息快照。"""
        self._ensure()
        v = self._db.get(str(message_id))
        return v if v and v != "data" else None

    async def has(self, message_id: str) -> bool:
        """检查消息是否存在。"""
        self._ensure()
        mid = str(message_id)
        return mid in self._db and mid != "data"

    async def delete(self, message_id: str) -> bool:
        """删除一条消息。"""
        self._ensure()
        mid = str(message_id)
        async with self._lock:
            if mid not in self._db or mid == "data":
                return False
            del self._db[mid]
            self._db["data"]["total"] = max(0, self._db["data"]["total"] - 1)
            self._write()
        return True

    # ── 清理 ──────────────────────────────────────────────────

    async def cleanup(self, max_total: int = 0, max_age_minutes: int = 0) -> int:
        """按总量和时长清理旧消息。返回清理条数。"""
        self._ensure()
        now = time.time()
        removed = 0

        async with self._lock:
            ids = [k for k in self._db if k != "data"]

            # 按时间排序（旧的在前）
            ids.sort(key=lambda k: self._db[k].get("time", 0))

            # 超量淘汰
            if max_total > 0:
                while len(ids) - removed > max_total:
                    mid = ids[removed]
                    del self._db[mid]
                    removed += 1

            # 超时淘汰
            if max_age_minutes > 0:
                cutoff = now - max_age_minutes * 60
                for mid in ids:
                    if mid not in self._db:
                        continue
                    if self._db[mid].get("time", 0) < cutoff:
                        del self._db[mid]
                        removed += 1

            if removed:
                self._db["data"]["total"] = len(self._db) - 1
                self._write()
                module_logger.info(f"[RecallDB] 清理了 {removed} 条消息")

        return removed

    # ── 统计 ──────────────────────────────────────────────────

    @property
    def total(self) -> int:
        self._ensure()
        return self._db["data"].get("total", 0)
