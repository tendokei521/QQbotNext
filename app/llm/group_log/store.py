"""群聊可见事件存储（按记录分片，内存环形 + JSONL 追加）。

## 为什么有这个模块

会话历史（``session.py``）回答的是"**这场对话说过什么**"；它只在消息**触发**
机器人时才写入（``pipeline.check_trigger`` 不通过就直接 return），而且完全不含
"谁给谁贴了表情""谁戳了谁"这类互动。于是模型看群里发生的事，只剩"临时拉一次
在线历史"这一个残缺窗口——默认还是关的。

本模块补的是**另一个面**：在事件接收时持续记下"群聊最近可见事件流"，
装配时把其中一段渲染成环境背景透传。两者是**独立的两套内容**：

- 对话记录 = 规范对话形态（原文、不截断），只由会话层维护；
- 群聊记录 = 环境形态（可折叠、有预算），只由本模块维护。

## 存储口径

- 分片键是 ``scope``（``group:<群号>`` / ``private:<对方QQ>``），跨群/跨会话天然隔离；
- 内存：每分片一个定长 ``deque`` + 幂等键索引 + ``message_id → 事件`` 索引；
- 落盘：``data/<bot_id>/<scope>/YYYY-MM-DD.jsonl``，只追加；文件超过保留量 2 倍时
  重写压缩（原子替换）；
- 写入：``append_many`` 只入队（不阻塞事件分发），由单个后台任务批量 flush；
- 保留：``retention_count`` 条 / ``retention_hours`` 小时，二者**独立**于装配窗口——
  本地能查多远（保留）与一次请求塞多少（装配）是两件事。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

from app.core.logger import logger
from app.llm.group_log.events import KIND_EMOJI, LogEvent

DEFAULT_RETENTION_COUNT = 2000
DEFAULT_RETENTION_HOURS = 24
DEFAULT_FLUSH_INTERVAL = 0.5
DEFAULT_FLUSH_BATCH = 32
#: 内存里最多保留多少条被"背压丢弃"的计数
DEFAULT_COMPACT_FACTOR = 2


class GroupLogStore:
    """一个 bot 的事件存储；按 ``scope`` 分片。

    线程/任务安全口径：内存结构的修改都发生在事件循环线程内（``append_many`` /
    ``recent`` 都是同步方法，由 asyncio 单线程保证），文件 IO 走 ``asyncio.to_thread``。
    因此不需要额外的锁；测试里可并发调用 ``append_many`` 验证不串。
    """

    def __init__(
        self,
        bot_id: Any = "",
        data_dir: str | Path | None = None,
        *,
        retention_count: int = DEFAULT_RETENTION_COUNT,
        retention_hours: float = DEFAULT_RETENTION_HOURS,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL,
        flush_batch: int = DEFAULT_FLUSH_BATCH,
        log: Any = None,
    ) -> None:
        self.bot_id = str(bot_id or "")
        self.data_dir = Path(data_dir).expanduser() if data_dir else None
        self.retention_count = max(0, int(retention_count))
        self.retention_hours = float(retention_hours)
        self.flush_interval = max(0.05, float(flush_interval))
        self.flush_batch = max(1, int(flush_batch))
        self.log = log or logger.add_info(f"#{self.bot_id}" if self.bot_id else "GroupLog")

        self._events: dict[str, deque] = {}
        self._stamps: dict[str, deque] = {}
        self._keys: dict[str, set] = {}
        self._by_message: dict[str, dict[str, list]] = {}
        self._covered: dict[str, set] = {}
        self._pending: dict[str, list] = {}
        self._seq = 0
        self._writer: asyncio.Task | None = None
        self._closing = False

        # 可观测计数：不静默丢数据（出现丢弃必须能查到）
        self.stats: dict[str, int] = {
            "appended": 0,
            "deduped": 0,
            "dropped_by_retention": 0,
            "dropped_by_backpressure": 0,
            "written": 0,
            "loaded": 0,
        }

    # ==================== 生命周期 ====================

    async def start(self) -> None:
        """启动后台写任务并恢复最近的记录（幂等，可重复调用）。"""
        if self._writer is None or self._writer.done():
            self._closing = False
            self._writer = asyncio.create_task(self._write_loop())
        await self._load_recent_files()

    async def close(self, *, flush: bool = True) -> None:
        """停止写任务并落盘（幂等）。"""
        self._closing = True
        if flush:
            try:
                await self.flush()
            except Exception as e:  # noqa: BLE001 - 关闭阶段失败不应上抛
                self.log.warning(f"[GroupLog] 关闭落盘失败: {e}")
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.cancel()
            try:
                await writer
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def flush(self) -> int:
        """把待写队列落盘，返回写入条数。"""
        written = 0
        for scope, items in list(self._pending.items()):
            if not items:
                continue
            batch, self._pending[scope] = list(items), []
            written += await self._append_to_disk(scope, batch)
        if written:
            self.stats["written"] += written
            for scope in list(self._events):
                await self._maybe_compact(scope)
        return written

    # ==================== 写入 ====================

    def append_many(self, events: Iterable[LogEvent]) -> int:
        """幂等入队；返回实际接受（未重复、未越界）的条数。"""
        accepted = 0
        for event in events or []:
            if not isinstance(event, LogEvent):
                try:
                    event = LogEvent.from_dict(dict(event))  # type: ignore[arg-type]
                except Exception:  # noqa: BLE001 - 坏数据不阻断记录
                    continue
            scope = event.scope
            if not scope:
                continue
            if self.retention_count <= 0:
                self.stats["dropped_by_retention"] += 1
                continue
            keys = self._keys.setdefault(scope, set())
            if event.key in keys:
                self.stats["deduped"] += 1
                continue
            self._index(scope, event)
            self._pending.setdefault(scope, []).append(event)
            self.stats["appended"] += 1
            accepted += 1
            self._enforce_retention(scope)
        return accepted

    def mark_conversation_covered(self, scope: str, message_ids: Iterable[Any]) -> None:
        """登记"这批消息已由会话历史呈现"，渲染层据此避免双源重复。

        只影响渲染，**不影响存储**——记录面保持完整，是渲染层做选择。
        """
        bucket = self._covered.setdefault(str(scope or ""), set())
        for mid in message_ids or []:
            text = str(mid or "").strip()
            if text:
                bucket.add(text)

    # ==================== 读取 ====================

    def recent(
        self,
        scope: str,
        *,
        minutes: float | None = None,
        limit: int | None = None,
    ) -> list[LogEvent]:
        """取某分片最近的事件（按时间升序）。

        ``minutes`` 给定时先按时间窗口裁剪；``limit`` 给定时只保留**最新的** N 条
        （仍按升序返回）。
        """
        scope = str(scope or "")
        if not scope:
            return []
        self.prune(scope)
        items = list(self._events.get(scope, ()))
        if minutes and float(minutes) > 0:
            cutoff = int(time.time() - float(minutes) * 60)
            items = [e for e in items if e.ts >= cutoff]
        if limit and int(limit) > 0:
            items = items[-int(limit):]
        return items

    def reactions_of(self, scope: str, message_id: Any) -> list[dict]:
        """某条消息上**当前有效**的表情聚合：``[{emoji_id, count, users}]``。

        "贴前先读"就靠它：已存在的表情不再重复贴（幂等），并让模型看到
        "这条上已经有 👍×2"。
        """
        events = self._by_message.get(str(scope or ""), {}).get(str(message_id or ""), [])
        if not events:
            return []
        state: dict[str, dict] = {}
        for event in sorted(events, key=lambda e: e.ts):
            if event.kind != KIND_EMOJI:
                continue
            emoji_id = event.emoji_id
            if not emoji_id:
                continue
            slot = state.setdefault(emoji_id, {"emoji_id": emoji_id, "count": 0, "users": []})
            if event.is_add:
                slot["count"] += 1
                if event.user_id and event.user_id not in slot["users"]:
                    slot["users"].append(event.user_id)
            else:
                slot["count"] = max(0, slot["count"] - 1)
                if event.user_id in slot["users"]:
                    slot["users"].remove(event.user_id)
        return [slot for slot in state.values() if slot["count"] > 0]

    def my_actions_on(self, scope: str, message_id: Any) -> list[str]:
        """我在某条消息上做过的动作（如 ``["emoji:66"]``），供模型行为反馈。"""
        events = self._by_message.get(str(scope or ""), {}).get(str(message_id or ""), [])
        acts: list[str] = []
        for event in sorted(events, key=lambda e: e.ts):
            if not event.by_me or not event.is_add:
                continue
            if event.kind == KIND_EMOJI and event.emoji_id:
                tag = f"emoji:{event.emoji_id}"
                if tag not in acts:
                    acts.append(tag)
        return acts

    def covered_ids(self, scope: str) -> set[str]:
        return set(self._covered.get(str(scope or ""), set()))

    def scopes(self) -> list[str]:
        return list(self._events.keys())

    # ==================== 保留与淘汰 ====================

    def prune(self, scope: str) -> int:
        """按保留窗口淘汰过期事件，返回淘汰条数。

        **按入账时间（wall clock）而不是事件自带时间**：迟到的记录、或重启后从磁盘
        恢复的旧记录，事件时间可能已是几小时前，若按事件时间淘汰就会刚记下就被删掉。
        事件时间只用于展示与排序。
        """
        if self.retention_hours <= 0:
            return 0
        cutoff = time.time() - self.retention_hours * 3600
        scope = str(scope or "")
        bucket = self._events.get(scope)
        stamps = self._stamps.get(scope)
        if not bucket:
            return 0
        if stamps is None:  # 兜底：状态不完整时按"无过期"处理，不误删数据
            return 0
        removed = 0
        while stamps and stamps[0] < cutoff:
            stamps.popleft()
            self._drop(scope, bucket.popleft())
            removed += 1
        if removed:
            self.stats["dropped_by_retention"] += removed
        return removed

    def prune_all(self) -> int:
        return sum(self.prune(scope) for scope in self.scopes())

    def _index(self, scope: str, event: LogEvent) -> None:
        bucket = self._events.setdefault(scope, deque())
        stamps = self._stamps.setdefault(scope, deque())
        dropped = bucket.popleft() if len(bucket) >= self.retention_count else None
        if dropped is not None:
            stamps.popleft()
        bucket.append(event)
        stamps.append(time.time())  # 入账时间：保留窗口只看它
        self._keys.setdefault(scope, set()).add(event.key)
        if event.message_id:
            self._by_message.setdefault(scope, {}).setdefault(event.message_id, []).append(event)
        if dropped is not None:
            # 淘汰必须同时清索引，否则被淘汰的 key 还留在集合里、同一条记录再也写不回来
            self._drop(scope, dropped)
            self.stats["dropped_by_retention"] += 1

    def _drop(self, scope: str, event: LogEvent) -> None:
        keys = self._keys.get(scope)
        if keys is not None:
            keys.discard(event.key)
        if event.message_id:
            per_message = self._by_message.get(scope)
            if per_message and event.message_id in per_message:
                remaining = [e for e in per_message[event.message_id] if e is not event]
                if remaining:
                    per_message[event.message_id] = remaining
                else:
                    per_message.pop(event.message_id, None)

    def _enforce_retention(self, scope: str) -> None:
        """按时间窗口淘汰过期事件（容量淘汰在 ``_index`` 里完成）。"""
        self.prune(scope)

    # ==================== 落盘 ====================

    def _scope_dir(self, scope: str) -> Path | None:
        if self.data_dir is None:
            return None
        safe = "".join(ch if ch not in ':*?"<>|\\/' else "_" for ch in str(scope))
        return Path(self.data_dir) / (self.bot_id or "bot") / (safe or "default")

    @staticmethod
    def _file_tag(ts: Any = None) -> str:
        if ts is None:
            return date.today().isoformat()
        return date.fromtimestamp(int(ts)).isoformat()

    def _scope_files(self, scope: str, days: int = 2) -> list[Path]:
        directory = self._scope_dir(scope)
        if directory is None or not directory.is_dir():
            return []
        tags = {self._file_tag(time.time() - day * 86400) for day in range(max(1, days))}
        files = [directory / f"{tag}.jsonl" for tag in sorted(tags)]
        return [f for f in files if f.is_file()]

    async def _append_to_disk(self, scope: str, events: list[LogEvent]) -> int:
        directory = self._scope_dir(scope)
        if directory is None or not events:
            return 0
        lines: dict[Path, list[str]] = {}
        for event in events:
            path = directory / f"{self._file_tag(event.ts)}.jsonl"
            lines.setdefault(path, []).append(
                json.dumps(event.to_dict(), ensure_ascii=False, default=str)
            )

        def _write() -> int:
            directory.mkdir(parents=True, exist_ok=True)
            count = 0
            for path, rows in lines.items():
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write("\n".join(rows) + "\n")
                count += len(rows)
            return count

        try:
            return int(await asyncio.to_thread(_write))
        except Exception as e:  # noqa: BLE001 - 落盘失败不能影响记录流程
            self.log.warning(f"[GroupLog] 落盘失败（已保留在内存）: {e}")
            return 0

    async def _load_recent_files(self) -> None:
        if self.data_dir is None:
            return
        directory = Path(self.data_dir) / (self.bot_id or "bot")
        if not directory.is_dir():
            return
        loaded = 0
        for scope_dir in directory.iterdir():
            if not scope_dir.is_dir():
                continue
            scope = self._restore_scope(scope_dir.name)
            for path in sorted(scope_dir.glob("*.jsonl"))[-2:]:
                loaded += await self._load_file(scope, path)
        if loaded:
            self.stats["loaded"] += loaded
            self.log.debug(f"[GroupLog] 已恢复 {loaded} 条记录")

    @staticmethod
    def _restore_scope(dir_name: str) -> str:
        """目录名 → scope（``group_123`` → ``group:123``）。"""
        for prefix in ("group_", "private_"):
            if dir_name.startswith(prefix):
                return f"{prefix[:-1]}:{dir_name[len(prefix):]}"
        return dir_name

    async def _load_file(self, scope: str, path: Path) -> int:
        def _read() -> list[str]:
            try:
                with open(path, encoding="utf-8") as fh:
                    return fh.read().splitlines()
            except Exception as e:  # noqa: BLE001
                self.log.debug(f"[GroupLog] 读取 {path.name} 失败（忽略）: {e}")
                return []

        lines = await asyncio.to_thread(_read)
        count = 0
        for line in lines[-self.retention_count:]:
            line = line.strip()
            if not line:
                continue
            try:
                event = LogEvent.from_dict(json.loads(line))
            except Exception:  # noqa: BLE001 - 坏行跳过，不让一条脏数据卡住恢复
                continue
            if not event.scope:
                event.scope = scope
            keys = self._keys.setdefault(event.scope, set())
            if event.key in keys:
                continue
            self._index(event.scope, event)
            count += 1
        return count

    async def _maybe_compact(self, scope: str, factor: int = DEFAULT_COMPACT_FACTOR) -> None:
        """文件远大于保留量时重写压缩（原子替换）。"""
        directory = self._scope_dir(scope)
        if directory is None or self.retention_count <= 0:
            return
        # 还有未落盘的事件时绝不压缩：压缩是"用内存内容重写文件"，
        # 此刻内存里还没有那批 pending，重写会直接把它们抹掉。
        if self._pending.get(scope):
            return
        files = sorted(directory.glob("*.jsonl"))
        if not files:
            return
        current = files[-1]
        try:
            size = await asyncio.to_thread(lambda: current.stat().st_size)
        except OSError:
            return
        # 粗略以"平均每行 200 字节"估算行数，超过保留量 factor 倍才压缩
        if size < self.retention_count * 200 * factor:
            return

        def _rewrite() -> None:
            keep = [json.dumps(e.to_dict(), ensure_ascii=False, default=str)
                    for e in self._events.get(scope, [])]
            tmp = current.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write("\n".join(keep) + ("\n" if keep else ""))
            os.replace(tmp, current)

        try:
            await asyncio.to_thread(_rewrite)
            self.log.debug(f"[GroupLog] 已压缩 {scope} 记录文件")
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"[GroupLog] 压缩失败（保留原文件）: {e}")

    # ==================== 后台任务 ====================

    async def _write_loop(self) -> None:
        while not self._closing:
            try:
                await asyncio.sleep(self.flush_interval)
                total = sum(len(v) for v in self._pending.values())
                if total >= self.flush_batch or total > 0:
                    await self.flush()
                self.prune_all()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - 后台任务不能因单次异常退出
                self.log.warning(f"[GroupLog] 写循环异常（继续）: {e}")


# ==================== 运行时挂载点 ====================


def attach(runtime: Any, store: GroupLogStore) -> None:
    """把 store 挂到 runtime（装配层与工具层按 ``runtime.group_log`` 取用）。"""
    runtime.group_log = store


def detach(runtime: Any) -> None:
    """摘掉挂载点（模块卸载时调用）。"""
    if getattr(runtime, "group_log", None) is not None:
        try:
            delattr(runtime, "group_log")
        except AttributeError:
            pass


def store_of(runtime: Any) -> GroupLogStore | None:
    """取当前 runtime 的 store；未挂载（模块未装/未启用）时返回 None。

    调用方必须容忍 None（降级为空环境块，不阻断主流程）。
    """
    store = getattr(runtime, "group_log", None)
    return store if isinstance(store, GroupLogStore) else None
