"""上下文按需展开工具（expand_context）测试。

它补的是渲染层展不开的那部分：更早/被引用的消息正文、合并转发内容、某个 QQ 是谁。
要求：零参数可用（从本轮触发消息推导）、支持批量且并发、返回自解释（带 relation）、
失败给可行动的错误而不是空串。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.llm import nicknames
from app.llm.context_tools import build_context_tools, derive_targets
from app.llm.tool import ToolContext


class _Bot:
    def __init__(self, *, members=None, messages=None, forwards=None, stranger=None):
        self.members = members or {}
        self.messages = messages or {}
        self.forwards = forwards or {}
        self.stranger = stranger or {}
        self.calls: list[tuple] = []

    async def get_group_member_info(self, group_id, user_id):
        self.calls.append(("member", group_id, user_id))
        info = self.members.get(int(user_id))
        return {"status": "ok", "data": info} if info else {"status": "failed", "data": None}

    async def get_stranger_info(self, user_id, no_cache=False):
        self.calls.append(("stranger", user_id))
        info = self.stranger.get(int(user_id))
        return {"status": "ok", "data": info} if info else {"status": "failed", "data": None}

    async def get_msg(self, message_id):
        self.calls.append(("msg", message_id))
        data = self.messages.get(str(message_id))
        return {"status": "ok", "data": data} if data else {"status": "failed", "data": None}

    async def get_forward_msg(self, id):
        self.calls.append(("forward", id))
        data = self.forwards.get(str(id))
        return {"status": "ok", "data": data} if data else {"status": "failed", "data": None}


def _ctx(bot, event=None, *, group_id=778, bot_id="10001"):
    runtime = SimpleNamespace(bot_id=bot_id, config={})
    return SimpleNamespace(
        bot=bot,
        runtime=runtime,
        bot_id=bot_id,
        group_id=group_id,
        user_id=20002,
        session_id=f"group_{group_id}" if group_id else "private_20002",
        event=event,
    )


def _event(segments, self_id=10001):
    return SimpleNamespace(message=segments, self_id=self_id, bot_id=self_id)


async def _call(ctx, args: dict) -> str:
    spec = build_context_tools(ctx.runtime, ctx)[0]
    return await spec.handler(ctx, args)


def setup_function(_fn):
    nicknames.clear_cache()


# ---------- 目标推导 ----------


def test_derive_targets_collects_ats_and_replies():
    event = _event([
        {"type": "at", "data": {"qq": "123"}},
        {"type": "at", "data": {"qq": "10001"}},   # 自己 → 跳过
        {"type": "at", "data": {"qq": "all"}},     # 全体 → 跳过
        {"type": "at", "data": {"qq": "456"}},
        {"type": "reply", "data": {"id": "999"}},
        {"type": "forward", "data": {"id": "f1"}},
        {"type": "text", "data": {"text": "你说呢"}},
    ])

    users, messages = derive_targets(event, {"10001"})

    assert users == ["123", "456"]
    assert messages == ["999", "f1"]


def test_derive_targets_tolerates_missing_message():
    assert derive_targets(SimpleNamespace(message=None), set()) == ([], [])
    assert derive_targets(SimpleNamespace(message="raw text"), set()) == ([], [])


# ---------- 用户展开 ----------


async def test_expand_user_in_group_reports_identity_and_relation():
    bot = _Bot(members={123: {"nickname": "张三", "card": "三哥", "role": "admin", "level": "3"}})
    event = _event([{"type": "at", "data": {"qq": "123"}}])

    result = await _call(_ctx(bot, event), {})

    assert "【用户 123】" in result
    assert "三哥" in result
    assert "admin" in result
    assert "relation：当前消息 @ 的对象" in result


async def test_expand_user_writes_shared_nickname_cache():
    bot = _Bot(members={123: {"nickname": "张三", "card": "三哥"}})

    await _call(_ctx(bot), {"users": [123]})

    assert nicknames.cached_nickname("10001", 778, "123") == "三哥"


async def test_expand_user_in_private_uses_stranger_info():
    bot = _Bot(stranger={20002: {"nickname": "小红"}})

    result = await _call(_ctx(bot, None, group_id=None), {"users": ["20002"]})

    assert "小红" in result
    assert bot.calls == [("stranger", 20002)]


# ---------- 消息展开 ----------


async def test_expand_message_reports_sender_and_text():
    bot = _Bot(messages={"999": {
        "sender": {"user_id": 123, "nickname": "张三", "card": "三哥"},
        "message": [{"type": "text", "data": {"text": "晚上一起打游戏吗"}}],
    }})
    event = _event([{"type": "reply", "data": {"id": "999"}}])

    result = await _call(_ctx(bot, event), {})

    assert "【消息 999】" in result
    assert "三哥(123)" in result
    assert "晚上一起打游戏吗" in result
    assert "relation：当前消息引用的消息" in result


async def test_expand_message_unwraps_forward_one_level():
    bot = _Bot(
        messages={"999": {
            "sender": {"user_id": 123, "nickname": "张三"},
            "message": [{"type": "forward", "data": {"id": "f1"}}],
        }},
        forwards={"f1": {"messages": [
            {"sender": {"nickname": "小明"}, "message": [{"type": "text", "data": {"text": "早"}}]},
            {"sender": {"nickname": "小刚"}, "message": [{"type": "text", "data": {"text": "早啊"}}]},
        ]}},
    )

    result = await _call(_ctx(bot), {"messages": ["999"]})

    assert "合并转发内容：" in result
    assert "小明: 早" in result


async def test_expand_respects_content_limit():
    long_text = "x" * 1000
    bot = _Bot(messages={"999": {
        "sender": {"user_id": 1, "nickname": "n"},
        "message": [{"type": "text", "data": {"text": long_text}}],
    }})

    result = await _call(_ctx(bot), {"messages": ["999"], "limit": 100})

    assert "…" in result
    assert long_text not in result


# ---------- 批量 / 并发 ----------


async def test_expand_bulk_runs_concurrently():
    started: list[str] = []

    class _SlowBot(_Bot):
        async def get_group_member_info(self, group_id, user_id):
            started.append(str(user_id))
            for _ in range(60):
                if len(started) == 3:
                    break
                await asyncio.sleep(0.01)
            return {"status": "ok", "data": {"nickname": f"n{user_id}"}}

    result = await _call(_ctx(_SlowBot()), {"users": [1, 2, 3]})

    assert len(started) == 3  # 三个都同时处于执行中
    assert "已展开 3 项" in result


async def test_expand_reports_only_resolved_items():
    bot = _Bot(members={123: {"nickname": "张三"}})

    result = await _call(_ctx(bot), {"users": [123, 999]})

    assert "已展开 1 项" in result
    assert "999" not in result.split("\n", 1)[1]


# ---------- 错误与边界 ----------


async def test_expand_without_targets_and_without_event_is_actionable():
    result = await _call(_ctx(_Bot(), None, group_id=None), {})

    assert result.startswith("error:")
    assert "users" in result and "messages" in result


async def test_expand_with_nothing_to_expand():
    event = _event([{"type": "text", "data": {"text": "普通消息"}}])

    result = await _call(_ctx(_Bot(), event), {})

    assert "没有需要展开" in result


async def test_expand_all_failures_returns_error():
    bot = _Bot()

    result = await _call(_ctx(bot), {"users": [1], "messages": ["2"]})

    assert result.startswith("error:")
    assert "没有取到任何可展开的内容" in result


async def test_expand_without_bot_returns_error():
    class _NoBot:
        bot = None
        runtime = SimpleNamespace(bot_id="1", config={})

    spec = build_context_tools(_NoBot.runtime, _NoBot())[0]

    assert (await spec.handler(_NoBot(), {"users": [1]})).startswith("error: 当前上下文无可用 Bot")


async def test_expand_ignores_invalid_ids():
    bot = _Bot(members={123: {"nickname": "张三"}})

    result = await _call(_ctx(bot), {"users": ["abc", "", None, 123]})

    assert "张三" in result
    assert bot.calls == [("member", 778, 123)]


# ---------- 工具元数据 / 开关 ----------


def test_tool_spec_metadata():
    runtime = SimpleNamespace(bot_id="1", config={})
    spec = build_context_tools(runtime, None)[0]

    assert spec.name == "expand_context"
    assert spec.source == "system"
    assert spec.permission == "member"
    assert spec.scopes == ("*",)
    assert "【未展开" in spec.description
    assert set(spec.parameters["properties"]) == {"users", "messages", "limit"}


def test_tool_is_listed_as_system_tool_and_gated_by_config():
    from app.llm.system_tools import list_system_tools

    runtime = SimpleNamespace(bot_id="1", config={})
    entry = next(i for i in list_system_tools(runtime) if i["name"] == "expand_context")
    assert entry["effective"] is True

    off = SimpleNamespace(bot_id="1", config={"context_expand_enable": False})
    entry_off = next(i for i in list_system_tools(off) if i["name"] == "expand_context")
    assert entry_off["effective"] is False
    assert entry_off["prerequisite"] == "上下文按需展开未启用"


async def test_collect_llm_ext_registers_tool_unless_disabled():
    from app.llm.chat import _collect_llm_ext

    event = _event([{"type": "text", "data": {"text": "hi"}}])
    event.bot = _Bot()
    event.user_id = 20002
    event.group = SimpleNamespace(group_id=778)
    event.event_type = "message_group"

    on = SimpleNamespace(bot_id="1", config={}, llm_tools=None, skills=None, memory=None,
                         knowledge=None, mcp_manager=None)
    specs, _skills, _ctx2 = await _collect_llm_ext(on, event, "group_778", False, False)
    assert "expand_context" in {s.name for s in specs}

    off = SimpleNamespace(bot_id="1", config={"context_expand_enable": False}, llm_tools=None,
                          skills=None, memory=None, knowledge=None, mcp_manager=None)
    specs_off, _s, _c = await _collect_llm_ext(off, event, "group_778", False, False)
    assert "expand_context" not in {s.name for s in specs_off}


async def test_handler_uses_bound_context_not_invocation_context():
    """与其它系统工具一致：处理器用构建时绑定的会话上下文（调用期的 ToolContext 只做权限校验）。"""
    bot = _Bot(members={123: {"nickname": "张三"}})
    ctx = _ctx(bot)
    spec = build_context_tools(ctx.runtime, ctx)[0]

    result = await spec.handler(ToolContext(bot=None, runtime=ctx.runtime), {"users": [123]})

    assert "张三" in result
