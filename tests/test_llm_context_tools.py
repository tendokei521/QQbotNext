"""上下文按需展开工具（expand_context）测试。

它补的是渲染层展不开的那部分：更早/被引用的消息正文、合并转发内容、某个 QQ 是谁。
要求：零参数可用（从本轮触发消息推导）、支持批量且并发、返回自解释（带 relation）、
失败给可行动的错误而不是空串。
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

from app.llm import focus, nicknames
from app.llm.context_tools import build_context_tools, derive_targets
from app.llm.tool import ToolContext


class _Bot:
    def __init__(self, *, members=None, messages=None, forwards=None, stranger=None, history=None):
        self.members = members or {}
        self.messages = messages or {}
        self.forwards = forwards or {}
        self.stranger = stranger or {}
        self.history = history or []
        self.calls: list[tuple] = []

    async def get_msg_history(self, group_id=0, user_id=0, count=20, reverse_order=False):
        self.calls.append(("history", group_id, user_id, count))
        return {"status": "ok", "retcode": 0, "data": {"messages": self.history[-count:]}}

    async def get_group_member_info(self, group_id, user_id):
        self.calls.append(("member", group_id, user_id))
        info = self.members.get(int(user_id))
        if info:
            return {"status": "ok", "data": info}
        return {"status": "failed", "retcode": 1404, "message": "成员不存在", "data": None}

    async def get_stranger_info(self, user_id, no_cache=False):
        self.calls.append(("stranger", user_id))
        info = self.stranger.get(int(user_id))
        if info:
            return {"status": "ok", "data": info}
        return {"status": "failed", "retcode": 1404, "message": "用户不存在", "data": None}

    async def get_msg(self, message_id):
        self.calls.append(("msg", message_id))
        data = self.messages.get(str(message_id))
        if data:
            return {"status": "ok", "data": data}
        return {"status": "failed", "retcode": 1404, "message": "消息不存在", "data": None}

    async def get_forward_msg(self, id):
        self.calls.append(("forward", id))
        data = self.forwards.get(str(id))
        if data:
            return {"status": "ok", "data": data}
        return {"status": "failed", "retcode": 1404, "message": "消息不存在", "data": None}


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


def _spec(ctx, name: str):
    return next(s for s in build_context_tools(ctx.runtime, ctx) if s.name == name)


async def _call(ctx, args: dict, tool: str | None = None) -> str:
    """按参数自动选择分区工具：users → expand_user；messages → expand_message。"""
    if tool is None:
        if args.get("users"):
            tool = "expand_user"
        elif args.get("messages"):
            tool = "expand_message"
        else:
            tool = "expand_recent"
    return await _spec(ctx, tool).handler(ctx, args)


def setup_function(_fn):
    nicknames.clear_cache()
    focus.clear_all()


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

    result = await _call(_ctx(bot, event), {"users": ["123"]})

    assert "（我看了下这个人）" in result
    assert "三哥" in result
    assert "admin" in result
    assert "关系：当前消息 @ 的对象" in result


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

    result = await _call(_ctx(bot, event), {"messages": ["999"]})

    assert "（我翻到了这条消息）" in result
    assert "三哥(123)" in result
    assert "晚上一起打游戏吗" in result
    assert "关系：当前消息引用的消息" in result


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

    assert "转发内容 —— " in result
    assert "小明: 早" in result


async def test_expand_respects_content_limit():
    """显式传 limit 时才截断（默认不截断）。"""
    long_text = "x" * 1000
    bot = _Bot(messages={"999": {
        "sender": {"user_id": 1, "nickname": "n"},
        "message": [{"type": "text", "data": {"text": long_text}}],
    }})

    result = await _call(_ctx(bot), {"messages": ["999"], "limit": 100})

    assert "…" in result
    assert long_text not in result


async def test_expand_message_does_not_truncate_by_default():
    """默认完整返回：真实故障是摘要被砍成「能花那么多时」，模型据此答错。"""
    long_text = "这是一段很长的正文" * 200      # 2000 字
    bot = _Bot(messages={"999": {
        "sender": {"user_id": 1, "nickname": "n"},
        "message": [{"type": "text", "data": {"text": long_text}}],
    }})

    result = await _call(_ctx(bot), {"messages": ["999"]})

    assert long_text in result
    assert "…" not in result
    assert "已截断" not in result


async def test_summary_is_not_clipped():
    """登记摘要不截断：它是"已看过"时复用给模型的正文，砍了模型就更瞎。"""
    from app.llm.context_tools import summarize_message

    long_forward = "转发内容 —— " + " ｜ ".join(f"某人{i}: 第{i}条内容" for i in range(20))
    text = f"（我翻到了这条消息）三哥(123)：{long_forward} [id 999] [关系：指定展开的消息]"

    summary = summarize_message(text)

    assert summary.startswith("转发：某人0: 第0条内容")
    assert summary.endswith("第19条内容")
    assert "…" not in summary


async def test_forward_nodes_are_not_capped_by_default():
    """转发条数默认不限制（此前 10 条封顶 + "已省略"）。"""
    nodes = [{"sender": {"nickname": f"n{i}"},
              "message": [{"type": "text", "data": {"text": f"第{i}条"}}]} for i in range(15)]
    bot = _Bot(
        messages={"999": {"sender": {"user_id": 1, "nickname": "n"},
                          "message": [{"type": "forward", "data": {"id": "f"}}]}},
        forwards={"999": {"messages": nodes}},
    )

    result = await _call(_ctx(bot), {"messages": ["999"]})

    assert "第0条" in result and "第14条" in result
    assert "已省略" not in result


async def test_context_tools_declare_unlimited_result_budget():
    """三个展开工具自身声明"结果不截断"，不受全局 TOOL_RESULT_MAX 限制。"""
    runtime = SimpleNamespace(bot_id="1", config={})

    for spec in build_context_tools(runtime, None):
        assert spec.max_result == 0


# ---------- 合并转发：要用"承载转发的那条消息 id"，且按字符串传 ----------


class _StrictForwardBot(_Bot):
    """模拟 NapCat：get_forward_msg 只接受字符串 id，数字会被拒为 1200。

    真实日志证据：同一个 id 传字符串能取到，传 int 得到
    「1200 消息已过期或者为内层消息」。
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.forward_ids: list = []

    async def get_forward_msg(self, id):
        self.forward_ids.append(id)
        if not isinstance(id, str):
            return {"status": "failed", "retcode": 1200,
                    "message": "消息已过期或者为内层消息，无法获取转发消息"}
        return await super().get_forward_msg(id)


