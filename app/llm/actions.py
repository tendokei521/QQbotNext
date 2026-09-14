"""输出侧动作通道：把模型文本里的轻量指令解析成真正的 OneBot 消息段。

背景：模型输出是纯文本，此前**没有任何引用/@ 的输出通道** —— 无论模型多“主动”，
它都无法引用某条消息或 @ 某人。这里提供最小可用的通道，让“想引用时能引用”：

支持指令（必须出现在回复正文**最前面**，可组合、顺序无关）：

- ``[reply]``    引用触发本轮的那条消息（用户在回什么，就引用什么）
- ``[@123456]``  真实 @ 某个 QQ（仅群聊有效；私聊会被剥离但不生效）

约定与防护：

- 只在**本轮首个输出片段**解析，之后的片段不再改写（避免给已发出的消息补引用，
  也避免流式过程中指令与正文错位）；
- 解析后指令一律从正文剥离，绝不漏给用户；
- @ 数量上限（``max_at``，默认 3），引用天然最多一次；
- 整块可通过 ``outbound_directive_enable`` 关闭。

历史上这些接口是纯文本输出，因此本模块是**新增能力**，不改变没有指令时的行为：
没有指令 → 返回的 Message 与 ``Message.from_text`` 等价。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.message import Message, MessageSegment

DEFAULT_MAX_AT = 3

_REPLY_RE = re.compile(r"\[reply\]", re.IGNORECASE)
_AT_RE = re.compile(r"\[@\s*(\d{5,12})\s*\]")


@dataclass
class OutboundDirectives:
    """解析出的输出侧指令。"""

    reply: bool = False
    ats: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.reply or self.ats)

    def merge(self, other: "OutboundDirectives") -> "OutboundDirectives":
        return OutboundDirectives(
            reply=self.reply or other.reply,
            ats=list(self.ats) + [qq for qq in other.ats if qq not in self.ats],
        )


def _leading_directives(text: str, max_at: int) -> tuple[OutboundDirectives, str]:
    """只消费正文**最前面**的连续指令，遇到正文即停止。"""
    rest = text
    out = OutboundDirectives()
    while True:
        stripped = rest.lstrip()
        if not stripped:
            return out, ""
        m = _REPLY_RE.match(stripped)
        if m:
            out.reply = True
            rest = stripped[m.end():]
            continue
        m = _AT_RE.match(stripped)
        if m:
            qq = m.group(1)
            if len(out.ats) < max(0, max_at) and qq not in out.ats:
                out.ats.append(qq)
            rest = stripped[m.end():]
            continue
        return out, rest


def strip_outbound_directives(text: str) -> str:
    """防御性剥离正文里残留的指令标记（非开头的也不漏给用户）。"""
    text = _REPLY_RE.sub("", str(text or ""))
    return _AT_RE.sub("", text)


def parse_outbound_directives(text: str, *, max_at: int = DEFAULT_MAX_AT):
    """解析开头的指令；返回 (directives, 剥离后的正文)。"""
    directives, rest = _leading_directives(str(text or ""), max_at)
    return directives, strip_outbound_directives(rest).strip()


def assemble_message(
    text: str,
    directives: OutboundDirectives,
    event,
    *,
    is_group: bool = True,
) -> Message:
    """按 OneBot 段序组装消息：reply → at… → text。"""
    segments: list[MessageSegment] = []
    if directives.reply:
        message_id = getattr(event, "message_id", None)
        if message_id not in (None, ""):
            segments.append(MessageSegment.reply(message_id))
    if directives.ats and is_group:
        for qq in directives.ats:
            segments.append(MessageSegment.at(qq))
    segments.append(MessageSegment.text(text))
    return Message(segments)


class OutboundBuilder:
    """按轮次构造待发送消息：只有首片能带指令，正文为空时不产生空消息。"""

    def __init__(self, event, *, enable: bool = True, max_at: int = DEFAULT_MAX_AT) -> None:
        self.event = event
        self.enable = bool(enable)
        self.max_at = max(0, int(max_at))
        self._first_done = False
        self._pending = OutboundDirectives()

    def build(self, text: str) -> Message | None:
        """返回 None 表示本片解析后没有正文可发（含“只有指令”的片）。"""
        directives = OutboundDirectives()
        clean = str(text or "")

        if self.enable:
            if not self._first_done:
                self._first_done = True
                directives, clean = parse_outbound_directives(clean, max_at=self.max_at)
                if directives and not clean:
                    # 首片只有指令（模型单独输出一行 [reply]）：指令挂到下一片正文
                    self._pending = directives
                    return None
            elif self._pending:
                directives, self._pending = self._pending, OutboundDirectives()
                clean = strip_outbound_directives(clean).strip()
            else:
                clean = strip_outbound_directives(clean).strip()
        else:
            clean = clean.strip()

        if not clean:
            return None

        is_group = getattr(self.event, "event_type", "") == "message_group" or (
            getattr(self.event, "group", None) is not None
        )
        return assemble_message(clean, directives, self.event, is_group=is_group)


def build_outbound_builder(event, config) -> OutboundBuilder:
    """按配置构造输出侧指令解析器（默认开启，@ 上限 3）。"""
    enable = True
    max_at = DEFAULT_MAX_AT
    if config is not None and hasattr(config, "get"):
        try:
            enable = bool(config.get("outbound_directive_enable", True))
        except Exception:
            enable = True
        try:
            max_at = int(config.get("outbound_directive_max_at", DEFAULT_MAX_AT))
        except (TypeError, ValueError):
            max_at = DEFAULT_MAX_AT
    return OutboundBuilder(event, enable=enable, max_at=max_at)
