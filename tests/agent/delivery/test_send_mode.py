"""发言方式契约：输出方式（传输）与发言方式（投递）**正交**。

锁三件事（都是用户能感知的对外行为）：

1. 解析矩阵：显式三种发言方式 + ``auto`` 跟随传输 + 旧「消息池」总闸迁移；
2. 非流式 + ``split``：整段拿到后按换行/句末标点拆开、按节奏逐条发；
3. 流式 + ``whole``：边收边攒，结束只发一条，引用段只挂一次。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agent.delivery.output import BlockingOutput, StreamOutput
from app.agent.delivery.send_mode import resolve_send_mode
from app.domain.message import Message


class _Config:
    def __init__(self, **values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


def _event() -> SimpleNamespace:
    return SimpleNamespace(
        event_type="message_group",
        message_type="group",
        group=SimpleNamespace(group_id=1),
        user_id=2,
        message_id=99,
    )


def _ctx(runtime, event=None) -> SimpleNamespace:
    return SimpleNamespace(
        runtime=runtime,
        event=event or _event(),
        state={},
        session_id="group_1",
        job=None,
        response_text="",
        response_messages=[],
    )


def _runtime(**config) -> SimpleNamespace:
    return SimpleNamespace(bot_id="1", config=_Config(**config))


# ---------- 1. 解析矩阵 ----------


def test_resolve_send_mode_matrix():
    # 显式选择：非流式也支持拆分，realtime 在非流式下退化为整段
    assert resolve_send_mode(_Config(reply_send_mode="split"), transport_stream=False) == "split"
    assert resolve_send_mode(_Config(reply_send_mode="whole"), transport_stream=True) == "whole"
    assert resolve_send_mode(_Config(reply_send_mode="realtime"), transport_stream=False) == "whole"
    # auto 跟随传输方式（默认口径 = 改造前行为）
    assert resolve_send_mode(_Config(), transport_stream=True) == "realtime"
    assert resolve_send_mode(_Config(), transport_stream=False) == "whole"
    # 旧「消息池」开关在 auto 下迁移为拆分节奏，否则升级会静默丢掉节奏
    assert resolve_send_mode(_Config(stream_send_pool_enabled=True), transport_stream=True) == "split"
    assert resolve_send_mode(_Config(stream_send_pool_enabled=True), transport_stream=False) == "split"
    # 非法值按 auto（与配置自愈同口径）
    assert resolve_send_mode(_Config(reply_send_mode="nonsense"), transport_stream=False) == "whole"


# ---------- 2. 非流式 + split ----------


@pytest.mark.asyncio
async def test_blocking_split_sends_newline_chunks_with_pacing(monkeypatch):
    """非流式也能「分几条说」：按换行/句末标点拆开，首条不等待，之后按节奏。"""
    from app.agent.loop import generate as generate_mod

    async def fake_generate(runtime, event, ctx=None, **_kwargs):
        return "[reply]第一行\n第二行。第三句。"

    monkeypatch.setattr(generate_mod, "generate_response", fake_generate)
    monkeypatch.setattr("app.agent.hooks.run_stage", _always_true)

    waits: list[float] = []

    async def fake_sleep(delay):
        waits.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    cfg = _Config(
        reply_send_mode="split",
        stream_send_interval_mode="fixed",
        stream_send_interval_base_ms=600,
        stream_send_interval_min_ms=100,
        stream_send_max_queue=10,
        stream_send_prefix="「",
        stream_send_suffix="」",
        stream_sentence_max_length=200,
        max_message_length=200,
    )
    runtime = _runtime(**cfg._values)
    ctx = _ctx(runtime)

    messages = [msg async for msg in BlockingOutput(runtime, cfg).respond(ctx)]

    assert [msg.text for msg in messages] == ["「第一行」", "「第二行。」", "「第三句。」"]
    # 引用段只挂在第一条上
    assert messages[0].segments[0].type == "reply"
    assert all(seg.type != "reply" for msg in messages[1:] for seg in msg.segments)
    # 节奏：首条立即发，之后每条等一次（固定间隔 600ms）
    assert waits == pytest.approx([0.6, 0.6])


async def _always_true(*_args, **_kwargs):
    return True


# ---------- 3. 流式 + whole / split ----------


@pytest.mark.asyncio
async def test_stream_whole_buffers_until_end_and_parses_directives_once(monkeypatch):
    from app.agent.loop import chat as chat_mod

    async def fake_stream(runtime, event, ctx=None):
        yield "[reply]第一句。"
        yield "第二句。"

    monkeypatch.setattr(chat_mod, "stream_response", fake_stream)

    cfg = _Config(reply_send_mode="whole")
    runtime = _runtime(**cfg._values)
    messages = [msg async for msg in StreamOutput(runtime, cfg).respond(_ctx(runtime))]

    assert len(messages) == 1, "整段发送必须只产出一条消息"
    assert messages[0].text == "第一句。第二句。"
    assert sum(seg.type == "reply" for seg in messages[0].segments) == 1


@pytest.mark.asyncio
async def test_stream_split_sends_each_sentence_through_pool(monkeypatch):
    from app.agent.loop import chat as chat_mod

    async def fake_stream(runtime, event, ctx=None):
        yield "第一句。"
        yield "第二句。"

    monkeypatch.setattr(chat_mod, "stream_response", fake_stream)

    cfg = _Config(
        reply_send_mode="split",
        stream_send_interval_mode="none",
        stream_send_prefix="·",
        stream_sentence_max_length=200,
    )
    runtime = _runtime(**cfg._values)
    messages = [msg async for msg in StreamOutput(runtime, cfg).respond(_ctx(runtime))]

    assert [msg.text for msg in messages] == ["·第一句。", "·第二句。"]
    assert all(isinstance(msg, Message) for msg in messages)
