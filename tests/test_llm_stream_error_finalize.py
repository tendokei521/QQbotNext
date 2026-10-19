"""流式请求失败时，这一轮的收尾必须照常发生。

审查发现的缺陷（``chat.stream_response`` 的 ``if stream_error: ... return``）：

1. **已发给用户的正文不进历史**。流式是边产边发，用户可能已经看到整段回答，
   但 ``return`` 跳过了 ``session_mgr.add_message`` / ``save_session`` ——
   下一轮上下文里"我刚说过的话"不存在，用户一追问模型就装傻。
2. **跳过 mark_replied**：群里被当成"没回复过"，可能被再次触发。
3. **跳过记忆蒸馏与遥测落库**：失败轮的经验永远进不了长期记忆，
   而遥测只在错误分支里记一次（成功路径的分支根本没走到）。
4. 发言通道下兜底话术是**纯文本 yield**，且没写进历史（"发出去了但没记录"）。

修法：错误分支不再 ``return``，而是 ``break`` 出工具轮循环，统一走收尾逻辑
（写历史 / mark_replied / 记忆蒸馏 / 遥测 / token usage）；兜底话术与"是否静默"
按有没有流式失败分别处理。
"""

from __future__ import annotations

import types

import pytest

from app.llm import chat
from app.llm.providers import PROVIDERS
from app.llm.providers.base import BaseProvider, StreamEvent

STREAMS: list[list[StreamEvent]] = []


class _Scripted(BaseProvider):
    async def chat_stream(self, messages, **kwargs):
        script = STREAMS[min(len(STREAMS) - 1, 0)] if STREAMS else []
        for event in script:
            yield event


class _Skills:
    def prompt_blocks(self):
        return []


class _Telemetry:
    def __init__(self):
        self.calls: list[dict] = []

    def record_call_simple(self, **kwargs):
        self.calls.append(kwargs)

    def record_tool(self, *args, **kwargs):
        return None


@pytest.fixture(autouse=True)
def _reset():
    STREAMS.clear()
    yield
    STREAMS.clear()


@pytest.fixture
def probe(monkeypatch, tmp_path):
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    monkeypatch.setitem(PROVIDERS, "err_probe", _Scripted)


def _runtime(bot_id: int, config: dict):
    from app.llm.session import SessionManager

    rt = types.SimpleNamespace()
    rt.bot_id = bot_id
    rt.config = dict(config)
    rt.memory = None
    rt.session_mgr = SessionManager(str(bot_id))
    rt.skills = _Skills()
    rt.llm_tools = None
    rt.telemetry = _Telemetry()
    rt.provider_config = lambda: {"api_key": "sk-test", "model": "err_probe"}
    rt.provider_chain = lambda: [{
        "provider": "err_probe", "model": "err_probe", "modalities": ["text", "tool_use"],
    }]
    return rt


def _event():
    from app.domain.events import GroupMessageEvent, MessageSegment, UserInfo

    return GroupMessageEvent(
        event_type="message_group",
        message_type="group",
        time=1788342260,
        user_id=20002,
        self_id=778,
        message=[MessageSegment("text", {"text": "在吗"})],
        user=UserInfo(user_id=20002, nickname="小明"),
        group=types.SimpleNamespace(group_id=466052056, group_name="测试群"),
    )


def _ctx():
    from app.llm.context import LlmContext

    event = _event()
    ctx = LlmContext(event=event, runtime=None, bot=None, session_id="group_466052056")
    ctx.user_text = "在吗"
    ctx.state["user_context"] = {"sent_text": "在吗", "sender": "小明(20002)"}
    ctx.state["is_at"] = True
    return ctx


def _config(**over):
    base = {
        "system_prompt": "你是助手",
        "group_enable": True,
        "private_enable": True,
        "meta_sender_style": "off",
        "history_rounds": 50,
        "stream_output": True,
        "max_tool_rounds": 3,
        "max_tokens": 1024,
        "context_expand_enable": True,
    }
    base.update(over)
    return base


def _history_texts(rt) -> list[str]:
    """本轮写进历史的所有 assistant 文本（含兜底话术）。"""
    return [
        str(m.get("content") or "")
        for m in rt.session_mgr.get_history("group_466052056", limit=50)
        if m.get("role") == "assistant"
    ]


async def test_partial_text_then_error_is_written_to_history(probe):
    """半句已发给用户后断流：这半句必须进历史（否则下一轮"我没说过"）。"""
    STREAMS.append([
        StreamEvent(type="text", text="我觉得可以。"),
        StreamEvent(type="error", text="midstream boom"),
    ])
    rt = _runtime(6601, _config())
    ctx = _ctx()

    out = [str(item) async for item in chat.stream_response(rt, ctx.event, ctx)]

    assert "我觉得可以。" in "".join(out)
    texts = _history_texts(rt)
    assert any("我觉得可以" in t for t in texts), f"已发送的正文必须进历史，实际 {texts}"


async def test_error_without_text_still_writes_fallback_and_marks_replied(probe):
    """一个字都没产出就失败：兜底话术照发，且同样进历史。"""
    STREAMS.append([StreamEvent(type="error", text="HTTP 401: bad key")])
    rt = _runtime(6602, _config())
    ctx = _ctx()

    out = [str(item) async for item in chat.stream_response(rt, ctx.event, ctx)]

    joined = "".join(out)
    assert "抱歉" in joined
    assert any("抱歉" in t for t in _history_texts(rt))


async def test_error_still_records_telemetry(probe):
    """失败轮也要落遥测（此前只在错误的 return 分支里记，成功路径的另一半数据全丢）。"""
    STREAMS.append([StreamEvent(type="error", text="HTTP 500")])
    rt = _runtime(6603, _config())
    ctx = _ctx()

    _ = [str(item) async for item in chat.stream_response(rt, ctx.event, ctx)]

    assert rt.telemetry.calls, "失败轮必须有遥测记录"
    assert rt.telemetry.calls[0].get("success") is False


async def test_speech_fallback_is_written_to_history(probe):
    """发言通道开兜底时，兜底话术发出去就必须进历史。"""
    STREAMS.append([StreamEvent(type="error", text="HTTP 500")])
    rt = _runtime(6604, _config(assembly_profile="qq_agent", speech_via_tool=True, speech_fallback_enable=True))
    ctx = _ctx()

    out = [str(item) async for item in chat.stream_response(rt, ctx.event, ctx)]

    assert any("抱歉" in str(item) for item in out)
    assert any("抱歉" in t for t in _history_texts(rt)), "兜底话术已发出，必须进历史"


async def test_error_does_not_force_speak_again(probe):
    """流式已失败就不该再补一轮请求（认证/配额类错误再打一枪只是再烧钱）。"""
    STREAMS.append([StreamEvent(type="error", text="HTTP 401")])
    rt = _runtime(6605, _config(assembly_profile="qq_agent", speech_via_tool=True))
    ctx = _ctx()
    forced = {"n": 0}

    async def _no_force(*args, **kwargs):
        forced["n"] += 1

    import app.llm.chat as chat_mod

    original = chat_mod._force_speak_round
    chat_mod._force_speak_round = _no_force
    try:
        _ = [str(item) async for item in chat.stream_response(rt, ctx.event, ctx)]
    finally:
        chat_mod._force_speak_round = original

    assert forced["n"] == 0

