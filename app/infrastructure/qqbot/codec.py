"""官方事件 → 领域事件。

官方事件是 JSON 对象（content 字符串 + attachments + mentions + msg_elements），
不是 OneBot 的消息段数组；ID 是字符串 openid，不是 QQ 号。这里做映射：

| 官方 | 领域 |
|---|---|
| ``GROUP_AT_MESSAGE_CREATE`` / ``GROUP_MESSAGE_CREATE`` | ``GroupMessageEvent`` |
| ``C2C_MESSAGE_CREATE`` | ``PrivateMessageEvent`` |
| 其余（好友/入群/审核…） | ``NoticeEvent``（未识别的类型统一兜底） |

@ 在 content 里是 ``<@member_openid>`` / ``<@all>``，这里转成 ``at`` 段；
附件转成 ``image/record/video/file`` 段（``file`` 用附件直链，图片流水线支持 http 直链）。
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

from app.domain.events import (
    BaseEvent,
    GroupInfo,
    GroupMessageEvent,
    NoticeEvent,
    PrivateMessageEvent,
    UserInfo,
)
from app.domain.message import MessageSegment

#: ``<@E4F4...>`` / ``<@!E4F4...>`` / ``<@all>``
_MENTION_RE = re.compile(r"<@!?([A-Za-z0-9_\-]+)>")
_IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp")


def parse_time(value: Any) -> int:
    """RFC3339（带时区）→ Unix 秒；已经是数字（``GROUP_ADD_ROBOT``）就用数字。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return int(time.time())
    if text.isdigit():
        return int(text)
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return int(time.time())


def _content_segments(content: Any) -> list[MessageSegment]:
    """把官方 content 拆成 text/at 段（@ 语法转成 at 段，正文不含标签）。"""
    text = str(content or "")
    segments: list[MessageSegment] = []
    cursor = 0
    for match in _MENTION_RE.finditer(text):
        if match.start() > cursor:
            segments.append(MessageSegment.text(text[cursor:match.start()]))
        target = match.group(1)
        if target.lower() == "all":
            segments.append(MessageSegment("at", {"qq": "all"}))
        else:
            segments.append(MessageSegment("at", {"qq": target}))
        cursor = match.end()
    if cursor < len(text) or not segments:
        segments.append(MessageSegment.text(text[cursor:]))
    return segments


def _attachment_segments(attachments: Any) -> list[MessageSegment]:
    segments: list[MessageSegment] = []
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        content_type = str(item.get("content_type") or "").lower()
        if not url:
            continue
        if content_type in _IMAGE_TYPES:
            segments.append(MessageSegment("image", {
                "file": url,
                "url": url,
                "width": item.get("width", 0),
                "height": item.get("height", 0),
            }))
        elif content_type == "voice":
            segments.append(MessageSegment("record", {"file": url, "url": url}))
        elif content_type.startswith("video/"):
            segments.append(MessageSegment("video", {"file": url, "url": url}))
        else:
            segments.append(MessageSegment("file", {"file": url, "url": url, "name": item.get("filename", "")}))
    return segments


def _quoted_text(data: dict) -> str:
    """引用消息（message_type=103）：把被引用内容拼成一段可读前缀。

    官方没有给出「被引用消息的 msg_id」，只有 msg_elements 里的内容，无法走
    框架的 reply 段反查；退化成文本前缀，保证模型/历史能看到上下文。
    """
    if int(data.get("message_type") or 0) != 103:
        return ""
    parts: list[str] = []
    for element in data.get("msg_elements") or []:
        if not isinstance(element, dict):
            continue
        content = str(element.get("content") or "").strip()
        if content:
            parts.append(content)
    if not parts:
        return ""
    text = "\n".join(parts)
    return f"[引用]{text}\n"


def _base_fields(data: dict, bot: Any, event_type: str, message_type: str) -> dict:
    author = data.get("author") or {}
    user_id = str(author.get("member_openid") or author.get("user_openid") or author.get("id") or "")
    role_raw = str(author.get("member_role") or "").strip().lower()
    role = {"owner": "owner", "admin": "admin"}.get(role_raw, "member")
    self_id = str(getattr(bot, "bot_id", "") or "")
    return dict(
        event_type=event_type,
        post_type="message",
        time=parse_time(data.get("timestamp")),
        user_id=user_id,
        self_id=self_id,
        bot=bot,
        account_id=getattr(bot, "account_id", None),
        bot_id=getattr(bot, "bot_id", None),
        owner_id=None,
        role=role,
        message_type=message_type,
        sub_type="",
        message_id=str(data.get("id") or ""),
        raw_message=str(data.get("content") or ""),
        message=_content_segments(data.get("content")),
        raw=data,
        user=UserInfo(
            user_id=user_id,
            nickname=str(author.get("username") or ""),
            card=str(author.get("username") or ""),
            role=role,
        ),
    )


