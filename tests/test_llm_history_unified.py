"""H3 统一性测试：四条路径共用同一渲染语义。

- 「本轮消息」（enhance.format_user_context）与「历史条目」（history_model）用同一套字段：
  头部行（时间/QQ/群号/发送者）+ 正文 + 附注行（提到了/引用了）；
- 在线背景（format_online_history）与会话历史（format_history_for_llm）共用
  render_history_entry，只有"取内容"的方式不同（OneBot 段 vs 已存正文）；
- 增强块判定只有一处（history_model.is_enhanced_content）。
"""

from __future__ import annotations

import types

from app.domain.events import GroupMessageEvent, MessageSegment, UserInfo
from app.llm.enhance import collect_user_context, format_user_context
from app.llm.group_context import format_history_for_llm, format_online_history
from app.llm.history_model import build_current_turn_text

NL = chr(10)


# ---------- build_current_turn_text ----------


def test_current_turn_legacy_style_order():
    text = build_current_turn_text(
        sent_text="你好",
        head_lines=["(时间：2026-09-19 19:41:48)", "(当前群号: 466)", "发送者：小明(20002)"],
        tail_lines=["引用了：小红(9)发送的引用消息：“在吗”"],
    )
    assert text.splitlines() == [
        "(时间：2026-09-19 19:41:48)",
        "(当前群号: 466)",
        "发送者：小明(20002)",
        "发送了：你好",
        "引用了：小红(9)发送的引用消息：“在吗”",
    ]


def test_current_turn_single_style_collapses_sender_into_body():
    text = build_current_turn_text(
        sent_text="你好",
        sender_label="小明(20002)",
        head_lines=["(时间：2026-09-19 19:41:48)"],
        tail_lines=["提到了(用户名)：@小红(9)"],
        sender_style="single",
    )
    assert text.splitlines() == [
        "(时间：2026-09-19 19:41:48)",
        "小明(20002): 你好",
        "提到了(用户名)：@小红(9)",
    ]


def test_current_turn_new_styles_and_empty_body():
    assert build_current_turn_text(
        sent_text="你好", head_lines=["发送者昵称：小明(1)"], sent_style="new"
    ).splitlines() == ["发送者昵称：小明(1)", "消息正文：你好"]
    # 没有正文时只保留头部行
    assert build_current_turn_text(sent_text="", head_lines=["(时间：t)"]) == "(时间：t)"
    # 什么都没拼出来 → 回退原文本
    assert build_current_turn_text(sent_text="", current_text="原样") == "原样"


# ---------- enhance.format_user_context ----------


def _ctx(event, user_text="", **state):
    from app.llm.context import LlmContext

    ctx = LlmContext(event=event, runtime=None, bot=None, session_id="group_1")
    ctx.user_text = user_text
    ctx.state.update(state)
    return ctx


def _group_event():
    return GroupMessageEvent(
        event_type="message_group", message_type="group", time=1, user_id=20002, self_id=778,
        message=[MessageSegment("text", {"text": "你好"})],
        user=UserInfo(user_id=20002, nickname="小明"),
        group=types.SimpleNamespace(group_id=466052056),
    )


async def test_format_user_context_group_injects_sender_group_time_quote():
    event = _group_event()
    ctx = _ctx(event, "你好", user_context={
        "sender": "小明(20002)",
        "mentioned": ["小红(30003)"],
        "quote": "在吗",
        "quote_sender": "小红(30003)",
        "sent_text": "你好",
    })
    await format_user_context(ctx)
    text = ctx.user_text
    assert text.splitlines()[0].startswith("(时间：")
    assert "(当前群号: 466052056)" in text
    assert "发送者：小明(20002)" in text
    assert "发送了：你好" in text
    assert "提到了(用户名)：小红(30003)" in text
    assert "引用了：小红(30003)发送的引用消息：“在吗”" in text
    assert ctx.state["message_meta_injected"] is True


async def test_format_user_context_private_injects_qq_line():
    event = GroupMessageEvent(
        event_type="message_private", message_type="private", time=1, user_id=30003, self_id=778,
        message=[MessageSegment("text", {"text": "在吗"})],
        user=UserInfo(user_id=30003, nickname="小红"),
        group=types.SimpleNamespace(group_id=0),
    )
    ctx = _ctx(event, "在吗", user_context={"sent_text": "在吗"})
    await format_user_context(ctx)
    assert "(QQ: 30003)" in ctx.user_text
    assert "发送了：在吗" in ctx.user_text


async def test_format_user_context_without_info_is_noop():
    ctx = _ctx(_group_event(), "原文")
    await format_user_context(ctx)
    assert ctx.user_text == "原文"


async def test_collect_then_format_matches_history_semantics():
    """collect_user_context + format_user_context 产出的字段，与历史渲染读同一批语义。"""
    event = _group_event()
    ctx = _ctx(event, "你好", user_context={
        "sender": "小明(20002)",
        "sent_text": "你好",
    })
    await collect_user_context(ctx)
    await format_user_context(ctx)
    assert "发送者：小明(20002)" in ctx.user_text
    assert "发送了：你好" in ctx.user_text


# ---------- 在线背景与会话历史共用渲染器 ----------


def test_online_and_session_history_agree_on_single_line_shape():
    """同一句话：在线（OneBot 段）与会话（结构化 base）渲染出的形态一致。"""
    from app.llm.history_model import BaseInfo, HistoryEntry, render_history_entry

    entry = HistoryEntry(
        role="user",
        base=BaseInfo(time=1788342260, sender_id="20002", sender_name="小明", text="在吗"),
    )
    session_line = render_history_entry(entry)
    assert session_line == "09-02 17:44 小明(20002): 在吗"

    online = [{
        "time": 1788342260, "message_id": 1,
        "sender": {"user_id": 20002, "nickname": "小明", "card": ""},
        "message": [{"type": "text", "data": {"text": "在吗"}}],
    }]
    assert format_online_history(online) == session_line


def test_both_paths_keep_enhanced_block_verbatim():
    """增强块（模型产出的散文块）在两处都原样保留，不再套外层前缀。"""
    enhanced = "发送者：小明(20002)" + NL + "发送了：你好"

    session = format_history_for_llm([{"role": "user", "content": enhanced, "time": 1}])
    online = format_online_history([{
        "time": 1, "message_id": 1,
        "sender": {"user_id": 20002, "nickname": "小明"},
        "message": [{"type": "text", "data": {"text": enhanced}}],
    }])
    assert session[0]["content"] == enhanced
    assert online == enhanced


def test_online_history_unknown_sender_falls_back_to_user_id():
    text = format_online_history([{
        "time": 1788342260, "message_id": 1,
        "sender": {"user_id": 30003},
        "message": [{"type": "text", "data": {"text": "hi"}}],
    }])
    assert text.endswith("30003: hi")
