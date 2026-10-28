"""诊断台事件历史（环形缓冲）回归测试。

产品目标：「连上了但 @ 没反应」先看协议端到底有没有把事件送进来。
要求：每账号最近 100 条滚动、摘要 ≤80 字（图片记 [图片]）、读操作不抛异常。
"""

from __future__ import annotations

import asyncio
import json
import time

from app.domain.events import GroupMessageEvent
from app.infrastructure.cache import Cache
from app.infrastructure.onebot.client import BotConnection
from app.infrastructure.onebot.gateway import OneBotGateway
from app.infrastructure.onebot.gateway_events import HISTORY_MAXLEN, SUMMARY_MAXLEN, EventHistory


class _Settings:
    ws_ping_interval = 30
    ws_ping_timeout = 10
    ws_connect_timeout = 3


class _NullLogger:
    def warning(self, *_a, **_k):
        pass

    info = warning
    debug = warning
    error = warning
    exception = warning

    def prefix(self, *_a, **_k):
        return self

    def add_info(self, *_a, **_k):
        return self


def _raw(group_id: int = 999, user_id: int = 100, text: str = "你好", **extra):
    payload = {
        "post_type": "message", "message_type": "group",
        "group_id": group_id, "user_id": user_id, "self_id": 1,
        "time": 1_700_000_000,
        "message": [{"type": "text", "data": {"text": text}}],
    }
    payload.update(extra)
    return payload


def _event(raw: dict | None) -> GroupMessageEvent:
    return GroupMessageEvent(
        event_type="message_group", message_type="group", time=1_700_000_000,
        user_id=100, self_id=1, message=[],
        group=type("G", (), {"group_id": 999})(), raw=raw or {},
    )


# ---------- 环形缓冲本身 ----------


def test_history_rolls_and_caps_at_maxlen():
    """超过上限只保留最新 100 条，seq 单调递增。"""
    history = EventHistory()
    for index in range(HISTORY_MAXLEN + 5):
        history.record("main", _event(_raw(text=f"msg-{index}")))

    records = history.recent("main", 999)
    assert len(records) == HISTORY_MAXLEN
    assert records[0]["seq"] == 6, "应挤掉最旧的 5 条"
    assert records[-1]["seq"] == HISTORY_MAXLEN + 5
    assert records[-1]["summary"] == f"msg-{HISTORY_MAXLEN + 4}"


def test_history_record_fields_and_summary():
    """字段：seq/time/event_type/user_id/group_id/summary/raw；图片记 [图片]。"""
    history = EventHistory()
    raw = _raw(text="看这个")
    raw["message"] = [
        {"type": "text", "data": {"text": "看这个"}},
        {"type": "image", "data": {"file": "a.png"}},
        {"type": "at", "data": {"qq": "456"}},
    ]
    history.record("main", _event(raw))

    record = history.recent("main", 50)[0]
    assert record["seq"] == 1
    assert record["event_type"] == "message.group"
    assert record["time"] == 1_700_000_000
    assert (record["user_id"], record["group_id"]) == (100, 999)
    assert record["summary"] == "看这个[图片]@456"
    assert len(record["summary"]) <= SUMMARY_MAXLEN
    assert record["raw"] == raw


def test_history_deep_copies_raw_payload():
    """诊断历史必须持有 raw 的深拷贝：外部/派发链改写事件不影响已记录内容。"""
    history = EventHistory()
    raw = _raw(text="原始内容")
    event = _event(raw)
    history.record("main", event)

    # 事件对象与原始 payload 都被后续处理改写
    raw["message"] = [{"type": "text", "data": {"text": "被外部改了"}}]
    raw["post_type"] = "notice"
    event.raw["group_id"] = 123

    record = history.recent("main", 1)[0]
    assert record["raw"]["post_type"] == "message", "记录必须是深拷贝，不能随外部改动漂移"
    assert record["raw"]["group_id"] == 999
    assert record["raw"]["message"][0]["data"]["text"] == "原始内容"
    assert record["raw"] is not raw


def test_history_limits_summary_length():
    history = EventHistory()
    history.record("main", _event(_raw(text="长" * 300)))
    summary = history.recent("main", 1)[0]["summary"]
    assert len(summary) == SUMMARY_MAXLEN


def test_history_non_message_event():
    """通知/心跳等事件没有 message：event_type 用 post_type.子类型，摘要留空。"""
    history = EventHistory()
    history.record("main", _event({
        "post_type": "notice", "notice_type": "group_recall",
        "group_id": 999, "user_id": 100, "self_id": 1, "time": 1_700_000_001,
    }))
    record = history.recent("main", 1)[0]
    assert record["event_type"] == "notice.group_recall"
    assert record["summary"] == ""


