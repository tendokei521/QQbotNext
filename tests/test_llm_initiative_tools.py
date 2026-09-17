"""主动消息 / 定时任务路径的工具与提示块测试（P4）。

问题背景：这两条路径此前**完全不传 tools**，也没有「主动性」提示块——模型只能看到会话
历史，拿不到群名、成员信息，也无法展开记录里的 @ / 引用。结果是"主动发言"和"定时提醒"
成了框架里唯一没有环境感知能力的入口。

本文件锁住：
1. build_initiative_tools 在没有触发事件时也能正确构造 ToolContext（会话目标显式给出）；
2. 工具集里包含会话/环境类工具，但不包含 schedule_task（这两条路径自身不需要再建定时）；
3. 主动性提示块进得了消息，且按能力裁剪；
4. 流式主动发送支持工具循环（工具执行 → 回填 → 继续生成）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.llm import initiative_stream
from app.llm.chat import build_initiative_tools
from app.llm.providers.base import StreamEvent


class _Bot:
    def __init__(self):
        self.sent: list[tuple] = []

    async def send_group_msg(self, group_id, message):
        self.sent.append(("g", group_id, getattr(message, "text", message)))

    async def send_private_msg(self, user_id, message):
        self.sent.append(("p", user_id, getattr(message, "text", message)))


def _runtime(config=None, **over):
    base = dict(
        bot_id="10001",
        config=config if config is not None else {},
        llm_tools=None,
        skills=None,
        memory=None,
        knowledge=None,
        mcp_manager=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


# ---------- 工具与提示块收集 ----------


async def test_initiative_tools_include_context_tools_without_event():
    bot = _Bot()
    runtime = _runtime()

    specs, skill_blocks, ctx, instruction = await build_initiative_tools(
        runtime, "group_778", False, bot=bot, group_id=778
    )
    names = {s.name for s in specs}

    assert {"get_current_session", "get_chat_history", "expand_context"} <= names
    # 主动消息/定时任务自身不需要再创建定时任务
    assert "schedule_task" not in names
    assert ctx.event is None
    assert ctx.bot is bot
    assert ctx.session_id == "group_778"
    assert ctx.group_id == 778
    assert skill_blocks == []
    assert instruction and "先自己取" in instruction


async def test_initiative_tools_private_session_maps_user_id():
    runtime = _runtime()

    _specs, _skills, ctx, _instr = await build_initiative_tools(
        runtime, "private_20002", True, bot=_Bot(), user_id=20002
    )

    assert ctx.user_id == 20002
    assert ctx.group_id is None


async def test_initiative_tools_respect_context_expand_switch():
    runtime = _runtime({"context_expand_enable": False})

    specs, _s, _c, instruction = await build_initiative_tools(
        runtime, "group_778", False, bot=_Bot(), group_id=778
    )
    names = {s.name for s in specs}

    assert "expand_context" not in names
    assert "get_current_session" in names
    assert instruction and "先自己取" in instruction
    assert "【未展开" not in instruction


async def test_initiative_instruction_can_be_disabled():
    runtime = _runtime({"proactive_prompt_enable": False})

    _s, _sk, _c, instruction = await build_initiative_tools(
        runtime, "group_778", False, bot=_Bot(), group_id=778
    )

    assert instruction is None


# ---------- 流式主动发送的工具循环 ----------


def _stream_stub(rounds: list[list[StreamEvent]], seen: dict):
    """构造一个假的流式请求函数：按轮次产出预置事件。"""

    async def _fake(chain, messages, *, model=None, temperature=0.7, max_tokens=1024,
                    timeout=30, tools=None, tool_executor=None):
        seen.setdefault("tools", tools)
        seen["rounds"] = seen.get("rounds", 0) + 1
        index = min(seen["rounds"] - 1, len(rounds) - 1)
        for ev in rounds[index]:
            yield ev

    return _fake


async def test_stream_initiative_runs_tool_loop(monkeypatch):
    bot = _Bot()
    runtime = _runtime(provider_chain=lambda: [{"provider": "openai", "model": "m"}])
    executed: list[tuple] = []

    async def _executor(name, args):
        executed.append((name, args))
        return "【用户 123】昵称：张三；relation：当前消息 @ 的对象"

    rounds = [
        [StreamEvent(type="tool_call", tool_call={
            "index": 0, "id": "c1",
            "function": {"name": "expand_context", "arguments": '{"users": [123]}'},
        })],
        [StreamEvent(type="text", text="大家好呀。")],
    ]
    seen: dict = {}
    monkeypatch.setattr(initiative_stream, "iter_stream_with_fallback", _stream_stub(rounds, seen))

    messages = [{"role": "system", "content": "你是助手"}, {"role": "user", "content": "开口"}]
    full = await initiative_stream.stream_send_initiative(
        runtime,
        bot,
        "group_778",
        True,
        778,
        messages,
        tools=[{"type": "function", "function": {"name": "expand_context", "parameters": {}}}],
        tool_executor=_executor,
    )

    assert executed == [("expand_context", {"users": [123]})]
    assert seen["rounds"] == 2
    assert "大家好呀" in full
    assert bot.sent == [("g", 778, "大家好呀。")]
    # 工具结果已回填进对话（assistant tool_calls + role=tool）
    roles = [m["role"] for m in messages]
    assert roles[-2:] == ["assistant", "tool"]
    assert messages[-1]["tool_call_id"] == "c1"


async def test_stream_initiative_without_tools_single_round(monkeypatch):
    bot = _Bot()
    runtime = _runtime(provider_chain=lambda: [{"provider": "openai"}])
    rounds = [[StreamEvent(type="text", text="在吗？")]]
    seen: dict = {}
    monkeypatch.setattr(initiative_stream, "iter_stream_with_fallback", _stream_stub(rounds, seen))

    full = await initiative_stream.stream_send_initiative(
        runtime, bot, "private_20002", False, 20002, [{"role": "user", "content": "开口"}]
    )

    assert seen["rounds"] == 1
    assert full == "在吗？"
    assert bot.sent == [("p", 20002, "在吗？")]


async def test_stream_initiative_stops_at_round_limit(monkeypatch):
    """模型一直要求工具时必须在轮数上限停住，不能无限循环。"""
    bot = _Bot()
    runtime = _runtime(provider_chain=lambda: [{"provider": "openai"}])
    tool_round = [StreamEvent(type="tool_call", tool_call={
        "index": 0, "id": "c", "function": {"name": "expand_context", "arguments": "{}"},
    })]
    seen: dict = {}
    monkeypatch.setattr(initiative_stream, "iter_stream_with_fallback", _stream_stub([tool_round], seen))

    async def _executor(name, args):
        return "ok"

    await initiative_stream.stream_send_initiative(
        runtime, bot, "group_778", True, 778, [{"role": "user", "content": "x"}],
        tools=[{"type": "function", "function": {"name": "expand_context", "parameters": {}}}],
        tool_executor=_executor,
        max_tool_rounds=3,
    )

    assert seen["rounds"] == 3


# ---------- 定时任务路径 ----------


class _FakeBot2(_Bot):
    async def get_group_member_info(self, group_id, user_id):
        return {"status": "ok", "data": {"nickname": "张三"}}


class _FakeConfig:
    def __init__(self, cfg):
        self._cfg = cfg

    def get(self, key, default=None):
        return self._cfg.get(key, default)

    @property
    def raw_config(self):
        return self._cfg

    def set_session(self, _sid):
        return None

    def clear_session(self):
        return None


class _FakeServices:
    def __init__(self):
        from app.core.task_manager import get_task_manager

        self.task_manager = get_task_manager()


class _FakeCtx:
    def __init__(self):
        self.bot = _FakeBot2()
        self.services = _FakeServices()


class _FakeModule:
    def __init__(self):
        self.bot_id = 99
        self.module_name = "llm_chat_v2"
        self.ctx = _FakeCtx()
        self.config = _FakeConfig({"schedule_enable": True, "context_expand_enable": True})


async def test_scheduled_message_gets_proactive_block_and_tools(tmp_path, monkeypatch):
    from app.llm import scheduler as ts_mod

    module = _FakeModule()
    sched = ts_mod.TaskScheduler(module, data_dir=str(tmp_path))
    captured: dict = {}

    class _FakeResp:
        ok = True
        text = "该起床啦"

    class _FakeProvider:
        async def chat(self, messages, **kw):
            captured["messages"] = messages
            captured["kwargs"] = kw
            return _FakeResp()

    monkeypatch.setattr(ts_mod, "get_provider", lambda cfg: _FakeProvider())

    try:
        entry = await sched.schedule("group_500", {"trigger": "明天早上8点", "content": "该起床啦"})
        await sched.trigger_now(entry.id)
    finally:
        sched.stop()

    joined = "\n".join(str(m.get("content")) for m in captured["messages"])
    assert "### 主动性" in joined
    assert "先自己取" in joined
    tool_names = {t["function"]["name"] for t in captured["kwargs"].get("tools") or []}
    assert "expand_context" in tool_names
    assert "get_current_session" in tool_names
    assert captured["kwargs"]["max_tool_rounds"] == 5


async def test_scheduled_message_skips_tools_when_disabled(tmp_path, monkeypatch):
    from app.llm import scheduler as ts_mod

    module = _FakeModule()
    module.config = _FakeConfig({"schedule_enable": True, "context_expand_enable": False})
    sched = ts_mod.TaskScheduler(module, data_dir=str(tmp_path))
    captured: dict = {}

    class _FakeResp:
        ok = True
        text = "该起床啦"

    class _FakeProvider:
        async def chat(self, messages, **kw):
            captured["kwargs"] = kw
            return _FakeResp()

    monkeypatch.setattr(ts_mod, "get_provider", lambda cfg: _FakeProvider())

    try:
        entry = await sched.schedule("private_100", {"trigger": "明天早上8点", "content": "该起床啦"})
        await sched.trigger_now(entry.id)
    finally:
        sched.stop()

    tool_names = {t["function"]["name"] for t in captured["kwargs"].get("tools") or []}
    assert "expand_context" not in tool_names
    assert "get_current_session" in tool_names


@pytest.mark.parametrize("is_group", [True, False])
async def test_collect_tools_maps_session_targets(tmp_path, is_group):
    from app.llm import scheduler as ts_mod

    module = _FakeModule()
    sched = ts_mod.TaskScheduler(module, data_dir=str(tmp_path))
    session_id = "group_500" if is_group else "private_100"

    try:
        entry = await sched.schedule(session_id, {"trigger": "明天早上8点", "content": "x"})
        _specs, _skills, ctx, instruction = await sched._collect_tools(entry)
    finally:
        sched.stop()

    assert ctx.session_id == session_id
    # TaskEntry.target 是 str（与 session_id 解析保持一致）；工具侧会自行归一化为纯数字
    assert str(ctx.group_id) == ("500" if is_group else "None")
    assert str(ctx.user_id) == ("None" if is_group else "100")
    assert instruction and "### 主动性" in instruction