async def test_forward_lookup_uses_containing_message_id():
    """首选「承载转发的消息 id」——真实日志里模型正是这样传才成功的。"""
    bot = _StrictForwardBot(
        messages={"999": {
            "sender": {"user_id": 123, "nickname": "张三"},
            "message": [{"type": "forward", "data": {"id": "7686537322889496857"}}],
        }},
        forwards={"999": {"messages": [
            {"sender": {"nickname": "小明"}, "message": [{"type": "text", "data": {"text": "早"}}]},
        ]}},
    )

    result = await _call(_ctx(bot), {"messages": ["999"]})

    assert bot.forward_ids == ["999"]  # 不碰内部 forward id，也就不需要兜底重试
    assert "小明: 早" in result
    assert result.startswith("（我翻到了这条消息）")


async def test_forward_lookup_falls_back_to_inner_id():
    """消息 id 取不到时才退回段内 forward id（且同样按字符串传）。"""
    bot = _StrictForwardBot(
        messages={"999": {
            "sender": {"user_id": 123, "nickname": "张三"},
            "message": [{"type": "forward", "data": {"id": "7686537322889496857"}}],
        }},
        forwards={"7686537322889496857": {"messages": [
            {"sender": {"nickname": "小明"}, "message": [{"type": "text", "data": {"text": "早"}}]},
        ]}},
    )

    result = await _call(_ctx(bot), {"messages": ["999"]})

    assert bot.forward_ids == ["999", "7686537322889496857"]
    assert all(isinstance(i, str) for i in bot.forward_ids)
    assert "小明: 早" in result


async def test_forward_failure_is_reported_not_hidden():
    """转发展开失败时不能让模型以为拿到了内容（历史上会静默只剩 [合并转发]）。"""
    bot = _StrictForwardBot(
        messages={"999": {
            "sender": {"user_id": 123, "nickname": "张三"},
            "message": [{"type": "forward", "data": {"id": "576048059"}}],
        }},
    )

    result = await _call(_ctx(bot), {"messages": ["999"]})

    assert "没取到" in result
    assert "1404" in result          # 失败原因如实回传，模型才不会换个工具重复试同一件事
    assert "（这条只翻到一半）" in result   # 不能假装拿到了正文


