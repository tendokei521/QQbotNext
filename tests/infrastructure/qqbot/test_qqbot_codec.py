"""官方事件解码契约：字符串 openid、@ 段、附件、引用、时间。"""

from __future__ import annotations

from app.domain.events import GroupMessageEvent, PrivateMessageEvent
from app.infrastructure.qqbot.codec import decode_event, dedupe_key, message_target


class FakeBot:
    protocol = "qqbot"
    account_id = "bot1"
    bot_id = "BOT_OPENID"


def _group_payload(**over):
    data = {
        "id": "ROBOT1.0_msg1",
        "author": {"member_openid": "MEM_A", "username": "小明", "member_role": "admin"},
        "content": " 你好 <@MEM_B> 在吗",
        "group_openid": "GROUP_A",
        "timestamp": "2026-07-21T10:00:00+08:00",
        "message_type": 0,
    }
    data.update(over)
    return data


def test_group_at_event_maps_string_ids_and_mentions():
    event = decode_event("GROUP_AT_MESSAGE_CREATE", _group_payload(), FakeBot())
    assert isinstance(event, GroupMessageEvent)
    assert event.event_type == "message_group"
    assert event.group.group_id == "GROUP_A"
    assert event.user_id == "MEM_A"
    assert event.user.nickname == "小明"
    assert event.user.role == "admin"
    assert event.message_id == "ROBOT1.0_msg1"
    assert event.time > 0
    assert event.text.startswith(" 你好 ")
    at = [str(seg.data.get("qq")) for seg in event.message if seg.type == "at"]
    assert "MEM_B" in at
    # @ 事件 content 已去掉 @ 前缀，必须补一个 at(self_id) 才能让「仅 @ 我」触发
    assert "BOT_OPENID" in at
    assert event.is_at_me() is True


def test_full_group_message_maps_is_you_mention_to_self():
    """全量群消息：content 保留 <@bot_member_openid>，mentions.is_you=True。"""
    data = _group_payload(
        content="<@BOT_MEMBER> 423",
        mentions=[{"bot": True, "member_openid": "BOT_MEMBER", "is_you": True}],
    )
    event = decode_event("GROUP_MESSAGE_CREATE", data, FakeBot())
    assert event.text == " 423"
    at = [str(seg.data.get("qq")) for seg in event.message if seg.type == "at"]
    assert at == ["BOT_OPENID"]
    assert event.is_at_me() is True


def test_c2c_event_is_private_without_group():
    data = {
        "id": "M1",
        "author": {"user_openid": "USER_A", "username": ""},
        "content": "hi",
        "timestamp": "2026-07-21T10:00:00+08:00",
    }
    event = decode_event("C2C_MESSAGE_CREATE", data, FakeBot())
    assert isinstance(event, PrivateMessageEvent)
    assert event.group is None
    assert event.user_id == "USER_A"


def test_attachments_become_image_segments():
    data = _group_payload(attachments=[{
        "content_type": "image/png",
        "url": "https://example.com/a.png",
        "width": 10,
        "height": 20,
    }])
    event = decode_event("GROUP_AT_MESSAGE_CREATE", data, FakeBot())
    images = [seg for seg in event.message if seg.type == "image"]
    assert images and images[0].data["file"] == "https://example.com/a.png"


def test_quoted_message_becomes_readable_prefix():
    data = _group_payload(
        message_type=103,
        content="这个不错",
        msg_elements=[{"message_type": 0, "content": "原消息内容"}],
    )
    event = decode_event("GROUP_AT_MESSAGE_CREATE", data, FakeBot())
    texts = [str(seg.data.get("text") or "") for seg in event.message if seg.type == "text"]
    assert any("[引用]原消息内容" in text for text in texts)


def test_unknown_event_returns_none_or_notice():
    assert decode_event("GROUP_ADD_ROBOT", {"group_openid": "G"}, FakeBot()) is None
    notice = decode_event("FRIEND_ADD", {"openid": "U", "timestamp": "2026-07-21T10:00:00+08:00"}, FakeBot())
    assert notice is not None


def test_dedupe_key_prefers_message_id():
    assert dedupe_key("GROUP_AT_MESSAGE_CREATE", {"id": "M1"}, "EV1") == "GROUP_AT_MESSAGE_CREATE:M1"
    assert dedupe_key("GROUP_AT_MESSAGE_CREATE", {}, "EV1") == "GROUP_AT_MESSAGE_CREATE:EV1"


def test_message_target_group_and_private():
    assert message_target({"group_openid": "G"}) == ("group", "G")
    assert message_target({"author": {"user_openid": "U"}}) == ("private", "U")
