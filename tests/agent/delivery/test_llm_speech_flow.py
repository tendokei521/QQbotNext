"""发言通道（``speech_via_tool``）在流式路径上的端到端行为。

这一层要钉住四件**改的就是它们**的事：

1. **round 边界 flush**：模型调完 ``send_message``，这一轮工具执行完就真发出去，
   然后它还能继续想下一句（不是"全部结束才发"）；
2. **思考不外泄**：模型输出的文本不再当作发言，一句都不发给用户；
3. **静默合法**：一句话没说 → 不发送、不写 assistant 历史，且不会补兜底话术；
4. **被点名必须回应**：@/私聊场景下静默会触发一次补救轮（防「叫了不理」）。
"""

from __future__ import annotations

import json
import types

from app.agent.providers import PROVIDERS
from app.agent.providers.base import BaseProvider, LLMResponse, StreamEvent

STREAM_MESSAGES: list[list[dict]] = []
CHAT_MESSAGES: list[list[dict]] = []
#: 事件时间线：``round``=又发起了一轮流式请求，``force``=补救轮，
#: ``send:<正文>``=有一条气泡被交给上层发送。用顺序证明"flush 发生在轮边界"。
TIMELINE: list[str] = []

#: 每轮流式脚本文本（按调用次序取，超出则重复最后一个）
STREAM_SCRIPT: list[list[StreamEvent]] = []
#: 补救轮（非流式）要不要替模型调用 send_message
FORCE_SPEAK_TEXT: str | None = None


def _text(value: str) -> StreamEvent:
    return StreamEvent(type="text", text=value)


def _tool_call(name: str, args: str, index: int = 0) -> StreamEvent:
    return StreamEvent(
        type="tool_call",
        tool_call={"index": index, "id": f"c{index}", "function": {"name": name, "arguments": args}},
    )


class _SpeechProbe(BaseProvider):
    """按脚本产出流式事件；非流式只在补救轮里被调用。

    补救轮走的是 ``chat_with_fallback`` 的兼容壳：适配器只发单次请求、把
    ``send_message`` 作为 ``tool_calls`` 交出去，由共享运行器执行（P1 收口）。
    所以这里第二轮（messages 尾部已是 role=tool）要返回"无工具调用"来收尾。
    """

    async def chat(self, messages, **kwargs):
        CHAT_MESSAGES.append([dict(m) for m in messages])
        called_tools = any(m.get("role") == "tool" for m in messages)
        if FORCE_SPEAK_TEXT and not called_tools:
            TIMELINE.append("force")
            return LLMResponse(text="", raw={}, tool_calls=[{
                "id": "c_force",
                "type": "function",
                "function": {
                    "name": "send_message",
                    "arguments": json.dumps({"messages": FORCE_SPEAK_TEXT}, ensure_ascii=False),
                },
            }])
        return LLMResponse(text="", raw={"choices": [{}]})

    async def chat_stream(self, messages, **kwargs):
        STREAM_MESSAGES.append([dict(m) for m in messages])
        TIMELINE.append("round")
        index = min(len(STREAM_MESSAGES) - 1, len(STREAM_SCRIPT) - 1)
        for event in STREAM_SCRIPT[index]:
            yield event
        yield StreamEvent(type="done", finish_reason="stop")


class _Skills:
    def prompt_blocks(self):
        return []


class _Tools:
    def enabled_specs(self):
        return []


class _Telemetry:
    def record_call_simple(self, **kwargs):
        return None

    def record_tool(self, *args, **kwargs):
        return None


def _config(**over):
    base = {
        "system_prompt": "你是助手",
        "group_enable": True,
        "private_enable": True,
        "meta_sender_style": "off",
        "history_rounds": 50,
        "schedule_enable": True,
        "assembly_profile": "qq_agent",
        "speech_via_tool": True,
        "stream_output": True,
        "max_tool_rounds": 5,
    }
    base.update(over)
    return base


def _runtime(bot_id: int, config: dict):
    from app.agent.session.manager import SessionManager

    rt = types.SimpleNamespace()
    rt.bot_id = bot_id
    rt.config = dict(config)
    rt.memory = None
    # 生产运行时带 session_mgr：发送成功后 pipeline 靠它回填 assistant 历史的 message_id
    rt.session_mgr = SessionManager(str(bot_id))
    rt.skills = _Skills()
    rt.llm_tools = _Tools()
    rt.telemetry = _Telemetry()
    rt.provider_config = lambda: {"api_key": "sk-test", "model": "speech_probe"}
    rt.provider_chain = lambda: [{"provider": "speech_probe", "model": "speech_probe"}]
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