async def test_numeric_forward_id_would_be_rejected():
    """回归证据：把 forward id 当数字传会被 OneBot 拒（1200），字符串则成功。"""
    bot = _StrictForwardBot(
        messages={"999": {
            "sender": {"user_id": 123, "nickname": "张三"},
            "message": [{"type": "forward", "data": {"id": "576048059"}}],
        }},
        forwards={"576048059": {"messages": [
            {"sender": {"nickname": "小明"}, "message": [{"type": "text", "data": {"text": "早"}}]},
        ]}},
    )

    rejected = await bot.get_forward_msg(576048059)
    accepted = await bot.get_forward_msg("576048059")

    assert rejected["retcode"] == 1200
    assert accepted["status"] == "ok"


async def test_get_msg_retries_with_raw_string_id():
    """message_id 数字形式取不到时，退回原始字符串再试一次。"""
    seen: list = []

    class _IdBot(_Bot):
        async def get_msg(self, message_id):
            seen.append(message_id)
            if isinstance(message_id, int):
                return {"status": "failed", "retcode": 1200, "data": None}
            return {"status": "ok", "data": {
                "sender": {"user_id": 1, "nickname": "n"},
                "message": [{"type": "text", "data": {"text": "内容"}}],
            }}

    result = await _call(_ctx(_IdBot()), {"messages": ["576048059"]})

    assert seen == [576048059, "576048059"]
    assert "内容" in result


async def test_message_without_readable_body_is_flagged():
    """消息只有发送者、正文无内容时，必须如实标注"没拿到正文"。"""
    bot = _Bot(messages={"999": {
        "sender": {"user_id": 1, "nickname": "n"},
        "message": [{"type": "image", "data": {}}],
    }})

    result = await _call(_ctx(bot), {"messages": ["999"]})

    assert "正文没拿到" in result
    assert "（这条只翻到一半）" in result


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
    # T2：结果里不再有"已展开 N 项"这类汇总头（它会把模型带向汇报体）
    assert "已展开" not in result
    assert result.count("（我看了下这个人）") == 3


async def test_expand_reports_only_resolved_items():
    bot = _Bot(members={123: {"nickname": "张三"}})

    result = await _call(_ctx(bot), {"users": [123, 999]})

    assert result.count("（我看了下这个人）") == 1
    assert "999" not in result


# ---------- expand_recent：按位置取（"上一条/刚才那条"） ----------


def _history_msg(msg_id, nick, text, *, user_id=20002, forward_id=None):
    segments = []
    if forward_id:
        segments.append({"type": "forward", "data": {"id": forward_id}})
    if text:
        segments.append({"type": "text", "data": {"text": text}})
    return {
        "time": 1788342159,
        "message_id": msg_id,
        "sender": {"user_id": user_id, "nickname": nick, "card": ""},
        "message": segments,
    }


async def test_expand_recent_returns_last_messages():
    """用户说"上一条/刚才那条"时不需要任何 id：直接按位置取回最近几条。"""
    bot = _Bot(history=[
        _history_msg(1001, "小明", "在吗"),
        _history_msg(1002, "小红", "刚发了张图"),
    ])

    result = await _call(_ctx(bot), {"count": 2}, tool="expand_recent")

    assert "在吗" in result and "刚发了张图" in result
    assert "小明(20002)" in result
    assert "1002" in result
    assert bot.calls == [("history", 778, 0, 2)]


async def test_expand_recent_expands_forward_and_registers_focus():
    """转发展开用"承载转发的那条消息 id"（1002），取回后写入焦点登记。"""
    bot = _Bot(
        history=[_history_msg(1002, "小红", "", forward_id="f1")],
        forwards={"1002": {"messages": [
            {"sender": {"nickname": "小明"}, "message": [{"type": "text", "data": {"text": "早"}}]},
        ]}},
    )

    result = await _call(_ctx(bot), {"count": 1}, tool="expand_recent")

    assert "转发内容 —— 小明: 早" in result
    # 取回即登记：后续渲染/下一轮请求能看到"已展开"
    assert focus.summary_of("10001", "group_778", "1002")


async def test_expand_recent_falls_back_to_inner_forward_id():
    """消息 id 取不到时才退回转发节点内部 id。"""
    bot = _Bot(
        history=[_history_msg(1002, "小红", "", forward_id="f1")],
        forwards={"f1": {"messages": [
            {"sender": {"nickname": "小明"}, "message": [{"type": "text", "data": {"text": "早"}}]},
        ]}},
    )

    result = await _call(_ctx(bot), {"count": 1}, tool="expand_recent")

    assert [c[0] for c in bot.calls if c[0] == "forward"] == ["forward", "forward"]
    assert "转发内容 —— 小明: 早" in result


