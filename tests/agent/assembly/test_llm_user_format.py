"""消息组装渲染契约（精简后的 8 键）。

覆盖：
- `user_message_format`：single_line / multi_line 两种形态；
- `prompt_time_position`：off / head / tail；
- `sender_label`：off / compact / verbose / masked；
- `quote_and_at`：off / mark（不取正文）/ enrich（取正文与昵称）；
- `include_sent`（仅多行块生效）与 `auto_disambiguation`（决定 L1 说明段）。
"""

from __future__ import annotations

import types

from app.agent.assembly.assembler import PromptRequest
from app.agent.loop.context import LlmContext
from app.domain.events import GroupMessageEvent, MessageSegment, UserInfo
from app.plugins.llm_prompt.src.meta import collect_user_context, format_user_context
from app.plugins.llm_prompt.src.prompt import (
    SINGLE_LINE_MESSAGE_META_INSTRUCTION,
    message_meta_instruction,
)


class _FakeBot:
    def __init__(self):
        self.calls = 0

    async def get_msg(self, message_id):
        self.calls += 1
        return {
            "data": {
                "sender": {"card": "小红", "user_id": 10002},
                "message": [{"type": "text", "data": {"text": "昨晚那部剧结局真离谱"}}],
            }
        }

    async def get_group_member_info(self, group_id, user_id):
        return {"data": {"card": "小明", "nickname": "小明"}}


def _event(bot=None) -> GroupMessageEvent:
    return GroupMessageEvent(
        event_type="message_group",
        message_type="group",
        time=1730000000,
        user_id=20002,
        self_id=3437542570,
        message=[
            MessageSegment("reply", {"id": "456"}),
            MessageSegment("text", {"text": "刚那条是什么意思？"}),
        ],
        user=UserInfo(user_id=20002, nickname="小明"),
        group=types.SimpleNamespace(group_id=466052056),
        bot=bot,
    )


def _ctx(config: dict, bot=None) -> LlmContext:
    runtime = types.SimpleNamespace(config=config, bot_id=3437542570)
    ctx = LlmContext(event=_event(bot), runtime=runtime, bot=bot, session_id="group_1")
    ctx.user_text = "刚那条是什么意思？"
    return ctx


async def _render(config: dict, bot=None) -> LlmContext:
    ctx = _ctx(config, bot)
    await collect_user_context(ctx)
    await format_user_context(ctx)
    return ctx


async def test_single_line_default_matches_history_format():
    ctx = await _render({}, _FakeBot())
    lines = ctx.user_text.splitlines()
    assert lines[0].startswith("(当前群号: 466052056)")
    assert "小明(20002): 刚那条是什么意思？" in ctx.user_text
    assert "(时间：" in ctx.user_text, "默认时间放尾部"
    assert ctx.user_text.index("小明(20002)") < ctx.user_text.index("(时间：")


async def test_multi_line_block_with_sent_marker():
    ctx = await _render({"user_message_format": "multi_line"}, _FakeBot())
    assert "发送者：小明(20002)" in ctx.user_text
    assert "发送了：刚那条是什么意思？" in ctx.user_text


async def test_multi_line_can_hide_sent_text():
    ctx = await _render(
        {"user_message_format": "multi_line", "include_sent": False}, _FakeBot()
    )
    assert "发送了：" not in ctx.user_text
    assert "发送者：小明(20002)" in ctx.user_text


async def test_time_position_off_and_head():
    off = await _render({"prompt_time_position": "off"}, _FakeBot())
    assert "(时间：" not in off.user_text
    head = await _render({"prompt_time_position": "head"}, _FakeBot())
    assert head.user_text.splitlines()[0].startswith("(时间：")


async def test_sender_label_off_and_masked():
    off = await _render({"sender_label": "off"}, _FakeBot())
    assert "小明" not in off.user_text
    masked = await _render({"sender_label": "masked"}, _FakeBot())
    assert "用户<20002>" in masked.user_text


async def test_quote_mark_does_not_fetch_body():
    bot = _FakeBot()
    ctx = await _render({"quote_and_at": "mark"}, bot)
    assert bot.calls == 0, "只标关系时不该请求被引用消息"
    assert "引用了：" in ctx.user_text


async def test_quote_enrich_fetches_body():
    bot = _FakeBot()
    ctx = await _render({"quote_and_at": "enrich"}, bot)
    assert bot.calls == 1
    assert "昨晚那部剧结局真离谱" in ctx.user_text


async def test_quote_off_drops_quote_and_mention():
    ctx = await _render({"quote_and_at": "off"}, _FakeBot())
    assert "引用了：" not in ctx.user_text


def _req(config: dict, injected: bool):
    req = PromptRequest(config=config)
    req.ctx = types.SimpleNamespace(state={"message_meta_injected": injected} if injected else {})
    return req


def test_legend_follows_format_and_can_be_disabled():
    assert message_meta_instruction(_req({}, False)) is None, "没注入元信息就不解释"
    assert message_meta_instruction(_req({}, True)) == SINGLE_LINE_MESSAGE_META_INSTRUCTION
    multi = message_meta_instruction(_req({"user_message_format": "multi_line"}, True))
    assert multi and "发送了" in multi
    assert message_meta_instruction(_req({"auto_disambiguation": False}, True)) is None
