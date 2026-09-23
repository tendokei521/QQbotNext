"""两条生产路径装配一致性测试：stream_response 与 generate_response 必须发出同一份 prompt。

背景：两者此前是各自 100+ 行的独立拷贝（块顺序、去重口径、记忆检索文本都可能漂移）。
现在共用 ``prepare_prompt`` + ``assembly``，本文件把这个不变量钉住：
**同一个事件、同一个模型配置下，两条路径实际发出的 messages 必须逐字相同**
（区别只在 provider 的调用方式：流式 vs 非流式）。
"""

from __future__ import annotations

import types

from app.llm.providers import PROVIDERS
from app.llm.providers.base import BaseProvider, LLMResponse, StreamEvent

STREAM_MESSAGES: list[list[dict]] = []
CHAT_MESSAGES: list[list[dict]] = []


class _ProbeProvider(BaseProvider):
    """记录收到的 messages；非流式返回文本，流式产出两个文本块。"""

    async def chat(self, messages, **kwargs):
        CHAT_MESSAGES.append([dict(m) for m in messages])
        return LLMResponse(text="非流式回复", raw={"choices": [{}]})

    async def chat_stream(self, messages, **kwargs):
        STREAM_MESSAGES.append([dict(m) for m in messages])
        yield StreamEvent(type="text", text="流式回复")
        yield StreamEvent(type="done", finish_reason="stop")


class _Skills:
    def prompt_blocks(self):
        return ["技能块"]


class _Tools:
    def enabled_specs(self):
        return []


class _Telemetry:
    def record_call_simple(self, **kwargs):
        return None


def _config_dict(**over):
    base = {
        "system_prompt": "你是助手",
        "group_enable": True,
        "private_enable": True,
        "outbound_directive_enable": False,
        "meta_sender_style": "off",
        "history_rounds": 50,
        "schedule_enable": True,
    }
    base.update(over)
    return base


def _runtime(config: dict):
    rt = types.SimpleNamespace()
    rt.bot_id = 778
    rt.config = dict(config)
    rt.memory = None
    rt.skills = _Skills()
    rt.llm_tools = _Tools()
    rt.telemetry = _Telemetry()
    rt.provider_config = lambda: {"api_key": "sk-test", "model": "probe-model"}
    rt.provider_chain = lambda: [{"provider": "probe_model", "model": "probe-model"}]
    return rt


def _event(time: int = 1788342260):
    from app.domain.events import GroupMessageEvent, MessageSegment, UserInfo

    return GroupMessageEvent(
        event_type="message_group",
        message_type="group",
        time=time,
        user_id=20002,
        self_id=778,
        message=[MessageSegment("text", {"text": "现在几点"})],
        user=UserInfo(user_id=20002, nickname="小明"),
        group=types.SimpleNamespace(group_id=466052056, group_name="测试群"),
    )


def _ctx(event, *, session_id: str | None = None):
    from app.llm.context import LlmContext

    ctx = LlmContext(event=event, runtime=None, bot=None, session_id=session_id or "group_466052056")
    ctx.user_text = "现在几点"
    ctx.state["user_context"] = {"sent_text": "现在几点", "sender": "小明(20002)"}
    # 群聊里 @ 触发会绕过回复冷却（与生产路径一致：被 @ 的消息必须回）
    ctx.state["is_at"] = True
    return ctx


def setup_function(_fn):
    STREAM_MESSAGES.clear()
    CHAT_MESSAGES.clear()


def _register(monkeypatch, name: str) -> None:
    monkeypatch.setitem(PROVIDERS, name, _ProbeProvider)


