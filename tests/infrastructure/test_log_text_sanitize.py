"""收到的消息渲染成日志行时，名字/群名必须单行、无控制字符。

线上真实案例：某个群名片里带 ``\\x07``(BEL) 与换行，机器人日志原样打印，
于是每收到该用户一条消息，终端就"叮"一声，而日志里只是一行空白——
完全查不到是谁干的。这里锁住两件事：名字压成一行、控制字符转义。
"""

from __future__ import annotations

from app.domain.events import GroupMessageEvent, MessageSegment, UserInfo
from app.infrastructure.onebot.log_text import received_log_lines


class _Conn:
    label = "才羽 ミドリ"


def _event(*, card: str = "", nickname: str = "昵称") -> GroupMessageEvent:
    return GroupMessageEvent(
        event_type="message_group",
        message_type="group",
        time=1788342260,
        user_id=42,
        self_id=10001,
        message=[MessageSegment("text", {"text": "你好"})],
        user=UserInfo(user_id=42, nickname=nickname, card=card),
        group=type("G", (), {"group_id": 100})(),
    )


def _lines(card: str = "", nickname: str = "昵称"):
    return received_log_lines(_Conn(), _event(card=card, nickname=nickname))


def test_bel_in_nickname_is_escaped():
    lines = _lines(card="坏\x07人")

    assert lines, "这条消息应该照常打日志"
    prefix, body = lines[0]
    text = prefix + body
    assert "\x07" not in text
    assert "\\x07" in text


def test_newline_in_nickname_is_flattened():
    lines = _lines(card="第一行\n第二行")

    assert lines
    prefix, body = lines[0]
    assert "\n" not in prefix and "\n" not in body
    assert "第一行 第二行" in (prefix + body)


def test_normal_nickname_still_readable():
    lines = _lines(card="普通群名片")

    prefix, body = lines[0]
    assert "普通群名片" in prefix + body
