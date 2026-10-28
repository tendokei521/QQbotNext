"""SQLite 访问层的锁与事务隔离：统计落库不能打断配置事务，关库后调用必须明确报错。

历史问题（`app/agent/usage/recorder.py`）：

- `flush()` 直接拿 `db._conn` 执行 `executemany` + `commit`，绕过了 `Database._lock`；
  与配置的 `run_in_transaction` 交叉时会提前提交（半写）或触发
  "cannot start a transaction within a transaction"。
- `Database.execute/fetch*` 在 `_conn is None`（已关闭）时抛 `AttributeError`，
  退出竞态里的日志毫无信息量。

修法：`Database` 提供受锁的 `executemany` 公开接口 + 连接守卫 + busy_timeout；
recorder 改走公开接口。
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from app.agent.usage.recorder import StatRecorder
from app.infrastructure.persistence.database import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "iso.db")
    await database.connect()
    yield database
    await database.close()


class _GatedConnection:
    """把 execute 的第 2 次调用卡在 gate 上，模拟「事务进行中」。

    只用真实连接做转发；不是完整 Connection 的替代品，仅本测试内使用。
    """

    def __init__(self, conn) -> None:
        self._conn = conn
        self.gate = asyncio.Event()
        self.calls = 0

    async def execute(self, sql, params=()):
        self.calls += 1
        if self.calls == 2:
            await self.gate.wait()
        return await self._conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


async def _wait_until(predicate, rounds: int = 100) -> None:
    for _ in range(rounds):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("等待条件超时")


async def test_executemany_writes_and_commits(db):
    await db.executemany(
        "INSERT OR REPLACE INTO kv (key, value_json) VALUES (?,?)",
        [("a", '"1"'), ("b", '"2"')],
    )

    rows = await db.fetchall("SELECT key, value_json FROM kv ORDER BY key")
    assert [(r["key"], r["value_json"]) for r in rows] == [("a", '"1"'), ("b", '"2"')]


async def test_run_in_transaction_rolls_back_every_statement(db):
    """最后一条 SQL 失败 → 前面已执行的语句全部回滚，连接还能继续用。"""
    await db.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY, v TEXT)")

    with pytest.raises(sqlite3.OperationalError):
        await db.run_in_transaction([
            ("INSERT INTO probe (id, v) VALUES (?,?)", (1, "a")),
            ("INSERT INTO no_such_table (id) VALUES (?)", (2,)),
        ])

    assert await db.fetchone("SELECT id FROM probe WHERE id = 1") is None
    # 回滚后连接没有卡在事务里：后续写入正常
    assert await db.execute("INSERT INTO probe (id, v) VALUES (?,?)", (3, "c")) == 1
    assert await db.fetchone("SELECT v FROM probe WHERE id = 3") == {"v": "c"}


async def test_recorder_flush_waits_for_active_transaction(db):
    """配置事务持锁期间，统计 flush 必须排队；事务完成后 flush 才落库。"""
    await db.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY, v TEXT)")
    recorder = StatRecorder(db, flush_max=1000, flush_interval=0)
    recorder.record(kind="llm", bot_id="1")

    real_conn = db._conn
    gated = _GatedConnection(real_conn)
    db._conn = gated
    try:
        tx = asyncio.create_task(db.run_in_transaction([
            ("INSERT INTO probe (id, v) VALUES (?,?)", (1, "a")),
            ("INSERT INTO probe (id, v) VALUES (?,?)", (2, "b")),
        ]))
        await _wait_until(lambda: gated.calls >= 2)

        flush = asyncio.create_task(recorder.flush())
        await asyncio.sleep(0.05)
        assert not flush.done(), "事务持锁期间 flush 必须等待（否则会打断事务/提前提交）"

        gated.gate.set()
        await asyncio.wait_for(tx, timeout=2)
        assert await asyncio.wait_for(flush, timeout=2) == 1
    finally:
        db._conn = real_conn

    probe = await db.fetchall("SELECT id FROM probe ORDER BY id")
    assert [r["id"] for r in probe] == [1, 2], "事务必须完整提交，不能被统计写打断"
    stat = await db.fetchone("SELECT COUNT(*) AS c FROM stat_event")
    assert stat["c"] == 1


async def test_recorder_flush_goes_through_database_lock(db):
    """flush 必须走 `Database.executemany`（受锁），不能再直接碰 `_conn`。"""
    calls: list[tuple[str, int]] = []
    original = db.executemany

    async def spy(sql: str, params_seq):
        calls.append((sql, len(params_seq)))
        return await original(sql, params_seq)

    db.executemany = spy  # type: ignore[method-assign]
    recorder = StatRecorder(db, flush_max=1000, flush_interval=0)
    recorder.record(kind="llm", bot_id="1")

    assert await recorder.flush() == 1
    assert len(calls) == 1 and calls[0][1] == 1


async def test_flush_after_close_keeps_rows_in_buffer(db):
    """库已关时 flush 不炸主流程，但也不静默丢数据（回填缓冲 + 计数）。"""
    recorder = StatRecorder(db, flush_max=1000, flush_interval=0)
    recorder.record(kind="llm", bot_id="1")
    await db.close()

    assert await recorder.flush() == 0
    assert recorder.failed == 1
    assert len(recorder._buf) == 1, "写失败的数据要回填缓冲，等库恢复后重试"


async def test_closed_database_raises_clear_error(db):
    await db.close()

    with pytest.raises(RuntimeError, match="未连接"):
        await db.execute("SELECT 1")
    with pytest.raises(RuntimeError, match="未连接"):
        await db.fetchone("SELECT 1")
    with pytest.raises(RuntimeError, match="未连接"):
        await db.fetchall("SELECT 1")
    with pytest.raises(RuntimeError, match="未连接"):
        await db.executemany("SELECT 1", [(1,)])
    with pytest.raises(RuntimeError, match="未连接"):
        await db.run_in_transaction([])

    await db.close()  # 幂等


async def test_connection_pragmas_for_concurrent_writers(db):
    """多写者排队 + WAL：busy_timeout 必须设上，否则并发写直接 "database is locked"。"""
    assert (await db.fetchone("PRAGMA busy_timeout"))["timeout"] == 5000
    assert str((await db.fetchone("PRAGMA journal_mode"))["journal_mode"]).lower() == "wal"
    assert (await db.fetchone("PRAGMA synchronous"))["synchronous"] == 1  # NORMAL