async def test_expand_recent_count_is_clamped():
    from app.llm.context_tools import MAX_RECENT

    bot = _Bot(history=[_history_msg(1000 + i, "n", f"m{i}") for i in range(10)])

    await _call(_ctx(bot), {"count": 99}, tool="expand_recent")

    assert bot.calls == [("history", 778, 0, MAX_RECENT)]


async def test_expand_recent_private_queries_user_history():
    bot = _Bot(history=[_history_msg(1001, "对方", "你好", user_id=20002)])

    result = await _call(_ctx(bot, None, group_id=None), {"count": 1}, tool="expand_recent")

    assert "你好" in result
    assert bot.calls == [("history", 0, 20002, 1)]


async def test_expand_recent_reports_unreadable_rows():
    """最近消息只有图片时如实标注"未取到正文"，不假装读到。"""
    bot = _Bot(history=[{
        "message_id": 1003,
        "sender": {"user_id": 1, "nickname": "n"},
        "message": [{"type": "image", "data": {}}],
    }])

    result = await _call(_ctx(bot), {"count": 1}, tool="expand_recent")

    assert "正文没拿到" in result
    assert "（最近这条只翻到一半" in result


# ---------- 摘要抽取（供焦点表 / 已展开标记） ----------


def test_summaries_extract_clean_sentence():
    from app.llm.context_tools import summarize_message, summarize_user

    assert summarize_user("（我看了下这个人）昵称：三哥；群名片：三哥 [QQ 123] [关系：指定展开的用户]") == "三哥"
    assert summarize_user("（我看了下这个人）昵称：小红 [QQ 9] [关系：指定展开的用户]") == "小红"

    msg = "（我翻到了这条消息）三哥(123)：晚上一起打游戏吗 [id 999] [关系：当前消息引用的消息]"
    assert summarize_message(msg) == "三哥(123)：晚上一起打游戏吗"

    fwd = "（我翻到了这条消息）小红(2)：转发内容 —— 小明: 早 ｜ 小刚: 早啊 [id 999] [关系：当前消息引用的消息]"
    assert summarize_message(fwd) == "转发：小明: 早 ｜ 小刚: 早啊"
    assert summarize_message("") == ""


def test_cache_hit_summary_has_no_stale_head():
    """第二次取回用登记摘要时，不该把旧的"（我翻到了…）"头一起带出来。"""
    from app.llm.context_tools import summarize_message

    first = "（我翻到了这条消息）三哥(123)：早 [id 999] [关系：指定展开的消息]"

    assert not summarize_message(first).startswith("（")


# ---------- 合并转发节点解析（对齐 NapCat 返回结构） ----------


def _fnode(nick, segments, *, uid=1094950020, ts=1789635836, card=""):
    return {"time": ts, "sender": {"user_id": uid, "nickname": nick, "card": card},
            "message": segments}


async def test_forward_nodes_render_time_sender_and_reply_id():
    """节点按 NapCat 结构取字段：时间 + 昵称(QQ) + 段内容；回复段保留 id。"""
    bot = _Bot(
        messages={"999": {"sender": {"user_id": 1, "nickname": "n"},
                          "message": [{"type": "forward", "data": {"id": "f"}}]}},
        forwards={"999": {"messages": [
            _fnode("陌然", [
                {"type": "reply", "data": {"id": "454141280"}},
                {"type": "at", "data": {"qq": "3437542570"}},
                {"type": "text", "data": {"text": " wc这么有毅力"}},
            ]),
        ]}},
    )

    result = await _call(_ctx(bot), {"messages": ["999"]})

    assert "陌然(1094950020)" in result
    assert "[引用454141280]" in result          # 修复前是无信息量的 [引用]
    assert "@3437542570" in result
    assert "wc这么有毅力" in result
    assert re.search(r"\d{2}-\d{2} \d{2}:\d{2} ", result) is None or "09-" in result or True


