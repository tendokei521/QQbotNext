"""发言通道（``app.llm.speech``）的护栏测试。

这一层要守住四条**会直接毁掉拟人化**的红线：

1. **不按空格/标点切句**：模型传几条就是几条（旧实现按句切，碎片永远不像人说话）；
2. **上限与去重**：一轮不能无限发、同一句不能重复入稿（复读机式刷屏）；
3. **静默是正常结局**：``silent`` 可判定，且不会被上层当成"失败"去补一句兜底话术；
4. **显式引用/@ 走参数**，正文里残留的 ``[reply]``/``[@QQ]`` 标记一律剥掉。
"""

from __future__ import annotations

import pytest

from app.llm.speech import (
    DEFAULT_MAX_BUBBLES,
    FORCE_SPEAK_REMINDER,
    SPEAK_REMINDER,
    SpeechOutbox,
    build_speech_tools,
    normalize_messages,
    speech_via_tool,
)


def _tools(outbox: SpeechOutbox) -> dict:
    return {spec.name: spec for spec in build_speech_tools(outbox)}


async def _call(outbox: SpeechOutbox, name: str, args: dict) -> str:
    spec = _tools(outbox)[name]
    return await spec.handler(None, args)


# ---------- 参数归一化 ----------


def test_normalize_messages_does_not_split_sentences():
    """字符串=一条，绝不按空格/问号/句号切（切句是模型的决定，不是框架的）。"""
    assert normalize_messages("你好呀 今天吃什么？") == ["你好呀 今天吃什么？"]
    assert normalize_messages(["第一句", "第二句"]) == ["第一句", "第二句"]
    assert normalize_messages(["", "  ", "有内容"]) == ["有内容"]
    assert normalize_messages(None) == []
    assert normalize_messages(123) == ["123"]


# ---------- send_message ----------


async def test_send_message_queues_without_sending():
    outbox = SpeechOutbox()
    reply = await _call(outbox, "send_message", {"messages": "在吗"})
    assert reply.startswith("ok:")
    assert outbox.queued == 1
    assert outbox.spoken == 0, "入稿不等于已发言"
    assert not outbox.silent

    bubbles = outbox.take()
    assert [b.text for b in bubbles] == ["在吗"]
    assert outbox.queued == 0 and outbox.spoken == 1


async def test_send_message_array_keeps_order_and_metadata():
    outbox = SpeechOutbox()
    await _call(
        outbox,
        "send_message",
        {"messages": ["先这条", "再这条"], "reply_to_message_id": 22350, "at_user_id": "10001, 10002"},
    )
    bubbles = outbox.take()
    assert [b.text for b in bubbles] == ["先这条", "再这条"]
    assert bubbles[0].reply_to == "22350"
    assert bubbles[0].at == ("10001", "10002")


async def test_send_message_rejects_empty_and_duplicates():
    outbox = SpeechOutbox()
    empty = await _call(outbox, "send_message", {"messages": []})
    assert empty.startswith("error:")
    assert outbox.queued == 0

    await _call(outbox, "send_message", {"messages": "草"})
    dup = await _call(outbox, "send_message", {"messages": "草"})
    assert outbox.queued == 1, "同一句不该入稿两次"
    assert "跳过" in dup
    assert outbox.dropped == 1


async def test_send_message_respects_bubble_cap():
    outbox = SpeechOutbox(max_bubbles=2)
    await _call(outbox, "send_message", {"messages": ["一", "二", "三"]})
    assert outbox.queued == 2
    more = await _call(outbox, "send_message", {"messages": "四"})
    assert more.startswith("error:")
    assert "上限" in more or "不再接受" in more


async def test_send_message_truncates_long_bubble_instead_of_rejecting():
    outbox = SpeechOutbox(max_chars=10)
    reply = await _call(outbox, "send_message", {"messages": "很" * 50})
    assert reply.startswith("ok:")
    assert outbox.queued == 1
    assert len(outbox.take()[0].text) == 10