def test_history_read_never_raises_on_unknown_account():
    history = EventHistory()
    assert history.recent("ghost", 50) == []
    assert history.latest_group_id("ghost") is None
    history.clear("ghost")  # 不抛异常


def test_history_latest_group_id():
    """取最近一条带 group_id 的群消息；纯私聊事件跳过。"""
    history = EventHistory()
    assert history.latest_group_id("main") is None
    history.record("main", _event(_raw(group_id=111)))
    history.record("main", _event(_raw(group_id=222)))
    history.record("main", _event({
        "post_type": "message", "message_type": "private", "user_id": 100,
        "self_id": 1, "time": 1_700_000_002, "message": [],
    }))
    assert history.latest_group_id("main") == 222


def test_history_clear_resets_sequence():
    history = EventHistory()
    history.record("main", _event(_raw()))
    history.clear("main")
    assert history.recent("main", 50) == []
    history.record("main", _event(_raw()))
    assert history.recent("main", 50)[0]["seq"] == 1, "清空后重新从 #1 计数"


# ---------- 网关接线：接收循环入队前就留痕（含被背压丢弃的事件） ----------


class _MessagesWs:
    """投递若干条群消息后保持连接（供网关接收循环测试）。"""

    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.sent: list = []

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self._texts:
            text = self._texts.pop(0)
            return json.dumps({
                "post_type": "message", "message_type": "group",
                "user_id": 100, "group_id": 999, "self_id": 1,
                "time": int(time.time()),
                "message": [{"type": "text", "data": {"text": text}}],
            })
        await asyncio.Event().wait()

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        pass


def _gateway() -> OneBotGateway:
    gw = OneBotGateway(settings=_Settings(), cache=Cache())

    async def _noop(conn):
        return False

    gw._get_login_info = _noop
    gw._get_login_groups = _noop
    return gw


async def _wait(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


async def test_gateway_receive_loop_records_history_before_dispatch():
    """端到端：事件一进接收循环就进诊断历史（不必等 worker/派发完成）。"""
    gw = _gateway()
    conn = BotConnection(websocket=_MessagesWs(["诊断台能看到吗"]))
    conn.account_id = "main"
    conn.account_name = "主号"
    gw.connections = {"main": conn}
    # 派发故意卡住：历史记录不能依赖派发完成
    blocked = asyncio.Event()

    async def handler(event):  # pragma: no cover —— 只验证记录不依赖它
        await blocked.wait()

    gw.dispatch_handler = handler
    task = asyncio.create_task(gw._bot_server("main"))
    try:
        assert await _wait(lambda: bool(gw.recent_events("main", 10))), "接收循环必须写入诊断历史"
        record = gw.recent_events("main", 10)[0]
        assert record["summary"] == "诊断台能看到吗"
        assert record["event_type"] == "message.group"
        assert gw.latest_group_id("main") == 999
        assert "dropped" not in record
    finally:
        blocked.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    gw.clear_events("main")
    assert gw.recent_events("main", 10) == []
    assert gw.event_queue_drops.get("main") == 0
    assert gw.recent_events("ghost", 10) == []


async def test_queue_drop_marks_event_dropped_and_counts(monkeypatch):
    """队列丢弃的事件也要留在诊断历史里，并带 dropped 标记（旧实现在 worker 记录 → 看不到）。"""
    from app.infrastructure.onebot import gateway_events as events_module

    monkeypatch.setattr(events_module, "EVENT_QUEUE_MAXSIZE", 1)
    gw = _gateway()
    conn = BotConnection(websocket=_MessagesWs(["m1", "m2", "m3"]))
    conn.account_id = "main"
    conn.account_name = "主号"
    gw.connections = {"main": conn}

    block = asyncio.Event()

    async def handler(event):
        await block.wait()  # 卡住 worker：接收循环继续收包 → 队列打满 → 丢最旧

    gw.dispatch_handler = handler
    task = asyncio.create_task(gw._bot_server("main"))
    try:
        assert await _wait(lambda: gw.event_queue_drops.get("main", 0) > 0), "应发生背压丢弃"
        records = gw.recent_events("main", 10)
        summaries = [record["summary"] for record in records]
        assert "m2" in summaries, "被丢弃的事件也必须先记入诊断历史"
        dropped = [record for record in records if record.get("dropped")]
        assert dropped, "被丢弃事件应带 dropped 标记"
        assert any(record["summary"] == "m2" for record in dropped) or any(
            record["summary"] == "m1" for record in dropped
        ), f"dropped 标记应落在被挤掉的事件上: {records}"
    finally:
        block.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
