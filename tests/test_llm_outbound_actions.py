"""输出侧动作通道测试：[reply] / [@QQ] → 真实消息段。

这类指令是「模型主动性」的能力前提：此前输出是纯文本，模型即使想引用也无从表达。
本文件锁住解析/剥离/段序/上限/流式分片与配置开关，以及**无指令时零行为变化**。
"""

from types import SimpleNamespace

from app.domain.message import Message
from app.llm.actions import (
    OutboundBuilder,
    OutboundDirectives,
    build_outbound_builder,
    parse_outbound_directives,
    strip_outbound_directives,
)

GROUP_EVENT = SimpleNamespace(
    event_type="message_group",
    message_id=99887766,
    group=SimpleNamespace(group_id=778459818),
    user_id=10001,
)
PRIVATE_EVENT = SimpleNamespace(event_type="message_private", message_id=99887766, user_id=10001)


def _types(msg: Message) -> list[str]:
    return [seg.type for seg in msg.segments]


def _seg_data(msg: Message, seg_type: str) -> list[dict]:
    return [seg.data for seg in msg.segments if seg.type == seg_type]


# ---------- 解析 / 剥离 ----------


def test_parse_leading_reply_and_at():
    directives, clean = parse_outbound_directives("[reply][@10001] 你说的对")

    assert directives.reply is True
    assert directives.ats == ["10001"]
    assert clean == "你说的对"


def test_parse_tolerates_whitespace_and_order():
    directives, clean = parse_outbound_directives("  [@10001]\n[reply]  好呀  ")

    assert directives.reply is True and directives.ats == ["10001"]
    assert clean == "好呀"


def test_directives_must_be_at_the_front():
    """正文中间的标记不算指令，但仍会被剥离（绝不漏给用户）。"""
    directives, clean = parse_outbound_directives("你说的对 [@10001]")

    assert not directives
    assert clean == "你说的对"


def test_at_cap_and_dedup():
    directives, _ = parse_outbound_directives(
        "[@10001][@10001][@10002][@10003]", max_at=2
    )
    assert directives.ats == ["10001", "10002"]

    directives, _ = parse_outbound_directives("[@10001][@10002]", max_at=0)
    assert directives.ats == []


def test_invalid_at_is_left_alone():
    directives, clean = parse_outbound_directives("[@abc] 你好")
    assert not directives
    assert clean == "[@abc] 你好"


def test_strip_outbound_directives_removes_anywhere():
    assert strip_outbound_directives("a[reply]b[@10001]c") == "abc"


# ---------- 组装 ----------


def test_assemble_message_order_reply_at_text():
    msg = OutboundBuilder(GROUP_EVENT).build("[reply][@10001] 好呀")

    assert _types(msg) == ["reply", "at", "text"]
    assert _seg_data(msg, "reply") == [{"id": "99887766"}]
    assert _seg_data(msg, "at") == [{"qq": "10001"}]
    assert msg.text == " 好呀".strip() or msg.text.strip() == "好呀"


def test_at_dropped_in_private_but_text_kept():
    msg = OutboundBuilder(PRIVATE_EVENT).build("[reply][@10001] 私聊里 @ 不生效")

    assert _types(msg) == ["reply", "text"]
    assert "10001" not in str(msg.to_onebot())


def test_no_directives_keeps_plain_text_behaviour():
    msg = OutboundBuilder(GROUP_EVENT).build("普通回复")

    assert _types(msg) == ["text"]
    assert msg.text == "普通回复"


def test_disabled_channel_sends_text_verbatim():
    msg = OutboundBuilder(GROUP_EVENT, enable=False).build("[reply] 未开启通道")

    assert _types(msg) == ["text"]
    assert msg.text == "[reply] 未开启通道"


# ---------- 流式分片 ----------


def test_directives_only_in_first_chunk():
    builder = OutboundBuilder(GROUP_EVENT)

    first = builder.build("[reply] 第一句")
    second = builder.build("[@10001] 第二句")  # 已发出引用，后续片不再有指令

    assert _types(first) == ["reply", "text"]
    assert _types(second) == ["text"]
    assert second.text == "第二句"


def test_lone_directive_chunk_carries_over_to_next_chunk():
    """模型单独输出一行 [reply] 时不能发出空消息，指令挂到下一片正文。"""
    builder = OutboundBuilder(GROUP_EVENT)

    assert builder.build("[reply]") is None
    msg = builder.build("接着说话")

    assert _types(msg) == ["reply", "text"]
    assert msg.text == "接着说话"


def test_empty_chunk_returns_none():
    builder = OutboundBuilder(GROUP_EVENT)

    assert builder.build("") is None
    assert builder.build("   ") is None


def test_message_id_missing_means_no_reply_segment():
    event = SimpleNamespace(event_type="message_group", message_id=None, group=SimpleNamespace(group_id=1))
    msg = OutboundBuilder(event).build("[reply] 你好")

    assert _types(msg) == ["text"]


# ---------- 配置工厂 ----------


def test_build_outbound_builder_reads_config():
    assert build_outbound_builder(GROUP_EVENT, {}).enable is True
    assert build_outbound_builder(GROUP_EVENT, {"outbound_directive_enable": False}).enable is False
    assert build_outbound_builder(GROUP_EVENT, {"outbound_directive_max_at": 1}).max_at == 1
    # 脏配置不炸
    assert build_outbound_builder(GROUP_EVENT, {"outbound_directive_max_at": "oops"}).max_at == 3


def test_directives_dataclass_truthiness_and_merge():
    assert not OutboundDirectives()
    assert OutboundDirectives(reply=True)
    assert OutboundDirectives(ats=["1"])

    merged = OutboundDirectives(ats=["1"]).merge(OutboundDirectives(reply=True, ats=["1", "2"]))
    assert merged.reply is True
    assert merged.ats == ["1", "2"]


# ---------- 流水线接入（流式：只有首句能带引用） ----------


class _HooksStub:
    def get(self, stage, event_type):
        return []


class _RuntimeStub:
    bot_id = 1

    def __init__(self):
        self.config = {"stream_send_pool_enabled": False, "outbound_directive_enable": True}
        self.llm_hooks = _HooksStub()
        self.proactive = None


async def test_pipeline_stream_attaches_reply_to_first_sentence_only(monkeypatch):
    from app.llm.context import LlmContext, LlmJob
    from app.llm.pipeline import LlmPipeline

    async def fake_stream(runtime, event, ctx):
        for sentence in ["[reply] 你好。", "第二句。"]:
            yield sentence

    monkeypatch.setattr("app.llm.chat.stream_response", fake_stream)

    pipeline = LlmPipeline(_RuntimeStub())
    sent: list[Message] = []

    async def _capture(ctx, msg):
        sent.append(msg)

    monkeypatch.setattr(pipeline, "_send", _capture)

    event = SimpleNamespace(
        event_type="message_group",
        message_type="group",
        message_id=99887766,
        group=SimpleNamespace(group_id=778459818),
        user_id=10001,
    )
    ctx = LlmContext(
        event=event,
        runtime=pipeline.runtime,
        bot=SimpleNamespace(),
        session_id="group_778459818",
        job=LlmJob(id="job1", group_key="group_778459818"),
    )

    await pipeline._run_stream(ctx)

    assert len(sent) == 2
    assert _types(sent[0]) == ["reply", "text"]
    assert sent[0].text == "你好。"
    assert _types(sent[1]) == ["text"]  # 后续片不再补引用
    assert sent[1].text == "第二句。"