def _merge_self_mention(message: list[MessageSegment], data: dict, bot: Any, *, at_event: bool) -> None:
    """把「@ 本机器人」统一成 ``at(qq=self_id)`` 段，供框架既有触发逻辑识别。

    官方两种事件形态不同：
    - ``GROUP_AT_MESSAGE_CREATE``：content 已去掉 @ 前缀、mentions 不含自己
      → 必须补一个 ``at(self_id)`` 段，否则「仅 @ 我」的群永远不会触发；
    - ``GROUP_MESSAGE_CREATE``（全量）：content 保留 ``<@bot_member_openid>``、
      mentions 里 ``is_you=true`` → 把该段 qq 换成本 bot 的 self_id。
    """
    self_id = str(getattr(bot, "bot_id", "") or "")
    if not self_id:
        return
    own_ids = {
        str((m or {}).get("member_openid") or "")
        for m in (data.get("mentions") or [])
        if isinstance(m, dict) and m.get("is_you")
    }
    found = False
    for seg in message:
        if seg.type != "at":
            continue
        qq = str(seg.data.get("qq") or "")
        if qq in own_ids:
            seg.data["qq"] = self_id
            qq = self_id
        if qq == self_id:
            found = True
    if not found and (at_event or own_ids):
        message.insert(0, MessageSegment.at(self_id))


def _group_message(data: dict, bot: Any, *, at_event: bool = False) -> GroupMessageEvent:
    group_id = str(data.get("group_openid") or "")
    fields = _base_fields(data, bot, "message_group", "group")
    base_message = fields.pop("message")
    quoted = _quoted_text(data)
    message = list(base_message)
    if quoted:
        message = [MessageSegment.text(quoted), *message]
    message.extend(_attachment_segments(data.get("attachments")))
    _merge_self_mention(message, data, bot, at_event=at_event)
    return GroupMessageEvent(
        **fields,
        message=message,
        group=GroupInfo(group_id=group_id, group_name="", user_role=str((data.get("author") or {}).get("member_role") or "")),
    )


def _private_message(data: dict, bot: Any) -> PrivateMessageEvent:
    fields = _base_fields(data, bot, "message_private", "private")
    base_message = fields.pop("message")
    quoted = _quoted_text(data)
    message = list(base_message)
    if quoted:
        message = [MessageSegment.text(quoted), *message]
    message.extend(_attachment_segments(data.get("attachments")))
    return PrivateMessageEvent(**fields, message=message, group=None)


def decode_event(name: str, data: dict, bot: Any) -> BaseEvent | None:
    """把一条 ``op=0`` 事件解码成领域事件；不产生业务事件时返回 None。"""
    if name in ("GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"):
        return _group_message(data, bot, at_event=name == "GROUP_AT_MESSAGE_CREATE")
    if name == "C2C_MESSAGE_CREATE":
        return _private_message(data, bot)
    if name in ("GROUP_ADD_ROBOT", "GROUP_DEL_ROBOT", "GROUP_MSG_RECEIVE", "GROUP_MSG_REJECT"):
        return None  # 由网关更新本地群列表，不派发业务事件
    if name in ("FRIEND_ADD", "FRIEND_DEL", "C2C_MSG_RECEIVE", "C2C_MSG_REJECT", "GROUP_JOIN_REQUEST"):
        return NoticeEvent(
            event_type=f"notice_{name.lower()}",
            post_type="notice",
            time=parse_time(data.get("timestamp")),
            user_id=str(data.get("openid") or data.get("user_openid") or ""),
            self_id=str(getattr(bot, "bot_id", "") or ""),
            bot=bot,
            account_id=getattr(bot, "account_id", None),
            bot_id=getattr(bot, "bot_id", None),
            raw=data,
        )
    if not name:
        return None
    return None


def dedupe_key(name: str, data: dict, event_id: Any = "") -> str:
    """去重键：优先消息 id，其次事件 id（同一 msg_id 可能重复推送）。"""
    message_id = str(data.get("id") or "").strip()
    if message_id:
        return f"{name}:{message_id}"
    return f"{name}:{event_id}" if event_id else ""


def group_of(data: dict) -> str:
    return str(data.get("group_openid") or "").strip()


def message_target(data: dict) -> tuple[str, str]:
    """事件所属场景与目标（群 openid / 用户 openid）。"""
    group_id = group_of(data)
    if group_id:
        return "group", group_id
    author = data.get("author") or {}
    return "private", str(author.get("user_openid") or author.get("id") or "")
