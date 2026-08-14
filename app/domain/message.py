"""消息模型：消息段 + 消息链，与传输层解耦。

模块通过 Message / MessageSegment 构造与解析消息，
OneBot 适配器负责与 payload 相互转换。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class MessageSegment:
    """OneBot 消息段。type + data，data 为段参数。"""

    type: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"type": self.type, "data": dict(self.data)}

    # ---------- 便捷构造 ----------
    @classmethod
    def text(cls, text: str) -> "MessageSegment":
        return cls("text", {"text": text})

    @classmethod
    def at(cls, qq: str | int) -> "MessageSegment":
        return cls("at", {"qq": str(qq)})

    @classmethod
    def reply(cls, message_id: str | int) -> "MessageSegment":
        return cls("reply", {"id": str(message_id)})

    @classmethod
    def image(cls, file: str) -> "MessageSegment":
        return cls("image", {"file": file})

    @classmethod
    def node(cls, name: str, uin: str | int, content: Any) -> "MessageSegment":
        return cls("node", {"name": name, "uin": uin, "content": content})


class Message:
    """消息链：有序消息段集合，可直接作为 send_xxx 的 message 参数。"""

    def __init__(self, segments: Iterable[MessageSegment | dict] = None) -> None:
        self.segments: list[MessageSegment] = []
        for seg in segments or []:
            self.append(seg)

    def append(self, seg: MessageSegment | dict | str) -> None:
        if isinstance(seg, MessageSegment):
            self.segments.append(seg)
        elif isinstance(seg, dict):
            self.segments.append(MessageSegment(seg.get("type", ""), seg.get("data", {}) or {}))
        elif isinstance(seg, str):
            self.segments.append(MessageSegment.text(seg))

    def extend(self, segments: Iterable[MessageSegment | dict | str]) -> None:
        for seg in segments:
            self.append(seg)

    def to_onebot(self) -> list[dict]:
        """转为一阶 dict 消息段数组（供 OneBot API 发送）。"""
        return [seg.to_dict() for seg in self.segments]

    @property
    def text(self) -> str:
        """拼接所有文本段。"""
        return "".join(s.data.get("text", "") for s in self.segments if s.type == "text")

    def __str__(self) -> str:
        return self.text or f"<Message {len(self.segments)} segs>"

    @classmethod
    def from_text(cls, text: str) -> "Message":
        return cls([MessageSegment.text(text)])

    @classmethod
    def from_onebot(cls, segments: Any) -> "Message":
        """从 OneBot 消息段（dict 数组或字符串）构造。"""
        if isinstance(segments, str):
            return cls.from_text(segments)
        if isinstance(segments, list):
            return cls(segments)
        return cls()


SegmentLike = str | MessageSegment | dict | Message | list[dict] | list[MessageSegment]
