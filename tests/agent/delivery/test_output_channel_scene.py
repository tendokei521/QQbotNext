"""输出通道的场景契约：participation / 发言通道 **只属于群聊**。

背景（2026-11-07）：私信被账号级 `reply_mode=participation` 带进了发言通道
（文本=思考，发言靠 send_message）。参与度的观察/档位/预算/冷却全在群聊路径，
私聊只继承了副作用；非流式发送又走了自建的 `deliver_now`，最终把私信回复
发进了「群 0」。现在把场景维度收到取用口：

- 私聊永远普通输出（文本即回复），流式与否只由 `stream_output` 决定；
- 发言协议 / send_message+finish 工具 / 思考通道 都不出现在私聊；
- 群聊 participation 行为不变。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.delivery.output import (
    BlockingOutput,
    SpeechOutput,
    StreamOutput,
    select_default_output,
)
from app.agent.delivery.speech import speech_via_tool
from app.agent.loop.assistant_output import _build_speech
from app.domain.events import PrivateMessageEvent


def _private_ctx(*, stream: bool = False) -> SimpleNamespace:
    event = PrivateMessageEvent(
        event_type="message_private", message_type="private", user_id=1901691195, self_id=1
    )
    return SimpleNamespace(
        event=event,
        session_id="private_1901691195",
        state={"is_private": True},
        runtime=None,
    )


def _group_ctx(*, stream: bool = False) -> SimpleNamespace:
    event = SimpleNamespace(
        event_type="message_group",
        message_type="group",
        group=SimpleNamespace(group_id=466052056),
        user_id=20002,
    )
    return SimpleNamespace(
        event=event,
        session_id="group_466052056",
        state={"is_private": False},
        runtime=None,
    )


def _participation(stream_output: bool = False) -> dict:
    return {"reply_mode": "participation", "stream_output": stream_output}


def test_speech_channel_is_group_only():
    cfg = _participation()
    assert speech_via_tool(cfg) is True
    assert speech_via_tool(cfg, is_private=True) is False
    assert speech_via_tool({"reply_mode": "legacy"}, is_private=False) is False


def test_private_uses_plain_output_even_when_account_is_participation():
    cfg = _participation(stream_output=False)
    strategy = select_default_output(cfg, None, _private_ctx())
    assert isinstance(strategy, BlockingOutput)
    assert not isinstance(strategy, SpeechOutput)


def test_private_keeps_streaming_transport_when_configured():
    cfg = _participation(stream_output=True)
    strategy = select_default_output(cfg, None, _private_ctx())
    assert isinstance(strategy, StreamOutput)
    assert not isinstance(strategy, SpeechOutput)


def test_group_still_uses_speech_channel_for_participation():
    cfg = _participation(stream_output=False)
    strategy = select_default_output(cfg, None, _group_ctx())
    assert isinstance(strategy, SpeechOutput)
    assert strategy.stream is False


def test_private_turn_never_builds_speech_outbox():
    cfg = _participation()
    assert _build_speech(cfg, force=True, is_private=True) is None
    assert _build_speech(cfg, force=False, is_private=False) is not None


def test_speech_protocol_not_attached_in_private():
    from app.agent.assembly.assembler import apply_speech_protocol
    from app.agent.prompts.system import SPEECH_PROTOCOL

    messages = [{"role": "user", "content": "你好"}]
    private_req = SimpleNamespace(
        is_private=True, config=_participation(), ctx=None
    )
    assert apply_speech_protocol([dict(m) for m in messages], private_req) == messages

    group_req = SimpleNamespace(is_private=False, config=_participation(), ctx=None)
    decorated = apply_speech_protocol([dict(m) for m in messages], group_req)
    assert decorated[0]["content"].endswith(SPEECH_PROTOCOL)


def test_caps_mark_private_as_plain_output():
    from app.agent.persona.rules import detect_caps

    private_caps = detect_caps(config=_participation(), is_private=True)
    assert private_caps["speech_by_tool"] is False
    assert private_caps["is_private"] is True

    group_caps = detect_caps(config=_participation(), is_private=False)
    assert group_caps["speech_by_tool"] is True
    assert group_caps["is_private"] is False
