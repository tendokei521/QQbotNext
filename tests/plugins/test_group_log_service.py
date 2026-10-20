"""`group_log` 插件的事件翻译层（`service.py`）单测：纯函数，不需要模块/容器/存储。"""

from __future__ import annotations

import types

from app.agent.group_log.events import KIND_EMOJI, KIND_MESSAGE, KIND_POKE
from app.plugins.group_log import service


def _group_event(**over) -> types.SimpleNamespace:
    base = dict(
        time=1700000000,
        message_id="9001",
        user_id="20002",
        self_id="778",
        group=types.SimpleNamespace(group_id=466052056),
        message=[{"type": "text", "data": {"text": "在吗"}}],
        user=types.SimpleNamespace(card="", nickname="小明"),
    )
    base.update(over)
    return types.SimpleNamespace(**base)


def test_strip_directives_removes_control_forms() -> None:
    assert service.strip_directives("[reply]<type=1>正常聊天") == "正常聊天"
    assert service.strip_directives("") == ""


def test_group_message_event_maps_fields() -> None:
    events = service.group_message_event(_group_event())
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == KIND_MESSAGE
    assert ev.scope == "group:466052056"
    assert ev.message_id == "9001"
    assert ev.by_me is False


def test_group_message_by_me_when_self_id_matches() -> None:
    ev = service.group_message_event(_group_event(user_id="778"))[0]
    assert ev.by_me is True


def test_directive_stripping_can_be_disabled() -> None:
    event = _group_event(message=[{"type": "text", "data": {"text": "[reply]在吗"}}])
    assert "reply" not in service.group_message_event(event, strip=True)[0].text
    assert "reply" in service.group_message_event(event, strip=False)[0].text


def test_emoji_event_without_details_still_records_one() -> None:
    event = _group_event(group_id="466052056", emoji_likes=None, emoji_is_add=True)
    events = service.emoji_event(event)
    assert len(events) == 1 and events[0].kind == KIND_EMOJI
    assert events[0].payload["emoji_id"] == ""


def test_poke_event_records_private_poke() -> None:
    event = _group_event(group=None, group_id="", operator_id="20002", target_id="778")
    events = service.poke_event(event)
    assert len(events) == 1 and events[0].kind == KIND_POKE
    assert events[0].payload["target_id"] == "778"
    assert "20002" in events[0].scope


def test_my_send_event_strips_and_marks_self() -> None:
    ctx = types.SimpleNamespace(
        group_id="466052056",
        message_id="7777",
        params={"message": "[reply]好的"},
    )
    events = service.my_send_event(ctx, bot_id=778)
    assert len(events) == 1
    assert events[0].by_me is True
    assert events[0].text == "好的"
