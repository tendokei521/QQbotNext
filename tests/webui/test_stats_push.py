"""推送链路的回归测试。

链路：`StatRecorder.flush()` → `on_written(count)` → `notify_stats_updated()` → WS 广播
前端订阅 `stats_updated` 后重查（前端行为由构建产物覆盖，这里测后端）。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.agent.usage.recorder import StatRecorder, set_recorder
from app.infrastructure.persistence.database import Database
from app.webui import stats_notify


@pytest.fixture
async def env(tmp_path):
    db = Database(tmp_path / "push.db")
    await db.connect()
    rec = StatRecorder(db, flush_max=1000, flush_interval=0)
    set_recorder(rec)
    yield rec, db
    await rec.close()
    set_recorder(None)
    await db.close()


# ==================== 记录器回调 ====================


async def test_flush_calls_on_written_with_batch_size(env):
    rec, _db = env
    seen: list[int] = []
    rec.on_written = seen.append

    rec.record(kind="llm", bot_id="1")
    rec.record(kind="llm", bot_id="1")
    await rec.flush()

    assert seen == [2], "落库后应以本批条数回调一次"


async def test_on_written_failure_does_not_break_flush(env):
    rec, db = env

    def _boom(_n: int) -> None:
        raise RuntimeError("notify down")

    rec.on_written = _boom
    rec.record(kind="llm", bot_id="1")
    written = await rec.flush()

    assert written == 1, "回调失败不该影响落库"
    rows = await db.fetchall("SELECT COUNT(*) AS c FROM stat_event")
    assert rows[0]["c"] == 1


# ==================== 推送函数 ====================


async def test_notify_broadcasts_stats_updated(monkeypatch):
    """`notify_stats_updated` 应把 `{"type": "stats_updated"}` 广播出去。"""
    sent: list[str] = []

    class _Manager:
        active_connections = [object()]

        async def broadcast(self, message: str) -> None:
            sent.append(message)

    import app.webui.ws as ws_mod

    monkeypatch.setattr(ws_mod, "manager", _Manager())
    stats_notify.reset_throttle()

    stats_notify.notify_stats_updated(3)
    await asyncio.sleep(0.05)   # 让 create_task 跑完

    assert sent, "应广播一条消息"
    payload = json.loads(sent[0])
    assert payload["type"] == "stats_updated"
    assert payload["count"] == 3


async def test_notify_is_throttled(monkeypatch):
    """1 秒时间窗内多次落库只推一次（避免刷屏）。"""
    sent: list[str] = []

    class _Manager:
        active_connections = [object()]

        async def broadcast(self, message: str) -> None:
            sent.append(message)

    import app.webui.ws as ws_mod

    monkeypatch.setattr(ws_mod, "manager", _Manager())
    stats_notify.reset_throttle()

    for _ in range(5):
        stats_notify.notify_stats_updated(1)
    await asyncio.sleep(0.05)

    assert len(sent) == 1, f"应节流成 1 条，实际 {len(sent)}"


async def test_notify_without_loop_is_safe(monkeypatch):
    """没有事件循环（纯同步场景）不该抛。"""
    stats_notify.reset_throttle()
    stats_notify.notify_stats_updated(1)  # 不该抛异常
