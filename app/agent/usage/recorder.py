"""统计写入器：内存攒批 → 批量落库；提供清理与查询入口。

## 为什么攒批

LLM 记账发生在每轮对话里，逐条 `INSERT` 会：
- 每次都要占用一次 SQLite 写锁（与会话历史写入抢锁）
- 高频小事务拖慢流水线

所以按「**20 条** 或 **2 秒**」攒批，一次事务写多条；退出时强制 flush。

## 绝不用 fire-and-forget

历史教训：`_schedule_persist` 那种"排进队列就不管"的写法，
在数据库被关闭时会静默丢数据（`Cannot operate on a closed database`）。
这里 `close()` 一定 `await flush()`。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.core.logger import logger

#: 攒到多少条就落库
FLUSH_MAX_ROWS = 20
#: 距上次落库超过多少秒就落库
FLUSH_INTERVAL_SECONDS = 2.0

#: 可写入的列（顺序即 INSERT 的列顺序）
COLUMNS: tuple[str, ...] = (
    "ts",
    "day_local",
    "kind",
    "bot_id",
    "source",
    "api",
    "model",
    "session_id",
    "session_label",
    "group_id",
    "user_id",
    "event_type",
    "direction",
    "outcome",
    "requests",
    "input_tokens",
    "output_tokens",
    "cache_hit",
    "cache_miss",
    "tokens_total",
    "reasoning_tokens",
    "messages",
    "segments",
    "bytes",
    "ok",
    "estimated",
    "err_kind",
    "latency_ms",
)

_INSERT_SQL = (
    f"INSERT INTO stat_event ({', '.join(COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in COLUMNS)})"
)


def day_key(ts: float, tz_offset_seconds: int | None = None) -> str:
    """把 UTC 秒转成本地日 `YYYY-MM-DD`（默认用系统本地时区）。"""
    tm = time.localtime(ts) if tz_offset_seconds is None else time.gmtime(ts + tz_offset_seconds)
    return time.strftime("%Y-%m-%d", tm)


def _row(**kwargs: Any) -> dict:
    """补齐缺列（未提供的用列定义的默认值）。"""
    row = {
        "ts": 0,
        "day_local": "",
        "kind": "",
        "bot_id": "",
        "source": "",
        "api": "",
        "model": "",
        "session_id": "",
        "session_label": "",
        "group_id": "",
        "user_id": "",
        "event_type": "",
        "direction": "",
        "outcome": "",
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_hit": None,
        "cache_miss": None,
        "tokens_total": 0,
        "reasoning_tokens": 0,
        "messages": 0,
        "segments": 0,
        "bytes": 0,
        "ok": 1,
        "estimated": 0,
        "err_kind": "",
        "latency_ms": 0,
    }
    row.update(kwargs)
    ts = float(row["ts"] or time.time())
    row["ts"] = int(ts)
    if not row["day_local"]:
        row["day_local"] = day_key(ts)
    return {"k": row}


class StatRecorder:
    """统计记录器：攒批写入 + 清理。查询见 `app/agent/usage/query.py`。"""

    def __init__(
        self,
        db: Any,
        *,
        flush_max: int = FLUSH_MAX_ROWS,
        flush_interval: float = FLUSH_INTERVAL_SECONDS,
    ) -> None:
        self._db = db
        self.flush_max = max(1, int(flush_max))
        self.flush_interval = max(0.0, float(flush_interval))
        self._buf: list[dict] = []
        self._last_flush = time.monotonic()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._closed = False
        #: 落库计数（测试与诊断用）
        self.written = 0
        #: 丢弃计数（写失败时不清缓冲，只记数）
        self.failed = 0

    # ==================== 对外记账 API ====================

    def record(self, **fields: Any) -> None:
        """记一条（同步、不阻塞）。字段缺失按列默认值补齐。"""
        if self._closed:
            return
        row = _row(**fields)["k"]
        self._buf.append(row)
        self._maybe_kick()

    def record_llm(
        self,
        *,
        usage: dict | None,
        latency_ms: int = 0,
        ok: bool = True,
        err_kind: str = "",
        estimated: bool = False,
        **fields: Any,
    ) -> None:
        """记一次 LLM 请求：从上游 `usage` 里取 token / 缓存命中 / 思考 token。"""
        from app.agent.providers.base import cache_hit_miss, input_output_tokens

        prompt, completion = input_output_tokens(usage)
        hit, miss = cache_hit_miss(usage)
        reasoning = 0
        if isinstance(usage, dict):
            details = usage.get("completion_tokens_details")
            if isinstance(details, dict):
                try:
                    reasoning = int(details.get("reasoning_tokens") or 0)
                except (TypeError, ValueError):
                    reasoning = 0
        total = None
        if isinstance(usage, dict):
            try:
                total = int(usage.get("total_tokens") or 0) or None
            except (TypeError, ValueError):
                total = None
        if total is None:
            total = int(prompt or 0) + int(completion or 0)
        self.record(
            kind="llm",
            requests=1,
            input_tokens=int(prompt or 0),
            output_tokens=int(completion or 0),
            cache_hit=hit,
            cache_miss=miss,
            tokens_total=total,
            reasoning_tokens=reasoning,
            ok=1 if ok else 0,
            estimated=1 if (estimated or prompt is None) else 0,
            err_kind=err_kind,
            latency_ms=int(latency_ms or 0),
            **fields,
        )

    def record_msg_in(self, *, outcome: str, **fields: Any) -> None:
        """记一条入站消息。`outcome` ∈ ignored / command / module / llm。"""
        self.record(
            kind="msg_in",
            direction="in",
            outcome=str(outcome or "ignored"),
            messages=1,
            **fields,
        )

    def record_msg_out(self, *, segments: int = 1, nbytes: int = 0, **fields: Any) -> None:
        """记一条出站消息。"""
        self.record(
            kind="msg_out",
            direction="out",
            messages=1,
            segments=max(0, int(segments or 0)),
            bytes=max(0, int(nbytes or 0)),
            **fields,
        )

    # ==================== 落库 ====================

    def _maybe_kick(self) -> None:
        """够了就落库（有事件循环才异步落，没有等显式 flush）。"""
        due = len(self._buf) >= self.flush_max or (
            self.flush_interval and (time.monotonic() - self._last_flush) >= self.flush_interval
        )
        if not due:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._task is None or self._task.done():
            self._task = loop.create_task(self.flush())

    async def flush(self) -> int:
        """把缓冲写进数据库，返回写入条数。"""
        async with self._lock:
            if not self._buf:
                self._last_flush = time.monotonic()
                return 0
            batch, self._buf = self._buf, []
            placeholders = [tuple(row[c] for c in COLUMNS) for row in batch]
            try:
                conn = getattr(self._db, "_conn", None)
                if conn is None:
                    raise RuntimeError("数据库未连接")
                await conn.executemany(_INSERT_SQL, placeholders)
                await conn.commit()
            except Exception as e:  # noqa: BLE001 —— 统计写失败不该影响主流程
                self.failed += len(batch)
                # 放回缓冲（保留数据，下次再试），但设上限防无限增长
                self._buf = (batch + self._buf)[-500:]
                logger.debug(f"[Stats] 批量写入失败（已回填缓冲）: {e}")
                return 0
            self.written += len(batch)
            self._last_flush = time.monotonic()
            return len(batch)

    async def start(self, task_manager: Any = None) -> None:
        """启动后台定期落库（可选；没有它也会在够 20 条时落）。"""

        async def _ticker() -> None:
            while not self._closed:
                await asyncio.sleep(self.flush_interval or 1.0)
                await self.flush()

        if self._task is not None and not self._task.done():
            return
        if task_manager is not None:
            self._task = task_manager.create_task(_ticker(), name="stats:flush", owner="framework")
        else:
            self._task = asyncio.get_running_loop().create_task(_ticker())

    async def close(self) -> None:
        """停止后台任务并**强制落库**（退出路径必须调用）。"""
        self._closed = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._task = None
        await self.flush()
        # 关库前再试一次（若 flush 因关闭失败，这里已回填，但已无意义）
        if self._buf:
            logger.debug(f"[Stats] 关闭时仍有 {len(self._buf)} 条未落库（已丢弃）")
            self._buf.clear()

    # ==================== 清理 ====================

    async def purge(
        self,
        *,
        before_ts: int | None = None,
        bot_id: str | None = None,
        batch: int = 1000,
    ) -> int:
        """分批删除历史数据（默认永久保留，由用户手动清）。"""
        conn = getattr(self._db, "_conn", None)
        if conn is None:
            return 0
        where = []
        params: list[Any] = []
        if before_ts is not None:
            where.append("ts < ?")
            params.append(int(before_ts))
        if bot_id:
            where.append("bot_id = ?")
            params.append(str(bot_id))
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        removed = 0
        while True:
            cur = await conn.execute(
                f"DELETE FROM stat_event WHERE id IN "
                f"(SELECT id FROM stat_event{clause} LIMIT {int(batch)})",
                tuple(params),
            )
            await conn.commit()
            count = cur.rowcount or 0
            removed += count
            if count < batch:
                break
        return removed


# ==================== 进程级单例 ====================

_recorder: StatRecorder | None = None


def get_recorder() -> StatRecorder | None:
    """取全局记录器；未初始化（纯单元测试）时返回 None。"""
    return _recorder


def set_recorder(recorder: StatRecorder | None) -> None:
    """装配/替换全局记录器（bootstrap 与测试用）。"""
    global _recorder
    _recorder = recorder


__all__ = [
    "COLUMNS",
    "FLUSH_INTERVAL_SECONDS",
    "FLUSH_MAX_ROWS",
    "StatRecorder",
    "day_key",
    "get_recorder",
    "set_recorder",
]