def _ctx(event, *, is_at: bool = True):
    from app.agent.loop.context import LlmContext

    ctx = LlmContext(event=event, runtime=None, bot=None, session_id="group_466052056")
    ctx.user_text = "在吗"
    ctx.state["user_context"] = {"sent_text": "在吗", "sender": "小明(20002)"}
    ctx.state["is_at"] = is_at
    return ctx


def _assistant_history(bot_id: int) -> list[str]:
    from app.agent.session.manager import SessionManager

    mgr = SessionManager(str(bot_id))
    return [
        str(item.get("content") or "")
        for item in mgr.get_history("group_466052056", limit=50)
        if item.get("role") == "assistant"
    ]


def setup_function(_fn):
    STREAM_MESSAGES.clear()
    CHAT_MESSAGES.clear()
    TIMELINE.clear()
    STREAM_SCRIPT.clear()
    global FORCE_SPEAK_TEXT
    FORCE_SPEAK_TEXT = None


def _register(monkeypatch):
    monkeypatch.setitem(PROVIDERS, "speech_probe", _SpeechProbe)


async def _run_stream(rt, ctx) -> list[str]:
    """跑一轮 stream_response，返回真正被交给"发送"的正文。"""
    from app.agent.loop import chat

    sent: list[str] = []
    async for item in chat.stream_response(rt, ctx.event, ctx):
        text = getattr(item, "text", None)
        if text is None:
            text = str(item)
        sent.append(text)
        TIMELINE.append(f"send:{text}")
    return sent


# ---------- 用例 ----------


async def test_flush_happens_at_round_boundary(monkeypatch, tmp_path):
    """模型调 send_message 后立刻发出，然后才进入下一轮思考。"""
    _register(monkeypatch)
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    STREAM_SCRIPT.extend([
        [
            _text("（看看他问什么）"),
            _tool_call("send_message", '{"messages": ["第一句", "第二句"]}'),
        ],
        [_text("（说完了，不用再补）")],
    ])
    rt = _runtime(991, _config())
    ctx = _ctx(_event())

    sent = await _run_stream(rt, ctx)

    assert sent == ["第一句", "第二句"], "思考文本不应进入发送队列"
    assert TIMELINE == ["round", "send:第一句", "send:第二句", "round"]
    # 每发一条气泡写一条 assistant 历史（不是整轮合并成一条）
    assert _assistant_history(991) == ["第一句", "第二句"]
    assert ctx.state["turn_reason"] == "completed", "结构化结局要能被调用方读到"


async def test_silence_sends_nothing_and_writes_no_history(monkeypatch, tmp_path):
    """一句话没说 = 静默：不发送、不写历史、不补兜底话术。"""
    _register(monkeypatch)
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    STREAM_SCRIPT.append([_text("（他们在聊游戏，我没兴趣）"), _tool_call("finish", '{"summary": "没兴趣"}')])
    rt = _runtime(992, _config())
    ctx = _ctx(_event(), is_at=False)

    sent = await _run_stream(rt, ctx)

    assert sent == []
    assert ctx.state.get("silent") is True
    assert ctx.state["turn_reason"] == "silent"
    assert _assistant_history(992) == [], "静默不该留下 assistant 历史"


async def test_at_trigger_forces_one_more_round(monkeypatch, tmp_path):
    """被 @ 却静默：补一轮要求它发言（防「叫了不理」）。"""
    _register(monkeypatch)
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    global FORCE_SPEAK_TEXT
    FORCE_SPEAK_TEXT = "干嘛"
    STREAM_SCRIPT.append([_text("（懒得理）")])
    rt = _runtime(993, _config())
    ctx = _ctx(_event(), is_at=True)

    sent = await _run_stream(rt, ctx)

    assert sent == ["干嘛"]
    assert TIMELINE == ["round", "force", "send:干嘛"]
    assert CHAT_MESSAGES and CHAT_MESSAGES[0][-1]["role"] == "user"
    # 措辞必须带"文本不会发出去"这个因果：实测纯祈使句（"你必须调用"）反而无效
    assert "文本不会发出去" in str(CHAT_MESSAGES[0][-1]["content"])
    assert "send_message" in str(CHAT_MESSAGES[0][-1]["content"])
    assert _assistant_history(993) == ["干嘛"]


async def test_force_reply_can_be_disabled(monkeypatch, tmp_path):
    """关掉「被点名必须回应」后，@ 也允许静默。"""
    _register(monkeypatch)
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    STREAM_SCRIPT.append([_text("（不想说话）")])
    rt = _runtime(994, _config(speech_force_reply_on_call=False))
    ctx = _ctx(_event(), is_at=True)

    sent = await _run_stream(rt, ctx)

    assert sent == []
    assert CHAT_MESSAGES == []


