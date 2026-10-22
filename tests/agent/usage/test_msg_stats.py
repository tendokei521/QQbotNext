"""S3/S4 测试：入站 `outcome` 四分类 + 出站记账。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.usage.recorder import StatRecorder, get_recorder, set_recorder
from app.infrastructure.persistence.database import Database


@pytest.fixture
async def recorder(tmp_path):
    db = Database(tmp_path / "s34.db")
    await db.connect()
    rec = StatRecorder(db, flush_max=1000, flush_interval=0)
    set_recorder(rec)
    yield rec, db
    await rec.close()
    set_recorder(None)
    await db.close()


# ==================== 出站（send_item） ====================


class _Bot:
    bot_id = "99"

    def __init__(self, fail=False):
        self.sent: list = []
        self.fail = fail

    async def send_group_msg(self, group_id, message):
        if self.fail:
            raise RuntimeError("network down")
        self.sent.append(("group", group_id, str(message)))
        return {"status": "ok", "data": {"message_id": 1}}

    async def send_private_msg(self, user_id, message):
        if self.fail:
            raise RuntimeError("network down")
        self.sent.append(("private", user_id, str(message)))
        return {"status": "ok", "data": {"message_id": 2}}


@pytest.mark.asyncio
async def test_send_item_records_msg_out(recorder):
    rec, db = recorder
    from app.agent.delivery.channel import send_item

    runtime = SimpleNamespace(bot_id="99")
    await send_item(_Bot(), "group_466052056", "你好", runtime=runtime, hooks=False)
    await rec.flush()

    rows = await db.fetchall("SELECT * FROM stat_event WHERE kind='msg_out'")
    assert len(rows) == 1
    row = rows[0]
    assert row["direction"] == "out"
    assert row["messages"] == 1
    assert row["segments"] >= 1
    assert row["bytes"] == len("你好".encode())
    assert row["session_id"] == "group_466052056"
    assert row["session_label"] == "group466052056"
    assert row["source"] == "delivery"
    assert row["ok"] == 1


@pytest.mark.asyncio
async def test_send_failure_records_msg_out_with_ok_zero(recorder):
    rec, db = recorder
    from app.agent.delivery.channel import send_item

    runtime = SimpleNamespace(bot_id="99")
    result = await send_item(_Bot(fail=True), "private_1", "hi", runtime=runtime, hooks=False)
    assert result is None, "发送失败仍应返回 None"
    await rec.flush()
    rows = await db.fetchall("SELECT * FROM stat_event WHERE kind='msg_out'")
    assert len(rows) == 1
    assert rows[0]["ok"] == 0
    assert rows[0]["err_kind"] == "RuntimeError"


@pytest.mark.asyncio
async def test_send_item_works_without_recorder():
    """没装记录器时发送照常（统计是旁路）。"""
    set_recorder(None)
    from app.agent.delivery.channel import send_item

    bot = _Bot()
    resp = await send_item(bot, "group_1", "hi", runtime=SimpleNamespace(bot_id="1"), hooks=False)
    assert resp is not None and bot.sent


# ==================== 入站（outcome 四分类） ====================


def _event(*, text="你好", private=False, group_id="466052056", user_id="1901691195",
           stopped=False, llm_stop=False, job=None):
    from app.domain.events import GroupMessageEvent, MessageSegment, PrivateMessageEvent, UserInfo

    if private:
        ev = PrivateMessageEvent(
            event_type="message_private",
            message_type="private",
            time=1792670176,
            user_id=int(user_id),
            self_id=99,
            message=[MessageSegment("text", {"text": text})],
            user=UserInfo(user_id=int(user_id), nickname="小明同学真好看"),
        )
    else:
        ev = GroupMessageEvent(
            event_type="message_group",
            message_type="group",
            time=1792670176,
            user_id=int(user_id),
            self_id=99,
            message=[MessageSegment("text", {"text": text})],
            user=UserInfo(user_id=int(user_id), nickname="小明同学真好看"),
            group=SimpleNamespace(group_id=int(group_id), group_name="测试群名称很长"),
        )
    ev._stopped = stopped
    ev._llm_stop = llm_stop
    ev._llm_job = job
    return ev


def _node():
    from app.plugin.nodes.inbound import AgentNode

    return AgentNode(agent_manager=None, config_service=None, gateway=None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"text": "#llm help"}, "command"),
        ({"text": "你好", "job": object()}, "llm"),
        ({"text": "你好", "stopped": True}, "module"),
        ({"text": "你好", "llm_stop": True}, "module"),
        ({"text": "随便聊聊"}, "ignored"),
    ],
)
async def test_inbound_outcome_classification(recorder, kwargs, expected):
    rec, db = recorder
    node = _node()
    ctx = SimpleNamespace(event=_event(**kwargs))
    node._record_inbound(ctx)
    await rec.flush()

    rows = await db.fetchall("SELECT * FROM stat_event WHERE kind='msg_in'")
    assert len(rows) == 1, "一次入站消息只记一条"
    assert rows[0]["outcome"] == expected


@pytest.mark.asyncio
async def test_inbound_records_labels_and_ids(recorder):
    rec, db = recorder
    node = _node()
    node._record_inbound(SimpleNamespace(event=_event(text="你好")))
    await rec.flush()

    row = (await db.fetchall("SELECT * FROM stat_event WHERE kind='msg_in'"))[0]
    assert row["session_id"] == "group_466052056"
    # 群名「测试群名称很长」→ 前 5 字 + …
    assert row["session_label"] == "测试群名称…group466052056"
    assert row["group_id"] == "466052056"
    assert row["direction"] == "in"
    assert row["event_type"] == "message_group"


@pytest.mark.asyncio
async def test_inbound_private_label(recorder):
    rec, db = recorder
    node = _node()
    node._record_inbound(SimpleNamespace(event=_event(private=True)))
    await rec.flush()

    row = (await db.fetchall("SELECT * FROM stat_event WHERE kind='msg_in'"))[0]
    assert row["session_id"] == "private_1901691195"
    assert row["session_label"] == "小明同学真…private1901691195"


@pytest.mark.asyncio
async def test_inbound_ignores_non_message_events(recorder):
    rec, db = recorder
    node = _node()
    node._record_inbound(SimpleNamespace(event=SimpleNamespace(event_type="notice")))
    await rec.flush()
    rows = await db.fetchall("SELECT COUNT(*) AS c FROM stat_event")
    assert rows[0]["c"] == 0, "通知类事件不记 msg_in"


@pytest.mark.asyncio
async def test_inbound_without_recorder_is_safe():
    set_recorder(None)
    node = _node()
    node._record_inbound(SimpleNamespace(event=_event()))  # 不该抛


def test_recorder_available_for_inbound():
    """冒烟：inbound 记账用的是全局记录器入口。"""
    assert callable(get_recorder)