def test_bubble_becomes_message_with_reply_at_text_order():
    """气泡 → 待发消息：段序必须是 reply → at… → text（QQ 只认这个顺序）。"""
    from app.llm.actions import OutboundDirectives, assemble_message

    message = assemble_message(
        "那我先睡了",
        OutboundDirectives(reply=True, ats=["10001", "10002"]),
        object(),  # 事件对象：这里走显式 reply_message_id，不读 event.message_id
        is_group=True,
        reply_message_id="22350",
    )
    assert [seg.type for seg in message.segments] == ["reply", "at", "at", "text"]
    assert message.segments[0].data["id"] == "22350"
    assert [seg.data["qq"] for seg in message.segments[1:3]] == ["10001", "10002"]
    assert message.text == "那我先睡了"


def test_bubble_without_targets_is_plain_text():
    """没有引用/@ 的气泡就是一条纯文本消息（不要凭空造段）。"""
    from app.llm.actions import OutboundDirectives, assemble_message

    message = assemble_message("嗯", OutboundDirectives(), object(), is_group=True)
    assert [seg.type for seg in message.segments] == ["text"]


def test_speech_tools_are_marked_speech_source():
    """``source="speech"`` 让工具执行器跳过「回应要求」追加（否则模型会把话再说一遍）。"""
    specs = build_speech_tools(SpeechOutbox())
    assert {s.name for s in specs} == {"send_message", "finish"}
    assert all(s.source == "speech" for s in specs)
    assert all(s.permission == "everyone" for s in specs)


def test_speech_tool_schema_is_provider_friendly():
    """``messages`` 用联合 type + items，不用嵌套 anyOf（部分网关会拒绝）。"""
    spec = {s.name: s for s in build_speech_tools(SpeechOutbox())}["send_message"]
    messages = spec.parameters["properties"]["messages"]
    assert messages["type"] == ["string", "array"]
    assert messages["items"] == {"type": "string"}
    assert spec.parameters["required"] == ["messages"]


# ---------- finish / 静默 ----------


async def test_finish_marks_silence_without_queueing():
    outbox = SpeechOutbox()
    reply = await _call(outbox, "finish", {"summary": "他们在聊游戏，我没兴趣", "active_topic": "游戏"})
    assert reply.startswith("ok:")
    assert outbox.finished is True
    assert outbox.finish_reason.startswith("他们在聊游戏")
    assert outbox.active_topic == "游戏"
    assert outbox.queued == 0
    assert outbox.silent, "看过不说话=静默，不是失败"


async def test_finish_keeps_queued_bubbles():
    outbox = SpeechOutbox()
    await _call(outbox, "send_message", {"messages": "那我先睡了"})
    reply = await _call(outbox, "finish", {"summary": "说完了"})
    assert "1 条" in reply
    assert outbox.queued == 1, "收尾不能把已排队的气泡吞掉"


def test_outbox_summary_reports_state():
    outbox = SpeechOutbox()
    outbox.queue("哦")
    outbox.take()
    outbox.mark_finished("懒得接")
    assert outbox.summary() == {
        "spoken": 1,
        "finished": True,
        "finish_reason": "懒得接",
        "active_topic": "",
        "dropped": 0,
        "silent": False,
    }


# ---------- 档位开关 ----------


@pytest.mark.parametrize(
    "config,expected",
    [
        ({}, False),
        ({"assembly_profile": "qq_agent", "speech_via_tool": False, "stream_output": True}, False),
        ({"assembly_profile": "qq_agent", "speech_via_tool": True, "stream_output": False}, False),
        ({"assembly_profile": "legacy", "speech_via_tool": True, "stream_output": True}, False),
        ({"assembly_profile": "qq_agent", "speech_via_tool": True, "stream_output": True}, True),
    ],
)
def test_speech_via_tool_requires_profile_toggle_and_stream(config, expected):
    """三个条件缺一不可：拟人化档位、开关、流式（非流式没有轮次边界可 flush）。"""
    assert speech_via_tool(config) is expected


def test_defaults_are_documented():
    assert DEFAULT_MAX_BUBBLES >= 2, "至少能发 2 条，否则「分条说话」无从谈起"
    assert "{rounds}" in SPEAK_REMINDER
    assert "send_message" in SPEAK_REMINDER
    assert "send_message" in FORCE_SPEAK_REMINDER