async def test_reminder_injected_when_no_speech_yet(monkeypatch, tmp_path):
    """剩余轮次不多且还没开口 → 注入一条 user 角色的【系统提醒】。"""
    _register(monkeypatch)
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    STREAM_SCRIPT.extend([
        [_tool_call("expand_recent", "{}")],
        [_text("（还是不说）")],
    ])
    rt = _runtime(995, _config(max_tool_rounds=4, speech_reminder_rounds=3))
    ctx = _ctx(_event(), is_at=False)

    await _run_stream(rt, ctx)

    assert len(STREAM_MESSAGES) >= 2, "提醒要出现在下一轮请求里"
    reminder = [m for m in STREAM_MESSAGES[1] if m["role"] == "user"]
    assert reminder, "提醒必须是 user 角色（system 会被适配器上提到最前，等于没加）"
    assert "【系统提醒】" in str(reminder[-1]["content"])
    assert "send_message" in str(reminder[-1]["content"])


async def test_speech_tools_are_injected_into_request(monkeypatch, tmp_path):
    """发言通道开启时，send_message / finish 必须真的出现在工具表里。"""
    _register(monkeypatch)
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    STREAM_SCRIPT.append([_text("（想）")])
    rt = _runtime(996, _config(speech_force_reply_on_call=False))
    ctx = _ctx(_event(), is_at=False)

    await _run_stream(rt, ctx)

    from app.agent.loop.chat import prepare_prompt

    req, _session, meta = await prepare_prompt(rt, ctx.event, ctx)
    names = {spec.name for spec in meta["all_specs"]}
    assert {"send_message", "finish"} <= names
    assert meta["speech"] is not None
    assert {"send_message", "finish"} <= req.available_tools


async def test_speech_off_keeps_text_as_speech(monkeypatch, tmp_path):
    """没开发言通道时行为逐字不变：文本照发、写一条 assistant 历史。"""
    _register(monkeypatch)
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    STREAM_SCRIPT.append([_text("就这样吧。")])
    rt = _runtime(997, _config(assembly_profile="legacy", speech_via_tool=False))
    ctx = _ctx(_event())

    sent = await _run_stream(rt, ctx)

    assert sent == ["就这样吧。"]
    assert _assistant_history(997) == ["就这样吧。"]


class _FakeBot:
    """最小的发送接口：记录每条待发消息，并回一个 message_id。"""

    def __init__(self):
        self.sent: list = []

    async def send_group_msg(self, group_id, message):
        self.sent.append(message)
        return {"status": "ok", "data": {"message_id": 9000 + len(self.sent)}}

    async def send_private_msg(self, user_id, message):
        self.sent.append(message)
        return {"status": "ok", "data": {"message_id": 9000 + len(self.sent)}}


async def test_pipeline_sends_bubbles_and_backfills_history_id(monkeypatch, tmp_path):
    """走真正的 pipeline 出口：气泡变成真消息，发送后 message_id 补到对应那条历史上。

    这条用例守的是"发言通道没有绕开原有发送路径"——pre_send/post_send 钩子、
    group_log 的「我的发言」入账、发送间隔策略都挂在 ``_send`` 上，
    任何自建发送实现都会漏掉其中一半。
    """
    from app.agent.loop.pipeline import LlmPipeline
    from app.domain.message import Message

    _register(monkeypatch)
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    STREAM_SCRIPT.extend([
        [
            _text("（先回一句）"),
            _tool_call("send_message", '{"messages": ["第一句", "第二句"], "reply_to_message_id": "22350"}'),
        ],
        [_text("（说完）")],
    ])
    rt = _runtime(998, _config())
    event = _event()
    ctx = _ctx(event)
    bot = _FakeBot()
    ctx.bot = bot

    await LlmPipeline(rt)._run_stream(ctx)

    assert len(bot.sent) == 2, "两条气泡应各自成为一条真消息"
    assert all(isinstance(m, Message) for m in bot.sent)
    assert [seg.type for seg in bot.sent[0].segments] == ["reply", "text"]
    assert bot.sent[0].segments[0].data["id"] == "22350"
    assert [m.text for m in bot.sent] == ["第一句", "第二句"]

    from app.agent.session.manager import SessionManager

    entries = [
        item
        for item in SessionManager("998").get_history("group_466052056", limit=50)
        if item.get("role") == "assistant"
    ]
    assert [e.get("content") for e in entries] == ["第一句", "第二句"]
    assert [str(e.get("message_id")) for e in entries] == ["9001", "9002"], (
        "每条气泡的历史都要拿到自己那条消息的 id（顺序反了会落到上一条上）"
    )
