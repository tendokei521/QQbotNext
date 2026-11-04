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
    """方案 A：昵称走 OpenAI message 的 ``name`` 字段，正文只留 id 标记。"""
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
    assert "[sender:id=20002]" in rendered[0]["content"]

    event = SimpleNamespace(
        message_type="group",
        user=SimpleNamespace(card="", nickname="老师，今年的学费也是一次性交吗"),
        group=SimpleNamespace(group_id=466052056, group_name="测试群"),
    )
    req = SimpleNamespace(
        event=event, is_private=False, user_text="[time=..,message_id=1][sender:id=20002]在吗",
        user_blocks=[], extra_user_content_parts=[],
    )
    msg = build_user(req)[0]
    assert msg["name"] == "老师，今年的学费也是一次性交吗"
    assert "老师，今年的学费也是一次性交吗" not in str(msg["content"])


# ---------- 本轮消息的 message_id 字段（2026-11-04） ----------


def test_current_turn_message_carries_message_id_field_like_history():
    """本轮（最后一条 user）消息也要有独立 ``message_id`` 字段，与历史条目同口径。

    回归（线上）：历史条目由 ``with_message_ids`` 贴上 ``message_id`` 字段，本轮
    消息却只有正文头部的 ``[time=..,message_id=..]``：同一个 id 在请求里有两种
    形状，负 id 的消息在凭证鉴权时被判"不是数字 id"，模型只能从占位文本里抄。
    """
    from app.agent.assembly.assembler import PromptAssembler, PromptRequest

    event = SimpleNamespace(message_id="-1208685782")
    req = PromptRequest(
        event=event,
        config={"system_prompt": "你是助手", "prompt_preset": "chat"},
        user_text="[time=2026-11-04 19:40,message_id=-1208685782][sender:id=1]在吗",
    )
    messages = PromptAssembler().build(req)
    last = messages[-1]
    assert last["role"] == "user"
    assert last["message_id"] == "-1208685782"
    # 正文里的占位标记保留（模型读的是正文，字段给框架/调试视图校验）
    assert "message_id=-1208685782" in last["content"]


def test_current_turn_without_event_has_no_message_id_field():
    """无事件（主动消息 / 定时任务）没有"本轮消息 id"可贴：保持原样，不造字段。"""
    from app.agent.assembly.assembler import PromptAssembler, PromptRequest

    messages = PromptAssembler().build(
        PromptRequest(
            config={"system_prompt": "你是助手", "prompt_preset": "chat"},
            user_text="在吗",
        )
    )
    assert messages[-1]["role"] == "user"
    assert "message_id" not in messages[-1]
