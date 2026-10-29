"""发言通道（``app.agent.delivery.speech``）的护栏测试。

这一层要守住四条**会直接毁掉拟人化**的红线：

1. **不按空格/标点切句**：模型传几条就是几条（旧实现按句切，碎片永远不像人说话）；
2. **上限与去重**：一轮不能无限发、同一句不能重复入稿（复读机式刷屏）；
3. **静默是正常结局**：``silent`` 可判定，且不会被上层当成"失败"去补一句兜底话术；
4. **显式引用/@ 走参数**，正文里残留的 ``[reply]``/``[@QQ]`` 标记一律剥掉。
"""

from __future__ import annotations

import pytest

from app.agent.delivery.speech import (
    DEFAULT_MAX_BUBBLES,
    FORCE_SPEAK_REMINDER,
    QUEUE_DUP,
    QUEUE_OK,
    SPEAK_REMINDER,
    SPEECH_PROTOCOL,
    SpeechOutbox,
    build_speech_tools,
    normalize_messages,
    speech_via_tool,
)

#: 发言通道生效的完整条件（档位 + 开关 + 流式）
SPEECH_CONFIG: dict = {
    "assembly_profile": "qq_agent",
    "speech_via_tool": True,
    "stream_output": True,
}


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
    # 截断必须**告诉模型**：否则它以为整段已送达，用户只收到被砍断的半句
    assert "截断" in reply


def test_truncated_bubble_is_deduplicated_on_the_truncated_text():
    """去重必须按**裁剪后**的文本比对。

    旧实现是「按全文比对、存裁剪后的文本」：``clean in self._seen`` 用的是全文，
    而 ``self._seen.add(clean)`` 存的是裁剪结果 → 超长正文重复入稿永远判不出重复，
    同一段长文会被切成同样的前 10 字反复排进队列。
    """
    outbox = SpeechOutbox(max_chars=10, max_bubbles=4)
    long_text = "很" * 50

    first, _ = outbox.queue(long_text)
    second, note = outbox.queue(long_text)

    assert first == QUEUE_OK
    assert second == QUEUE_DUP, f"同样的超长正文应判重，实际 {second}"
    assert outbox.queued == 1
    assert outbox.dropped == 1


def test_truncation_note_reaches_the_model_in_batch_write():
    """批量入稿里同批有成功项时，截断附注也不能被丢掉。"""
    outbox = SpeechOutbox(max_chars=10, max_bubbles=4)
    accepted, notes = outbox.queue_many(["短句", "很" * 50])

    assert accepted == 2
    assert any("截断" in n for n in notes), f"截断附注丢了：{notes}"


def test_speech_protocol_is_injected_into_the_user_turn():
    """发言协议必须贴在**最后一条 user 消息末尾**——位置错了模型就不照做。

    实测（同一份真实请求，deepseek-v4-flash）：协议只在 system 行为段里、
    或作为尾部 system 追加时，模型依然只输出文本（整轮静默）；
    贴到 user 消息里才稳定调用 send_message。这条用例把这个位置钉死。
    """
    from app.agent.assembly.assembler import PromptRequest, apply_speech_protocol

    req = PromptRequest(config=SPEECH_CONFIG)
    messages = [
        {"role": "system", "content": "人设"},
        {"role": "user", "content": "10-18 19:00 小明(20002): 在吗"},
    ]
    out = apply_speech_protocol(messages, req)

    assert out[-1]["role"] == "user"
    assert out[-1]["content"].startswith("10-18 19:00 小明(20002): 在吗")
    assert out[-1]["content"].rstrip().endswith(SPEECH_PROTOCOL)
    assert out[0] == {"role": "system", "content": "人设"}, "system 侧不能被改写"
    assert "不会发到聊天里" in SPEECH_PROTOCOL
    assert "send_message" in SPEECH_PROTOCOL
    assert "不想接话就直接结束" in SPEECH_PROTOCOL, "必须同时给出「沉默」这个正常出口"


