"""流式发言的 ``message_id`` 必须落在**本轮**的历史条目上。

审查发现的缺陷：纯文本流式路径是"先发后写"——正文分片一句句 yield 出去，
历史要等全部发完才 ``add_message`` 写一条。而 pipeline 在**每片发送成功后**调
``session_mgr.mark_last_assistant_message_id``，它按"最近一条 assistant"定位。
于是分片发送期间"最近一条 assistant"还是**上一轮**的发言：

- 上一轮那条被反复改成这一轮某一片的 message_id（引用指错消息）；
- 本轮发言始终没有句柄 → 群聊环境记录的双源去重失效（同一句话再次以环境视角出现）。

发言通道（speech）本来就是"先写历史再 yield"，这里把纯文本流式对齐：
``upsert_streaming_assistant`` 在发送前插入/更新本轮那一条，发完再定稿。
"""

from __future__ import annotations

import types

import pytest

from app.agent.loop import chat
from app.agent.providers import PROVIDERS
from app.agent.providers.base import BaseProvider, StreamEvent


class _Scripted(BaseProvider):
    async def chat_stream(self, messages, **kwargs):
        yield StreamEvent(type="text", text="第一句。第二句。")
        yield StreamEvent(type="done", finish_reason="stop")


class _Skills:
    def prompt_blocks(self):
        return []


class _Telemetry:
    def record_call_simple(self, **kwargs):
        return None

    def record_tool(self, *args, **kwargs):
        return None


@pytest.fixture
def probe(monkeypatch, tmp_path):
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    monkeypatch.setitem(PROVIDERS, "mid_probe", _Scripted)


def _runtime(bot_id: int):
    from app.agent.session.manager import SessionManager

    rt = types.SimpleNamespace()
    rt.bot_id = bot_id
    rt.config = {
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
    rt.memory = None
    rt.session_mgr = SessionManager(str(bot_id))
    rt.skills = _Skills()
    rt.llm_tools = None
    rt.telemetry = _Telemetry()
    rt.provider_config = lambda: {"api_key": "sk-test", "model": "mid_probe"}
    rt.provider_chain = lambda: [{
        "provider": "mid_probe", "model": "mid_probe", "modalities": ["text", "tool_use"],
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
        message=[MessageSegment("text", {"text": "说两句"})],
        user=UserInfo(user_id=20002, nickname="小明"),
        group=types.SimpleNamespace(group_id=466052056, group_name="测试群"),
    )


def _ctx():
    from app.agent.loop.context import LlmContext

    event = _event()
    event.bot = None
    ctx = LlmContext(event=event, runtime=None, bot=None, session_id="group_466052056")
    ctx.user_text = "说两句"
    ctx.state["user_context"] = {"sent_text": "说两句", "sender": "小明(20002)"}
    ctx.state["is_at"] = True
    return ctx


def _assistant_entries(rt):
    return [
        m for m in rt.session_mgr.get_history("group_466052056", limit=50)
        if m.get("role") == "assistant"
    ]


async def test_streaming_message_id_lands_on_current_turn(probe):
    """pipeline 在发送成功后回填 message_id → 必须补到本轮，而不是上一轮。"""
    rt = _runtime(7701)
    session_id = "group_466052056"
    session = rt.session_mgr.create_session(session_id, "group")
    # 上一轮的发言（带自己的句柄）：不能被本轮的发送回填污染
    rt.session_mgr.add_message(session_id, "assistant", "上一轮我说过的话", message_id="old-id")

    ctx = _ctx()
    seen: list[str] = []
    async for item in chat.stream_response(rt, ctx.event, ctx):
        text = str(item)
        if text:
            seen.append(text)
            # 模拟 pipeline：每片发送成功后立刻回填
            rt.session_mgr.mark_last_assistant_message_id(session_id, f"new-{len(seen)}")

    entries = _assistant_entries(rt)
    # 本轮整段回复只有一条历史，而 QQ 侧是多个气泡 → 句柄最终落在**最后一片**上；
    # 关键是它落在**本轮**这条，上一轮保持自己的 old-id 不被改写。
    assert [e.get("message_id") for e in entries] == ["old-id", "new-2"], (
        f"本轮条目应拿到分片的句柄，上一轮保持自己的 old-id，实际 {entries}"
    )
    assert "第一句" in entries[-1]["content"]
    assert session is not None


async def test_streaming_history_is_single_entry_for_whole_turn(probe):
    """一轮流式只该有一条 assistant 历史（分片是同一句话的碎片，不是多次发言）。"""
    rt = _runtime(7702)
    ctx = _ctx()

    _ = [str(item) async for item in chat.stream_response(rt, ctx.event, ctx)]

    entries = _assistant_entries(rt)
    assert len(entries) == 1, f"一轮只能有一条 assistant 历史，实际 {entries}"
    assert "第一句" in entries[0]["content"] and "第二句" in entries[0]["content"]


async def test_upsert_appends_once_then_updates(probe):
    """upsert 语义：第一次插入，之后只更新同一条（不产生重复条目）。"""
    from app.agent.session.manager import SessionManager

    mgr = SessionManager("7703")
    session_id = "group_upsert"
    mgr.create_session(session_id, "group")

    assert mgr.upsert_streaming_assistant(session_id, "第一句") is True
    assert mgr.upsert_streaming_assistant(session_id, "第一句。第二句") is True

    entries = mgr.get_history(session_id, limit=10)
    assert len(entries) == 1
    assert entries[0]["content"] == "第一句。第二句"

    # 结束标记后，下一轮重新插入新条目
    mgr.end_streaming_assistant(session_id)
    mgr.upsert_streaming_assistant(session_id, "下一轮")
    assert len(mgr.get_history(session_id, limit=10)) == 2


async def test_upsert_skips_junk_and_empty(probe):
    from app.agent.session.manager import SessionManager

    mgr = SessionManager("7704")
    session_id = "group_junk"
    mgr.create_session(session_id, "group")

    assert mgr.upsert_streaming_assistant(session_id, "") is False
    assert mgr.upsert_streaming_assistant(session_id, "   ") is False
    assert mgr.get_history(session_id, limit=10) == []