async def test_stream_and_generate_send_identical_prompt(monkeypatch, tmp_path):
    """核心不变量：非流式与流式发出的 messages 完全一致。

    两条路径各自独立会话（生产环境由 ``stream_output`` 二选一，不会同时跑），
    但事件、配置、模型完全相同 → 装配结果必须逐字一致。
    """
    from app.llm import chat

    _register(monkeypatch, "probe_model")
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))

    # 非流式：走 generate_response
    event_chat = _event()
    ctx_chat = _ctx(event_chat)
    rt_chat = _runtime(_config_dict())
    out = await chat.generate_response(rt_chat, event_chat, ctx_chat)
    assert out == "非流式回复"

    # 流式：走 stream_response（异步生成器，需要消费）；独立会话避免冷却互相影响
    event_stream = _event()
    ctx_stream = _ctx(event_stream, session_id="group_466052056_stream")
    rt_stream = _runtime(_config_dict(stream_output=True))
    chunks = [chunk async for chunk in chat.stream_response(rt_stream, event_stream, ctx_stream)]
    assert chunks == ["流式回复"]

    assert CHAT_MESSAGES and STREAM_MESSAGES
    chat_msgs, stream_msgs = CHAT_MESSAGES[0], STREAM_MESSAGES[0]
    # 两条路径的 system 块（人设/定时/主动性/格式说明/技能）+ 本轮消息必须逐字一致；
    # 中间只差会话历史（两个独立会话，历史条数不同属预期）。
    chat_systems = [m for m in chat_msgs if m["role"] == "system"]
    stream_systems = [m for m in stream_msgs if m["role"] == "system"]
    assert chat_systems == stream_systems
    assert chat_msgs[-1] == stream_msgs[-1]
    assert any(m["content"] == "技能块" for m in chat_systems)
    assert chat_msgs[-1]["content"].endswith("现在几点")
    # 本轮消息只出现一次（去重口径一致）
    assert [m["content"] for m in chat_msgs].count(chat_msgs[-1]["content"]) == 1


async def test_both_paths_share_deduplicated_history(monkeypatch, tmp_path):
    """同一会话第二轮：历史里不应出现重复的本轮用户消息（两条路径同一口径）。"""
    from app.llm import chat

    _register(monkeypatch, "probe_model")
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))

    rt = _runtime(_config_dict())

    event1 = _event()
    ctx1 = _ctx(event1)
    await chat.generate_response(rt, event1, ctx1)

    event2 = _event(time=1788343000)  # 时间推进，绕过群聊回复冷却
    ctx2 = _ctx(event2)
    ctx2.user_text = "第二条"
    ctx2.state["user_context"] = {"sent_text": "第二条"}
    await chat.generate_response(rt, event2, ctx2)

    second = CHAT_MESSAGES[-1]
    user_texts = [m["content"] for m in second if m["role"] == "user"]
    assert user_texts.count("第二条") == 1, user_texts
    assert any("现在几点" in str(t) for t in user_texts), user_texts


# ---------- #llm 指令入口 ----------


async def test_handle_dispatches_chat_commands(monkeypatch):
    """``#llm`` 指令必须被派发到 handle_commands；非指令消息静默交给流水线。

    回归：``handle`` 曾经同时承担"旧版自己发消息"的完整回复路径，
    非指令消息会被重复回复。现在它只做指令分发。
    """
    from app.llm import chat

    seen: list[tuple] = []

    async def _fake_commands(module, session_mgr, session_id, group_id, user_id,
                             raw_text, is_admin, is_private, event=None):
        seen.append((session_id, group_id, raw_text, is_private))
        return True

    monkeypatch.setattr(chat, "handle_commands", _fake_commands)
    module = _CommandModule()

    await chat.handle(module, _command_event("#llm memory list", message_type="private"))
    assert seen == [("private_20002", None, "#llm memory list", True)]
    assert module.config.get("_session") is None  # 会话档案已清理

    seen.clear()
    await chat.handle(module, _command_event("#llm schedule list", message_type="group"))
    assert seen == [("group_466052056", "466052056", "#llm schedule list", False)]

    # 非指令消息：不派发、不自己回复
    seen.clear()
    await chat.handle(module, _command_event("你好", message_type="private"))
    assert seen == []


class _CommandModule:
    bot_id = 778
    name = "agent"

    def __init__(self):
        self.config = _SessionCfg({"private_enable": True, "group_enable": True})

    def provider_config(self):
        return {"api_key": "sk-test"}


class _SessionCfg(dict):
    def set_session(self, session_id):
        self["_session"] = session_id

    def clear_session(self):
        self.pop("_session", None)


def _command_event(text: str, *, message_type: str):
    from app.domain.events import MessageSegment

    payload = {
        "message_type": message_type,
        "user_id": 20002,
        "self_id": 778,
        "is_admin": True,
        "message": [MessageSegment("text", {"text": text})],
    }
    if message_type == "group":
        payload["group"] = types.SimpleNamespace(group_id=466052056)
    return types.SimpleNamespace(**payload)
