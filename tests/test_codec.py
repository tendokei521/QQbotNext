"""领域层与 OneBot 编解码测试。"""

from app.domain.events import GroupMessageEvent, MessageEvent, NoticeEvent, PrivateMessageEvent, RequestEvent
from app.domain.message import Message, MessageSegment
from app.infrastructure.onebot.codec import decode


class FakeBot:
    bot_id = 123
    index = 0
    owner_id = None


def _base_payload(**kw):
    payload = {"time": 1234567890, "self_id": 123, "user_id": 456}
    payload.update(kw)
    return payload


def test_message_segment_to_dict():
    seg = MessageSegment.text("你好")
    assert seg.to_dict() == {"type": "text", "data": {"text": "你好"}}


def test_message_text_and_onebot():
    msg = Message([MessageSegment.text("a"), MessageSegment.at(456)])
    assert msg.text == "a"
    assert msg.to_onebot() == [
        {"type": "text", "data": {"text": "a"}},
        {"type": "at", "data": {"qq": "456"}},
    ]


def test_decode_group_message():
    payload = _base_payload(
        post_type="message",
        message_type="group",
        group_id=999,
        message_id=1,
        raw_message="你好 @123",
        message=[{"type": "text", "data": {"text": "你好 "}}, {"type": "at", "data": {"qq": "123"}}],
        sender={"user_id": 456, "nickname": "小明", "card": "", "role": "admin"},
    )
    event = decode(payload, FakeBot())
    assert isinstance(event, GroupMessageEvent)
    assert event.event_type == "message_group"
    assert event.group.group_id == 999
    assert event.user_id == 456
    assert event.user.nickname == "小明"
    assert event.text == "你好 "
    assert event.is_at_me() is True
    assert len(event.message) == 2
    assert event.message[0].type == "text"


def test_decode_private_message():
    payload = _base_payload(post_type="message", message_type="private", message="hi", message_id=2)
    event = decode(payload, FakeBot())
    assert isinstance(event, PrivateMessageEvent)
    assert event.event_type == "message_private"


def test_decode_poke_notice():
    payload = _base_payload(
        post_type="notice", notice_type="notify", sub_type="poke",
        group_id=999, target_id=123, user_id=456,
    )
    event = decode(payload, FakeBot())
    assert isinstance(event, NoticeEvent)
    assert event.event_type == "notice_poke"
    assert event.target_id == 123
    assert event.group_id == 999


def test_decode_recall_notice():
    payload = _base_payload(
        post_type="notice", notice_type="group_recall",
        group_id=999, operator_id=456, user_id=789, message_id=42,
    )
    event = decode(payload, FakeBot())
    assert event.event_type == "notice_group_recall"
    assert event.operator_id == 456
    assert event.message_id == 42


def test_decode_request():
    payload = _base_payload(
        post_type="request", request_type="group", group_id=999,
        comment="申请", flag="abc", user_id=456,
    )
    event = decode(payload, FakeBot())
    assert isinstance(event, RequestEvent)
    assert event.event_type == "request_group"
    assert event.flag == "abc"


def test_event_reply_targets_bot():
    """reply() 应把目标转发到对应 IBot 发送方法。"""
    sent = {}

    class Bot:
        bot_id = 123
        index = 0
        owner_id = None

        async def send_group_msg(self, group_id, message, auto_escape=False):
            sent["group"] = (group_id, message)
            return {}

        async def send_private_msg(self, user_id, message, auto_escape=False):
            sent["private"] = (user_id, message)
            return {}

    ev = decode(
        _base_payload(post_type="message", message_type="group", group_id=999, message="x", message_id=1),
        Bot(),
    )
    import asyncio

    async def run():
        await ev.reply("hello")

    asyncio.run(run())
    assert sent["group"][0] == 999
    assert sent["group"][1].text == "hello"
