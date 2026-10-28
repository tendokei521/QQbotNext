"""诊断台事件历史（环形缓冲）回归测试。

产品目标：「连上了但 @ 没反应」先看协议端到底有没有把事件送进来。
要求：每账号最近 100 条滚动、摘要 ≤80 字（图片记 [图片]）、读操作不抛异常。
"""

from __future__ import annotations

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
    assert record["raw"] is raw


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


# ---------- 网关接线：_process_event 会留痕 ----------


async def test_gateway_process_event_records_history():
    """端到端：事件经 _process_event 后可从网关读到（诊断台 API 的数据源）。"""
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    conn = BotConnection()
    conn.account_id = "main"
    conn.account_name = "主号"
    gw.connections = {"main": conn}

    await gw._process_event(_event(_raw(text="诊断台能看到吗")), conn, _NullLogger(), _NullLogger())

    records = gw.recent_events("main", 10)
    assert len(records) == 1
    assert records[0]["summary"] == "诊断台能看到吗"
    assert gw.latest_group_id("main") == 999

    gw.clear_events("main")
    assert gw.recent_events("main", 10) == []
    assert gw.recent_events("ghost", 10) == []
