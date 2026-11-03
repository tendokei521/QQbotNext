"""消息凭证格式契约（2026-11-03）。

锁三件事：

1. 每条消息的头部带 ``message_id``，不再逐条带 ``group_id/group_name``；
2. 群信息只出现在 L1 尾部「当前群聊」一行，且在「你的账号」之前；
3. 只有本轮存在 high/critical 的 OneBot 工具时，才注入「消息凭证」约束段。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.prompts.sections import build_identity_context, build_onebot_auth
from app.agent.message_assembly.history import render_history_entry
from app.agent.message_assembly.render import render_turn
from app.agent.session.model import BaseInfo, HistoryEntry


def _turn() -> str:
    return render_turn(
        message=[{"type": "text", "data": {"text": "早上好"}}],
        sender_id=20002,
        sender_name="小明",
        time_str="2026-11-03 19:00",
        message_id=123456,
    )


def test_turn_head_carries_message_id_not_group_fields():
    text = _turn()
    assert text.startswith("[time=2026-11-03 19:00,message_id=123456]")
    assert "group_id" not in text and "group_name" not in text


def test_history_head_carries_message_id():
    entry = HistoryEntry(
        role="user",
        message_id="777",
        base=BaseInfo(
            sender_id="20002",
            sender_name="小明",
            text="在吗",
            time=1788342260,
            group_id="466052056",
        ),
    )
    text = render_history_entry(entry, include_time=True)
    assert "message_id=777" in text
    assert "group_id" not in text


def _identity_req(*, group=True):
    group_obj = SimpleNamespace(group_id=466052056, group_name='测试"群"')
    req = SimpleNamespace(
        runtime=SimpleNamespace(bot_id="bot1", bot=SimpleNamespace(bot_id=42), config={}, config_service=None),
        config={},
        cfg=lambda key, default=None: default,
        group_id="466052056" if group else None,
        event=SimpleNamespace(group=group_obj) if group else None,
    )
    return req


def test_group_block_is_first_line_before_account():
    text = build_identity_context(_identity_req())[0]["content"]
    first = text.splitlines()[0]
    assert first.startswith("当前群聊")
    assert "group_id=466052056" in first
    assert '"测试\\"群\\""' in first  # 群名按 JSON 字符串转义，不能伪造标记
    assert text.splitlines()[1] == "你的账号：42"


def test_private_chat_has_no_group_block():
    text = build_identity_context(_identity_req(group=False))[0]["content"]
    assert text.startswith("你的账号：")
    assert "当前群聊" not in text


def test_auth_section_only_when_privileged_tool_available():
    req = SimpleNamespace(available_tools={"send_msg"})
    assert build_onebot_auth(req) == []
    req = SimpleNamespace(available_tools={"send_msg", "set_group_kick"})
    assert build_onebot_auth(req)[0]["role"] == "system"


def test_sender_name_goes_to_message_name_field_not_content():
    """方案 A：昵称只走 OpenAI message 的 ``name`` 字段，正文不再放任何发送者占位符。"""
    from app.agent.message_assembly import history as history_assembly
    from app.agent.prompts.sections import build_user

    entry = HistoryEntry(
        role="user",
        message_id="777",
        base=BaseInfo(sender_id="20002", sender_name="老师，今年的学费也是一次性交吗",
                      text="在吗", time=1788342260, group_id="466052056"),
    )
    from app.agent.context.group import format_history_for_llm

    rendered = format_history_for_llm(
        [entry], is_private=False, renderer=history_assembly.render_history_entry
    )
    assert rendered[0]["name"] == "老师，今年的学费也是一次性交吗"
    assert "老师，今年的学费也是一次性交吗" not in rendered[0]["content"]
    assert "[sender:" not in rendered[0]["content"]
    assert rendered[0]["content"].startswith("[time=")

    event = SimpleNamespace(
        message_type="group",
        user=SimpleNamespace(card="", nickname="老师，今年的学费也是一次性交吗"),
        group=SimpleNamespace(group_id=466052056, group_name="测试群"),
    )
    req = SimpleNamespace(
        event=event, is_private=False, user_text="[time=..,message_id=1]在吗",
        user_blocks=[], extra_user_content_parts=[],
    )
    msg = build_user(req)[0]
    assert msg["name"] == "老师，今年的学费也是一次性交吗"
    assert "老师，今年的学费也是一次性交吗" not in str(msg["content"])
