"""群聊可见事件的数据契约（记录面上的"一条事实"）。

设计背景见 ``store.py`` 模块 docstring。这里只定义"记什么"：

- ``KIND_MESSAGE``：群里的普通消息（含图片/表情/语音等段，正文用既有渲染器转成占位文本）；
- ``KIND_EMOJI``：谁给哪条消息贴了/撤了表情（OneBot ``group_msg_emoji_like`` 通知）；
- ``KIND_POKE``：谁戳了谁（``notify.poke`` 通知；**私聊的戳也走这里**，见 ``scope``）；
- ``KIND_RECALL``：谁撤回了哪条消息（``group_recall`` 通知）；
- ``KIND_MY_SEND``：机器人自己发出的消息（来自发送成功回调，``by_me=True``）。

两条纪律：

1. **只记"消息相关"的事实**。入群/退群、输入状态这类不记（信息量低、噪音大）；
2. **写入即脱敏**：昵称由调用方先过 ``safe_sender_label`` 再放进 ``nickname``，
   避免日志绕过会话历史那套 ``mask_nickname`` 规则。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field
from typing import Any

KIND_MESSAGE = "message"
KIND_EMOJI = "emoji"
KIND_POKE = "poke"
KIND_RECALL = "recall"
KIND_MY_SEND = "my_send"

ALL_KINDS = (KIND_MESSAGE, KIND_EMOJI, KIND_POKE, KIND_RECALL, KIND_MY_SEND)

#: 戳一戳的幂等窗口（秒）：同一次戳的上报抖动不会记成两条
POKE_DEDUPE_WINDOW = 5


@dataclass
class LogEvent:
    """一条群聊可见事件。

    字段语义（``scope`` 是"记录分片键"）：

    - 群聊：``scope="group:<群号>"``，``group_id`` 同时写入；
    - 私聊：``scope="private:<对方QQ>"``，``group_id`` 留空。
      目前私聊**只有戳一戳**入账（其余私聊内容不记）。

    ``text`` 是渲染后的可读正文（原始段留在 ``payload["segments"]``），
    渲染层不再回头解析段，避免"记录面"与"渲染层"耦合。
    """

    ts: int
    kind: str
    scope: str = ""
    group_id: str = ""
    message_id: str = ""
    user_id: str = ""
    nickname: str = ""
    text: str = ""
    reply_to: str = ""
    payload: dict = field(default_factory=dict)
    by_me: bool = False
    key: str = ""

    def __post_init__(self) -> None:
        self.ts = int(self.ts or 0)
        if not self.ts:
            self.ts = int(time.time())
        self.kind = str(self.kind or "")
        self.scope = str(self.scope or "")
        self.group_id = str(self.group_id or "")
        self.message_id = str(self.message_id or "")
        self.user_id = str(self.user_id or "")
        self.nickname = str(self.nickname or "")
        self.text = str(self.text or "")
        self.reply_to = str(self.reply_to or "")
        self.by_me = bool(self.by_me)
        if not isinstance(self.payload, dict):
            self.payload = {}
        if not self.key:
            self.key = event_key(self)

    @property
    def emoji_id(self) -> str:
        """表情 id（仅 ``KIND_EMOJI`` 有意义）。"""
        return str(self.payload.get("emoji_id", "") or "")

    @property
    def is_add(self) -> bool:
        """表情/动作是"添加"还是"取消"。"""
        return bool(self.payload.get("is_add", True))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> LogEvent:
        if not isinstance(data, dict):
            return cls(ts=int(time.time()), kind="")
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


def event_key(event: LogEvent) -> str:
    """幂等键：同一件事重复上报（网关重推 / 多路订阅）只应记一条。

    各 kind 的口径：

    - ``message`` / ``my_send``：``kind:scope:message_id``（无 id 时退化到"时间+发送者+正文哈希"）；
    - ``emoji``：同一条消息上**同一个人贴的同一个表情**只记一条（贴/取消可区分）；
    - ``poke``：``kind:scope:operator:target:ts//5``（5 秒窗口内的重复上报合并）；
    - ``recall``：``kind:scope:message_id``。

    注意：键**不含来源**，所以同一个事件被模块 hook 与其它路径各记一次时，也会被合并。
    """
    kind = str(event.kind or "")
    scope = str(event.scope or "")
    if kind in (KIND_MESSAGE, KIND_MY_SEND):
        if event.message_id:
            return f"{kind}:{scope}:{event.message_id}"
        digest = hashlib.sha1(
            f"{event.user_id}:{event.text}".encode("utf-8", "ignore")
        ).hexdigest()[:12]
        return f"{kind}:{scope}:{event.ts}:{digest}"
    if kind == KIND_EMOJI:
        # 末尾的 actor 位：同一条消息上"我贴的"和"别人贴的"必须分开记，
        # 否则机器人的动作会被自己的幂等键吃掉（"我干过什么"就永远查不到）。
        actor = "me" if event.by_me else "other"
        return (
            f"{kind}:{scope}:{event.message_id}:{event.user_id}:"
            f"{event.emoji_id}:{1 if event.is_add else 0}:{actor}"
        )
    if kind == KIND_POKE:
        operator = str(event.user_id or "")
        target = str(event.payload.get("target_id", "") or "")
        return f"{kind}:{scope}:{operator}:{target}:{event.ts // POKE_DEDUPE_WINDOW}"
    if kind == KIND_RECALL:
        return f"{kind}:{scope}:{event.message_id}"
    return f"{kind}:{scope}:{event.ts}:{event.user_id}"


def make_scope(is_group: bool, *, group_id: Any = None, user_id: Any = None) -> str:
    """记录分片键：群聊按群，私聊按对方。

    跨群/跨会话隔离就靠它——群 A 的记录永远查不到群 B 的桶里去。
    """
    if is_group:
        gid = str(group_id or "")
        return f"group:{gid}" if gid else ""
    uid = str(user_id or "")
    return f"private:{uid}" if uid else ""