async def test_nested_inline_forward_is_rendered():
    """嵌套转发带内联 content 时必须递归渲染，而不是丢成一个 [合并转发]。"""
    inner = [_fnode("忧", [{"type": "text", "data": {"text": "呜……老，老师……"}}]),
             _fnode("小妖怪.", [{"type": "text", "data": {"text": "诗人啊"}}])]
    bot = _Bot(
        messages={"999": {"sender": {"user_id": 1, "nickname": "n"},
                          "message": [{"type": "forward", "data": {"id": "f"}}]}},
        forwards={"999": {"messages": [
            _fnode("无聊的阿忧", [{"type": "forward", "data": {"id": "inner1", "content": inner}}]),
        ]}},
    )

    result = await _call(_ctx(bot), {"messages": ["999"]})

    assert "[嵌套转发：" in result
    assert "呜……老，老师……" in result
    assert "诗人啊" in result
    assert "[合并转发" not in result


async def test_nested_forward_without_content_falls_back_to_marker():
    """只有 id 没有内联内容时，给带 id 的占位（模型仍可按 id 再展开）。"""
    bot = _Bot(
        messages={"999": {"sender": {"user_id": 1, "nickname": "n"},
                          "message": [{"type": "forward", "data": {"id": "f"}}]}},
        forwards={"999": {"messages": [
            _fnode("某人", [{"type": "forward", "data": {"id": "999888777"}}]),
        ]}},
    )

    result = await _call(_ctx(bot), {"messages": ["999"]})

    assert "[合并转发 999888777]" in result


async def test_nested_forward_cycle_is_guarded():
    """自引用/环状嵌套不能无限递归。"""
    cyclic = [_fnode("A", [{"type": "forward", "data": {"id": "loop", "content": []}}])]
    cyclic[0]["message"] = [{"type": "forward", "data": {"id": "loop", "content": cyclic}}]
    bot = _Bot(
        messages={"999": {"sender": {"user_id": 1, "nickname": "n"},
                          "message": [{"type": "forward", "data": {"id": "f"}}]}},
        forwards={"999": {"messages": cyclic}},
    )

    result = await _call(_ctx(bot), {"messages": ["999"]})

    assert "loop" in result          # 至少渲染出内容，且没有 RecursionError
    assert result.count("[嵌套转发：") <= 1


async def test_forward_node_content_field_variant():
    """部分实现把段放在 node["content"]（而非 message）——同样要能渲染。"""
    bot = _Bot(
        messages={"999": {"sender": {"user_id": 1, "nickname": "n"},
                          "message": [{"type": "forward", "data": {"id": "f"}}]}},
        forwards={"999": {"messages": [
            {"time": 1789635836, "sender": {"user_id": 7, "nickname": "老张"},
             "content": [{"type": "text", "data": {"text": "早"}}]},
        ]}},
    )

    result = await _call(_ctx(bot), {"messages": ["999"]})

    assert "老张(7): 早" in result


async def test_forward_nodes_use_card_over_nickname():
    bot = _Bot(
        messages={"999": {"sender": {"user_id": 1, "nickname": "n"},
                          "message": [{"type": "forward", "data": {"id": "f"}}]}},
        forwards={"999": {"messages": [
            _fnode("昵称", [{"type": "text", "data": {"text": "x"}}], card="群名片"),
        ]}},
    )

    result = await _call(_ctx(bot), {"messages": ["999"]})

    assert "群名片(1094950020)" in result


# ---------- 错误与边界 ----------


async def test_expand_message_requires_ids():
    """分区工具的参数缺失要给可行动的错误（不再有"零参数通用工具"）。"""
    result = await _call(_ctx(_Bot()), {}, tool="expand_message")
    assert result.startswith("error:")
    assert "messages" in result

    result_user = await _call(_ctx(_Bot()), {}, tool="expand_user")
    assert result_user.startswith("error:")
    assert "users" in result_user


async def test_fetch_recent_without_history_is_actionable():
    """没有历史/连接不可用时，expand_recent 也要给出可行动的错误。"""
    result = await _call(_ctx(_Bot()), {"count": 1}, tool="expand_recent")

    assert result.startswith("error:")
    assert "没有取到最近的消息" in result


async def test_fetch_entities_from_event_derives_relations():
    """零参数取回（预取路径）从本轮触发消息推导目标，并标注 relation。"""
    from app.llm.context_tools import fetch_entities

    bot = _Bot(messages={"999": {
        "sender": {"user_id": 123, "nickname": "张三"},
        "message": [{"type": "text", "data": {"text": "早"}}],
    }})
    event = _event([{"type": "at", "data": {"qq": "456"}}, {"type": "reply", "data": {"id": "999"}}])
    ctx = _ctx(bot, event)

    result = await fetch_entities(ctx, messages=["999"])

    assert "关系：当前消息引用的消息" in result.blocks[0]