def test_speech_protocol_respects_multimodal_user_content():
    """多模态 user 消息（content 是块数组）时把协议作为 text 块追加，不破坏结构。"""
    from app.agent.assembly.assembler import PromptRequest, apply_speech_protocol

    req = PromptRequest(config=SPEECH_CONFIG)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
            ],
        }
    ]
    out = apply_speech_protocol(messages, req)
    assert out[-1]["content"][-1] == {"type": "text", "text": SPEECH_PROTOCOL}
    assert out[-1]["content"][0]["text"] == "看图"


@pytest.mark.parametrize(
    "config",
    [
        {"assembly_profile": "qq_agent", "speech_via_tool": False, "stream_output": True},
        {},
    ],
)
def test_speech_protocol_absent_when_channel_off(config):
    """文本即发言的档位贴这句就是假话——必须一个字都不加。"""
    from app.agent.assembly.assembler import PromptRequest, apply_speech_protocol

    messages = [{"role": "user", "content": "在吗"}]
    out = apply_speech_protocol(messages, PromptRequest(config=config))
    assert out == [{"role": "user", "content": "在吗"}]


def test_bubble_becomes_message_with_reply_at_text_order():
    from app.agent.delivery.directives import OutboundDirectives, assemble_message

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
    from app.agent.delivery.directives import OutboundDirectives, assemble_message

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


# ---------- 发言通道开关 ----------


@pytest.mark.parametrize(
    "config,expected",
    [
        ({}, False),
        ({"assembly_profile": "qq_agent", "speech_via_tool": False, "stream_output": True}, False),
        # 只看开关：档位与 stream_output 都不再是门槛（发言通道已成主形态）
        ({"assembly_profile": "qq_agent", "speech_via_tool": True, "stream_output": False}, True),
        ({"assembly_profile": "legacy", "speech_via_tool": True, "stream_output": True}, True),
        ({"speech_via_tool": True}, True),
    ],
)
def test_speech_via_tool_only_reads_the_switch(config, expected):
    """发言通道只由 ``speech_via_tool`` 决定；档位/流式不再参与。"""
    assert speech_via_tool(config) is expected


def test_defaults_are_documented():
    assert DEFAULT_MAX_BUBBLES >= 2, "至少能发 2 条，否则「分条说话」无从谈起"
    assert "{rounds}" in SPEAK_REMINDER
    assert "send_message" in SPEAK_REMINDER
    assert "send_message" in FORCE_SPEAK_REMINDER


def test_speech_channel_is_on_by_default():
    """默认就走「文本=思考，send_message=发言」：三处默认值必须一致。"""
    from app.agent.config import DEFAULT_LLM_CONFIG
    from app.plugins.llm_output.config_schema import SCHEMA as OUTPUT_SCHEMA
    from app.plugins.llm_output.main import Module as OutputModule

    assert DEFAULT_LLM_CONFIG["speech_via_tool"] is True
    assert OutputModule.default_config["speech_via_tool"] is True
    assert OUTPUT_SCHEMA["speech_via_tool"]["default"] is True


def test_chat_prompt_explains_tool_speech():
    """聊天预设的提示词必须讲清"工具是动作/怎么分条"（对齐 qqbridge v2 的口径）。"""
    from app.agent.persona import rules

    caps = rules.detect_caps(
        {
            "send_message",
            "finish",
            "send_poke",
            "tavily_search",
            "memory_save",
            "get_chat_history",
        },
        {"speech_via_tool": True},
    )
    text = rules.render(
        None,
        {"send_message", "finish", "send_poke", "tavily_search", "memory_save", "get_chat_history"},
        {"speech_via_tool": True},
    )

    assert caps["speech_by_tool"] is True
    assert "你输出的文本只是思考" in text
    assert "send_message" in text
    assert "工具调用是动作" in text
    assert "不要把同一句话拆到两条里" in text
    assert "直接结束" in text, "静默必须是明确允许的正常结局"


def test_speech_channel_falls_back_when_tools_unavailable():
    """模型不支持 tool calling 时，发言通道必须降级回文本，而不是整轮静默。"""
    from app.agent.loop.assistant_output import speech_needs_text_fallback

    assert speech_needs_text_fallback(object(), False) is True
    assert speech_needs_text_fallback(object(), True) is False
    assert speech_needs_text_fallback(None, False) is False
