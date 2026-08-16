"""领域事件模型。

替代原 ws_msgprocess.MsgData + event_bus.Event：
- 事件对象直接携带解析后的字段（不再有 msg_object.User / .Group / .Msg 三级嵌套）；
- 每个事件持有其所属的 IBot，模块通过 event.bot / event.reply() 收发消息；
- event_type 沿用原事件字符串（message_group / notice_poke / request_group / time_core …），
  保证模块订阅语义不变。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.domain.bot import IBot
from app.domain.message import Message, MessageSegment, SegmentLike


@dataclass
class UserInfo:
    user_id: int = 0
    nickname: str = ""
    card: str = ""
    role: str = ""


@dataclass
class GroupInfo:
    group_id: int = 0
    group_name: str = ""
    user_role: str = ""


class LlmGate:
    """模块内控制 LLM 是否参与本次事件的开关（通过 event.llm 访问）。

    框架的 LLM Agent 节点在模块链之后兜底执行；模块在 handle 中调用
    event.llm.stop() 声明「我已处理，跳过 LLM 回复」。

    在 LLM 流水线中，钩子可通过 event.llm.wait_continue() / resume()
    实现暂停与继续（如请求池防抖）。
    """

    def __init__(self, event: "BaseEvent") -> None:
        self._event = event

    def stop(self) -> None:
        """标记：模块已处理本次事件，跳过 LLM 的回复部分。"""
        self._event._llm_stop = True
        job = getattr(self._event, "_llm_job", None)
        if job is not None:
            job.skip = True

    @property
    def stopped(self) -> bool:
        return self._event._llm_stop

    def resume(self) -> None:
        """放行当前 LLM Job（用于 LLM 流水线中的手动继续）。

        注：``continue`` 是 Python 关键字，因此命名为 ``resume``。
        """
        job = getattr(self._event, "_llm_job", None)
        if job is not None:
            job.go.set()

    async def wait_continue(self, timeout: float | None = None) -> None:
        """暂停当前 LLM 流水线，直到 continue() 被调用或超时。"""
        job = getattr(self._event, "_llm_job", None)
        if job is None:
            return
        await asyncio.wait_for(job.go.wait(), timeout)


@dataclass
class BaseEvent:
    """所有事件基类。event_type 为模块订阅依据。"""

    event_type: str
    post_type: str = ""
    time: int = 0
    user_id: int = 0
    self_id: int = 0
    bot: IBot = None
    bot_id: int | None = None
    bot_index: int | None = None
    owner_id: int | None = None
    # 权限（由 dispatcher 计算后写入）
    role: str = "member"               # member / group_admin / group_owner
    is_bot_owner: bool = False
    is_group_owner: bool = False
    is_admin: bool = False
    is_member: bool = True
    permission_role: str = "member"    # 最终生效角色：member / group_admin / group_owner / owner
    raw: dict = field(default_factory=dict)
    # 模块可调用 event.llm.stop() 跳过 LLM 处理（LLM 节点在模块链之后执行）
    _llm_stop: bool = False
    # LLM 流水线提交后挂载的 LlmJob（供 event.llm.resume/wait_continue 使用）
    _llm_job: Any = field(default=None, repr=False, compare=False)
    # 模块可调用 event.stop() 强制终止整条节点链（对齐 astrbot stop_event）
    _stopped: bool = False
    # 事件所属节点链上下文（dispatcher 注入，供 event.stop() 短路链路；不参与 repr/eq）
    _ctx: Any = field(default=None, repr=False, compare=False)

    @property
    def llm(self) -> LlmGate:
        """LLM 门控：event.llm.stop() 跳过本次事件的 LLM 回复。"""
        return LlmGate(self)

    def stop(self) -> None:
        """强制终止本事件在节点链中的继续传播（对齐 astrbot stop_event）。

        调用后：后续模块、LLM 兜底均不再执行；链在下一个节点边界立即短路。
        与 event.llm.stop() 的区别：后者仅跳过 LLM，模块链照常。
        """
        self._stopped = True
        ctx = getattr(self, "_ctx", None)
        if ctx is not None:
            ctx.cancelled = True

    @property
    def stopped(self) -> bool:
        """事件是否已被某模块调用 event.stop() 终止。"""
        return self._stopped

    async def reply(self, message: SegmentLike, **kwargs) -> dict:
        """向本事件的目标发送消息（群/私聊自动判断）。"""
        raise NotImplementedError


@dataclass
class MessageEvent(BaseEvent):
    """消息类事件（群消息 / 私聊消息）。

    对应 OneBot v11 中 ``post_type="message"`` / ``"message_sent"`` 的上报。
    解码器（``app/infrastructure/onebot/codec.py``）从 payload 中提取以下可能字段：

    公共字段（由 BaseEvent 承载）：
    - ``time``: int，事件发生时间（Unix 秒）
    - ``self_id``: int，收到事件的机器人 QQ
    - ``user_id``: int，发送者 QQ
    - ``post_type``: str，``"message"`` / ``"message_sent"``

    message 事件字段：
    - ``message_type``: str，``"private"`` 或 ``"group"``
    - ``sub_type``: str
      - private：``friend`` / ``group`` / ``other`` / ``self``
      - group：``normal`` / ``anonymous`` / ``notice``
    - ``message_id``: int，消息 ID
    - ``group_id``: int，群号（仅群消息）
    - ``message``: list[MessageSegment]，消息段数组（也兼容字符串形式）
    - ``raw_message``: str，原始消息文本
    - ``font``: int，字体（未单独存字段，保留在 ``raw``）
    - ``target_id``: int，私聊/发送场景的接收者 QQ（未单独存字段，保留在 ``raw``）
    - ``sender``: dict，发送者信息，解码为 ``UserInfo``：
      ``user_id`` / ``nickname`` / ``card`` / ``role`` / ``sex`` / ``age`` / ``area`` / ``level`` / ``title`` / ``group_id``
    - ``user``: UserInfo，解码后的发送者
    - ``group``: GroupInfo，解码后的群信息（仅群消息，含 ``group_id`` / ``group_name`` / 发送者群内角色）
    """

    message_type: str = ""
    sub_type: str = ""
    message_id: int = 0
    raw_message: str = ""
    message: list = field(default_factory=list)  # list[MessageSegment]
    forward_msg: list = field(default_factory=list)
    user: UserInfo = field(default_factory=UserInfo)
    group: GroupInfo = field(default_factory=GroupInfo)

    @property
    def text(self) -> str:
        """提取全部文本段拼接。"""
        return "".join(s.data.get("text", "") for s in self.message if s.type == "text")

    def is_at_me(self) -> bool:
        """是否 @ 了本 bot。"""
        for seg in self.message:
            if seg.type == "at" and str(seg.data.get("qq", "")) == str(self.self_id):
                return True
        return False

    async def reply(self, message: SegmentLike, **kwargs) -> dict:
        m = _to_message(message)
        if self.message_type == "private":
            return await self.bot.send_private_msg(self.user_id, m, **kwargs)
        return await self.bot.send_group_msg(self.group.group_id, m, **kwargs)


@dataclass
class GroupMessageEvent(MessageEvent):
    """群消息事件。

    除 MessageEvent 公共字段外，OneBot 群消息 payload 还常含：
    - ``group_id``: int，群号
    - ``anonymous``: dict，匿名信息（如有）
    - ``sender.group_id``: int，发送者所在群
    - ``sender.role``: str，``owner`` / ``admin`` / ``member``
    - ``sender.card``: str，群名片
    """

    event_type: str = "message_group"
    message_type: str = "group"


@dataclass
class PrivateMessageEvent(MessageEvent):
    """私聊消息事件。

    除 MessageEvent 公共字段外，OneBot 私聊消息 payload 还常含：
    - ``sub_type``: str，``friend`` / ``group`` / ``other`` / ``self``
    - ``sender.user_id``: int，发送者 QQ
    - ``sender.nickname``: str，昵称
    - ``target_id``: int，接收者 QQ（部分实现/发送事件场景）
    """

    event_type: str = "message_private"
    message_type: str = "private"


@dataclass
class NoticeEvent(BaseEvent):
    """通知类事件（戳一戳 / 表情回应 / 撤回等）。"""

    notice_type: str = ""
    sub_type: str = ""
    group_id: int = 0
    target_id: int = 0
    # 表情回应
    emoji_likes: list = field(default_factory=list)
    emoji_is_add: bool = False
    # 撤回
    operator_id: int = 0
    message_id: int = 0

    async def reply(self, message: SegmentLike, **kwargs) -> dict:
        m = _to_message(message)
        if self.group_id:
            return await self.bot.send_group_msg(self.group_id, m, **kwargs)
        return await self.bot.send_private_msg(self.user_id, m, **kwargs)


@dataclass
class RequestEvent(BaseEvent):
    """申请类事件（加群 / 加好友）。"""

    request_type: str = ""
    sub_type: str = ""
    group_id: int = 0
    comment: str = ""
    flag: str = ""
    operator_id: int = 0

    async def approve(self, approve: bool = True, reason: str = "") -> dict:
        """处理申请。"""
        if self.request_type == "group":
            return await self.bot.set_group_add_request(self.flag, approve=approve, reason=reason)
        return await self.bot.set_friend_add_request(self.flag, approve=approve)


@dataclass
class MetaEvent(BaseEvent):
    """生命周期类事件（心跳等）。"""

    meta_event_type: str = ""


@dataclass
class TimeCoreEvent(BaseEvent):
    """调度器发出的时间核心事件（替代旧 time_core）。"""

    event_type: str = "time_core"
    hour: int = 0
    minute: int = 0

    async def reply(self, message: SegmentLike, **kwargs) -> dict:  # pragma: no cover
        raise RuntimeError("time_core 事件无回复目标")


def _to_message(message: SegmentLike) -> Message:
    if isinstance(message, Message):
        return message
    if isinstance(message, str):
        return Message.from_text(message)
    if isinstance(message, MessageSegment):
        return Message([message])
    if isinstance(message, list):
        return Message(message)
    return Message([MessageSegment.text(str(message))])
