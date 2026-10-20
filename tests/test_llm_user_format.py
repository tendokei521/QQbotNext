"""``assembly_profile=qq_agent`` 的 user 侧改造：单行格式 / 时间挪位 / 表情提示 / 格式图例。

守的是这三件容易悄悄回退的事：

1. ``legacy`` 档 user 侧**逐字不变**（六行元信息块、时间在首行）；
2. ``single_line`` 档本轮消息与历史条目同形（``昵称(QQ): 正文``），且时间挪到末尾——
   否则"本轮六行块 + 历史单行"的混搭会原样回来；
3. 消息格式说明随格式一起换（别一边发单行、一边解释"发送者：/发送了："）。
"""

from __future__ import annotations

import types

from app.domain.events import GroupMessageEvent, MessageSegment, UserInfo
from app.agent.assembly.assembler import PromptRequest, message_meta_instruction
from app.agent.assembly.enhance import format_user_context
from app.agent.assembly.prompt import (
    SINGLE_LINE_MESSAGE_META_INSTRUCTION,
    emoji_hint_line,
    user_message_format,
)

NL = chr(10)


def _group_event() -> GroupMessageEvent:
    return GroupMessageEvent(
        event_type="message_group",
        message_type="group",
        time=1,
        user_id=20002,
        self_id=778,
        message=[MessageSegment("text", {"text": "晚上吃什么"})],
        user=UserInfo(user_id=20002, nickname="小明"),
        group=types.SimpleNamespace(group_id=466052056),
    )


def _ctx(config: dict | None):
    from app.agent.loop.context import LlmContext

    runtime = types.SimpleNamespace(config=config or {}, bot_id=3437542570)
    ctx = LlmContext(event=_group_event(), runtime=runtime, bot=None, session_id="group_1")
    ctx.user_text = "晚上吃什么"
    ctx.state["user_context"] = {
        "sender": "小明(20002)",
        "mentioned": ["小红(30003)"],
        "quote": "在吗",
        "quote_sender": "小红(30003)",
        "sent_text": "晚上吃什么",
    }
    return ctx


async def test_legacy_profile_keeps_multi_line_block():
    """legacy 档：六行元信息块 + 时间在首行（改造前逐字不变）。"""
    ctx = _ctx({"assembly_profile": "legacy"})
    await format_user_context(ctx)
    lines = ctx.user_text.splitlines()
    assert lines[0].startswith("(时间：")
    assert "(当前群号: 466052056)" in lines
    assert "发送者：小明(20002)" in lines
    assert "发送了：晚上吃什么" in lines
    assert "提到了(用户名)：小红(30003)" in lines


async def test_single_line_profile_matches_history_format():
    """单一形态：群号一行 + 昵称(QQ): 正文 + 附注 + 时间，没有「发送者：/发送了：」。"""
    ctx = _ctx({"assembly_profile": "qq_agent", "user_message_format": "single_line"})
    await format_user_context(ctx)
    lines = ctx.user_text.splitlines()
    assert lines[0] == "(当前群号: 466052056)"
    assert "小明(20002): 晚上吃什么" in lines
    assert not any(line.startswith(("发送者：", "发送了：")) for line in lines)
    assert not lines[0].startswith("(时间：")
    assert lines[-1].startswith("(时间："), "时间应挪到末尾"
    assert "提到了(用户名)：小红(30003)" in ctx.user_text


async def test_time_position_head_restores_old_order():
    ctx = _ctx(
        {
            "assembly_profile": "qq_agent",
            "user_message_format": "single_line",
            "prompt_time_position": "head",
        }
    )
    await format_user_context(ctx)
    assert ctx.user_text.splitlines()[0].startswith("(时间：")


def test_format_helpers_default_to_legacy_off_profile():
    """档位没开时两个格式开关都不生效（可一键回退）。"""
    assert user_message_format({}) == "legacy"
    assert user_message_format({"assembly_profile": "qq_agent"}) == "single_line"
    assert user_message_format({"assembly_profile": "qq_agent", "user_message_format": "legacy"}) == "legacy"


def test_message_meta_legend_follows_format():
    """格式换了，说明也得换：发单行却解释「发送者：/发送了：」= 给模型一本错手册。"""
    base = {
        "assembly_profile": "qq_agent",
        "user_message_format": "single_line",
        "meta_sender_style": "legacy",
    }
    req = PromptRequest(session_id="group_1", is_private=False, user_text="在吗", config=base)
    req.ctx = types.SimpleNamespace(state={"message_meta_injected": True})
    assert message_meta_instruction(req) == SINGLE_LINE_MESSAGE_META_INSTRUCTION

    legacy = dict(base, user_message_format="legacy")
    req = PromptRequest(session_id="group_1", is_private=False, user_text="在吗", config=legacy)
    req.ctx = types.SimpleNamespace(state={"message_meta_injected": True})
    assert message_meta_instruction(req) != SINGLE_LINE_MESSAGE_META_INSTRUCTION


def test_message_meta_off_wins_over_profile():
    req = PromptRequest(
        session_id="group_1",
        is_private=False,
        user_text="在吗",
        config={"assembly_profile": "qq_agent", "meta_instruction_mode": "off"},
    )
    req.ctx = types.SimpleNamespace(state={"message_meta_injected": True})
    assert message_meta_instruction(req) is None


def test_emoji_hint_line_is_stable_and_names_tags():
    from app.agent.persona.emoji import available_tags

    tags = available_tags(20)
    line = emoji_hint_line(tags)
    assert line.startswith("【可用表情】")
    assert "赞" in line
    assert emoji_hint_line("") == ""
    assert emoji_hint_line(tags) == line, "同一标签表必须逐字稳定（前缀缓存）"
