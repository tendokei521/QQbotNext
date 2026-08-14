"""OneBot payload → 领域事件 编解码。

替代原 webserver/ws_msgprocess.py：
- 事件类型字符串与原 SUPPORTED_EVENTS 完全一致；
- 解析结果直接产出领域事件对象。
"""

from __future__ import annotations

from typing import Any

from app.domain.bot import IBot
from app.domain.events import (
    BaseEvent,
    GroupInfo,
    GroupMessageEvent,
    MetaEvent,
    MessageEvent,
    NoticeEvent,
    PrivateMessageEvent,
    RequestEvent,
    UserInfo,
)
from app.domain.message import MessageSegment

# 事件显示名（仅用于日志）
EVENT_NAMES: dict[str, str] = {
    "message_group": "群消息",
    "message_private": "私信消息",
    "request_group": "群申请",
    "request_private": "好友申请",
    "notice_poke": "戳一戳",
    "notice_group_emoji": "群表情",
    "notice_group_recall": "群撤回",
    "notice_private_recall": "私信撤回",
    "notice_group_increase": "入群通知",
    "notice_group_decrease": "退群通知",
    "bot_offline": "下线事件",
    "bot_heartbeat": "心跳",
    "bot_send_msg": "发送消息",
    "time_core": "定时调度",
    "unknown": "未知类型",
}


def _segments(raw_message: Any) -> list[MessageSegment]:
    if isinstance(raw_message, str):
        return [MessageSegment("text", {"text": raw_message})]
    if not isinstance(raw_message, list):
        return []
    return [
        MessageSegment(seg.get("type", ""), seg.get("data", {}) or {})
        for seg in raw_message
        if isinstance(seg, dict)
    ]


def _base(payload: dict, bot: IBot, event_type: str) -> dict:
    return dict(
        event_type=event_type,
        post_type=payload.get("post_type", ""),
        time=int(payload.get("time", 0)),
        user_id=int(payload.get("user_id", 0) or 0),
        self_id=int(payload.get("self_id", 0) or 0),
        bot=bot,
        bot_id=bot.bot_id if bot else None,
        bot_index=bot.index if bot else None,
        owner_id=bot.owner_id if bot else None,
        raw=payload,
    )


def _decode_message(payload: dict, bot: IBot) -> MessageEvent:
    sender = payload.get("sender", {}) or {}
    message_type = payload.get("message_type", "group")
    post_type = payload.get("post_type", "message")
    event_type = "bot_send_msg" if post_type == "message_sent" else (
        "message_group" if message_type == "group" else "message_private"
    )

    common = _base(payload, bot, event_type)
    common.update(
        message_type=message_type,
        sub_type=payload.get("sub_type", ""),
        message_id=int(payload.get("message_id", 0) or 0),
        raw_message=payload.get("raw_message", "") or "",
        message=_segments(payload.get("message")),
        user=UserInfo(
            user_id=int(payload.get("user_id", 0) or 0),
            nickname=sender.get("nickname", ""),
            card=sender.get("card", ""),
            role=sender.get("role", ""),
        ),
        group=GroupInfo(
            group_id=int(payload.get("group_id", 0) or 0),
            group_name=payload.get("group_name", "") or "",
            user_role=sender.get("role", ""),
        ),
    )

    if message_type == "group":
        return GroupMessageEvent(**common)
    return PrivateMessageEvent(**common)


def _decode_notice(payload: dict, bot: IBot) -> NoticeEvent:
    notice_type = payload.get("notice_type", "")
    sub_type = payload.get("sub_type", "")

    event_type_map = {
        "notify": "notice_poke",
        "group_msg_emoji_like": "notice_group_emoji",
        "group_recall": "notice_group_recall",
        "friend_recall": "notice_private_recall",
        "group_increase": "notice_group_increase",
        "group_decrease": "notice_group_decrease",
        "bot_offline": "bot_offline",
    }
    event_type = event_type_map.get(notice_type, f"notice_{notice_type}")

    common = _base(payload, bot, event_type)
    common.update(
        notice_type=notice_type,
        sub_type=sub_type,
        group_id=int(payload.get("group_id", 0) or 0),
        target_id=int(payload.get("target_id", 0) or 0),
        emoji_likes=payload.get("likes", []) or [],
        emoji_is_add=bool(payload.get("is_add", False)),
        operator_id=int(payload.get("operator_id", 0) or 0),
        message_id=int(payload.get("message_id", 0) or 0),
    )
    return NoticeEvent(**common)


def _decode_request(payload: dict, bot: IBot) -> RequestEvent:
    request_type = payload.get("request_type", "")
    event_type = "request_group" if request_type == "group" else "request_private"
    common = _base(payload, bot, event_type)
    common.update(
        request_type=request_type,
        sub_type=payload.get("sub_type", ""),
        group_id=int(payload.get("group_id", 0) or 0),
        comment=payload.get("comment", "") or "",
        flag=payload.get("flag", "") or "",
        operator_id=int(payload.get("operator_id", 0) or 0),
    )
    return RequestEvent(**common)


def _decode_meta(payload: dict, bot: IBot) -> MetaEvent:
    meta_type = payload.get("meta_event_type", "")
    common = _base(payload, bot, "bot_heartbeat")
    common.update(meta_event_type=meta_type)
    return MetaEvent(**common)


def decode(payload: dict, bot: IBot | None = None) -> BaseEvent:
    """解析 OneBot 上报 payload 为领域事件。"""
    post_type = payload.get("post_type", "")
    if post_type in ("message", "message_sent"):
        return _decode_message(payload, bot)
    if post_type == "notice":
        return _decode_notice(payload, bot)
    if post_type == "request":
        return _decode_request(payload, bot)
    if post_type == "meta_event":
        return _decode_meta(payload, bot)
    common = _base(payload, bot, "unknown")
    return BaseEvent(**common)


def event_name(event_type: str) -> str:
    return EVENT_NAMES.get(event_type, "未知类型")