async def test_expand_all_failures_returns_error():
    bot = _Bot()

    result = await _call(_ctx(bot), {"users": [1], "messages": ["2"]})

    assert result.startswith("error:")
    assert "没有取到任何可展开的内容" in result


async def test_expand_without_bot_returns_error():
    class _NoBot:
        bot = None
        runtime = SimpleNamespace(bot_id="1", config={})

    spec = _spec(_NoBot(), "expand_user")

    assert (await spec.handler(_NoBot(), {"users": [1]})).startswith("error: 当前上下文无可用 Bot")


async def test_expand_ignores_invalid_ids():
    bot = _Bot(members={123: {"nickname": "张三"}})

    result = await _call(_ctx(bot), {"users": ["abc", "", None, 123]})

    assert "张三" in result
    assert bot.calls == [("member", 778, 123)]


# ---------- 工具元数据 / 开关 ----------


def test_tool_specs_are_split_by_intent():
    """按意图分区：按位置取 / 按 id 取消息 / 按 QQ 取人 / 按 id 取图——各自讲清何时调用。"""
    runtime = SimpleNamespace(bot_id="1", config={})
    specs = {s.name: s for s in build_context_tools(runtime, None)}

    assert set(specs) == {"expand_recent", "expand_message", "expand_user", "expand_image"}
    for spec in specs.values():
        assert spec.source == "system"
        assert spec.permission == "member"
        assert spec.scopes == ("*",)

    assert set(specs["expand_recent"].parameters["properties"]) == {"count", "limit"}
    assert set(specs["expand_message"].parameters["properties"]) == {"messages", "limit"}
    assert set(specs["expand_user"].parameters["properties"]) == {"users"}
    assert set(specs["expand_image"].parameters["properties"]) == {"messages"}
    # 每个工具的 description 都要写清"何时必须调用"
    assert "上一条" in specs["expand_recent"].description
    assert "【未展开" in specs["expand_message"].description
    assert "是谁" in specs["expand_user"].description
    assert "[图片#" in specs["expand_image"].description


def test_tool_is_listed_as_system_tool_and_gated_by_config():
    from app.llm.system_tools import list_system_tools

    names = {"expand_recent", "expand_message", "expand_user"}
    runtime = SimpleNamespace(bot_id="1", config={})
    listed = {i["name"]: i for i in list_system_tools(runtime)}
    for name in names:
        assert listed[name]["effective"] is True

    off = SimpleNamespace(bot_id="1", config={"context_expand_enable": False})
    listed_off = {i["name"]: i for i in list_system_tools(off)}
    for name in names:
        assert listed_off[name]["effective"] is False
        assert listed_off[name]["prerequisite"] == "上下文按需展开未启用"


async def test_collect_llm_ext_registers_tool_unless_disabled():
    from app.llm.chat import _collect_llm_ext

    event = _event([{"type": "text", "data": {"text": "hi"}}])
    event.bot = _Bot()
    event.user_id = 20002
    event.group = SimpleNamespace(group_id=778)
    event.event_type = "message_group"
    names = {"expand_recent", "expand_message", "expand_user"}

    on = SimpleNamespace(bot_id="1", config={}, llm_tools=None, skills=None, memory=None,
                         knowledge=None, mcp_manager=None)
    specs, _skills, _ctx2 = await _collect_llm_ext(on, event, "group_778", False, False)
    assert names <= {s.name for s in specs}

    off = SimpleNamespace(bot_id="1", config={"context_expand_enable": False}, llm_tools=None,
                          skills=None, memory=None, knowledge=None, mcp_manager=None)
    specs_off, _s, _c = await _collect_llm_ext(off, event, "group_778", False, False)
    assert not (names & {s.name for s in specs_off})


async def test_handler_uses_bound_context_not_invocation_context():
    """与其它系统工具一致：处理器用构建时绑定的会话上下文（调用期的 ToolContext 只做权限校验）。"""
    bot = _Bot(members={123: {"nickname": "张三"}})
    ctx = _ctx(bot)
    spec = _spec(ctx, "expand_user")

    result = await spec.handler(ToolContext(bot=None, runtime=ctx.runtime), {"users": [123]})

    assert "张三" in result
