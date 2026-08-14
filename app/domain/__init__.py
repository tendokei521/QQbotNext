"""领域层：Bot 抽象、事件模型、消息模型、通用模型。"""

from app.domain.bot import IBot
from app.domain.events import (
    BaseEvent,
    GroupInfo,
    GroupMessageEvent,
    MessageEvent,
    MetaEvent,
    NoticeEvent,
    PrivateMessageEvent,
    RequestEvent,
    TimeCoreEvent,
    UserInfo,
)
from app.domain.message import Message, MessageSegment

__all__ = [
    "IBot",
    "BaseEvent",
    "GroupInfo",
    "UserInfo",
    "GroupMessageEvent",
    "PrivateMessageEvent",
    "MessageEvent",
    "NoticeEvent",
    "RequestEvent",
    "MetaEvent",
    "TimeCoreEvent",
    "Message",
    "MessageSegment",
]
