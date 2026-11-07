"""私聊发送出口的契约：场景只由协议字段决定，不看 ``event.group``。

历史事故（2026-11-07）：``PrivateMessageEvent.group`` 被无条件塞成
``GroupInfo(group_id=0)``，发言通道的发送出口用 ``event.group is not None``
判群聊 → 私聊回复被发进 ``send_group_msg(0)``（OneBot 1400 / 日志「未知目标」），
用户一条都收不到。这里用**真实事件**钉死：

1. 私聊 ``deliver_now`` 必须走 ``send_private_msg``；
2. 私聊 ``_flush_speech`` 不得把 @ 段塞进消息（私聊 @ 无效）；
3. 发送失败不得写历史、不得回报成功（失败路径见 ``test_llm_speech_flow`` 的同组用例）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.delivery.speech import SpeechOutbox
from app.agent.loop.assistant_output import deliver_now, flush_speech
from app.domain.events import PrivateMessageEvent


class _Bot:
    def __init__(self):
        self.calls: list[tuple[str, int, str]] = []

    async def send_group_msg(self, group_id, message):
        self.calls.append(("group", group_id, str(message)))
        return {"status": "ok", "data": {"message_id": 1}}

    async def send_private_msg(self, user_id, message):
        self.calls.append(("private", user_id, str(message)))
        return {"status": "ok", "data": {"message_id": 2}}


class _SessionMgr:
    def __init__(self):
        self.added: list[str] = []

    def add_message(self, session_id, role, content):
        self.added.append(str(content))

    def mark_last_assistant_message_id(self, session_id, message_id):
        return None


def _private_event() -> PrivateMessageEvent:
    return PrivateMessageEvent(
        event_type="message_private",
        message_type="private",
        user_id=1901691195,
        self_id=123,
        message_id=-768578921,
        message=[],
    )


def _ctx(bot: _Bot, event: PrivateMessageEvent, mgr: _SessionMgr) -> SimpleNamespace:
    return SimpleNamespace(
        bot=bot,
        event=event,
        runtime=SimpleNamespace(bot_id="123", session_mgr=mgr),
        session_id="private_1901691195",
    )


@pytest.mark.asyncio
async def test_deliver_now_private_uses_send_private_msg():
    bot = _Bot()
    mgr = _SessionMgr()
    ctx = _ctx(bot, _private_event(), mgr)
    outbox = SpeechOutbox(immediate=True)
    outbox.queue("你好")

    sent, error = await deliver_now(ctx, outbox)

    assert (sent, error) == (1, "")
    assert bot.calls == [("private", 1901691195, "你好")]
    assert mgr.added == ["你好"]


@pytest.mark.asyncio
async def test_flush_speech_private_has_no_at_segment():
    """私聊里 @ 不生效：即使模型给了 at_user_id，也不能拼进消息。"""
    mgr = _SessionMgr()
    event = _private_event()
    outbox = SpeechOutbox()
    outbox.queue("看这个", at=("10001",))

    messages = [msg async for msg in flush_speech(outbox, mgr, "private_1901691195", {}, event)]

    assert len(messages) == 1
    segment_types = [seg.type for seg in messages[0].segments]
    assert "at" not in segment_types
    assert "10001" not in str(messages[0].to_onebot())
