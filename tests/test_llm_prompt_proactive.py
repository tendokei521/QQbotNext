"""「主动性」提示块测试。

约定：提示词部分只增加**一块** system（`proactive_instruction`），本文件同时锁住
「块的存在性 / 单一性 / 按能力裁剪 / 意图补强 / 可关闭」这几条不变量。
"""

from types import SimpleNamespace

from app.llm.prompt import (
    PROACTIVE_FOOTER_LINE,
    PROACTIVE_HISTORY_LINE,
    PROACTIVE_HISTORY_NUDGE,
    PROACTIVE_POKE_LINE,
    build_messages,
    build_proactive_instruction,
    history_intent,
)

ALL_TOOLS = {"get_chat_history", "send_poke", "get_current_session", "schedule_task"}
HISTORY_ONLY = {"get_chat_history", "get_current_session"}
POKE_ONLY = {"send_poke"}
NO_TOOLS = {"get_current_session"}


def _cfg(**over):
    base = {"proactive_prompt_enable": True, "proactive_history_intent_nudge": True}
    base.update(over)
    return base


# ---------- 唯一性与可关闭 ----------


def test_block_is_added_as_exactly_one_system_message():
    messages = build_messages(
        system_prompt="你是助手",
        user_text="在吗",
        with_schedule_instruction=False,
        proactive_instruction=build_proactive_instruction(_cfg(), "在吗", available_tools=ALL_TOOLS),
    )

    blobs = [m for m in messages if m["role"] == "system" and m["content"].startswith("### 主动性")]
    assert len(blobs) == 1
    assert messages[-1] == {"role": "user", "content": "在吗"}


def test_block_can_be_disabled():
    assert build_proactive_instruction(_cfg(proactive_prompt_enable=False), "", available_tools=ALL_TOOLS) is None
    messages = build_messages(system_prompt="s", user_text="u", with_schedule_instruction=False)
    assert not [m for m in messages if m["role"] == "system" and m["content"].startswith("### 主动性")]


# ---------- 按能力裁剪 ----------


def test_history_line_only_when_history_tool_available():
    history_block = build_proactive_instruction(_cfg(), "", available_tools=HISTORY_ONLY)
    poke_block = build_proactive_instruction(_cfg(), "", available_tools=POKE_ONLY)

    assert PROACTIVE_HISTORY_LINE in history_block
    assert PROACTIVE_POKE_LINE not in history_block
    assert PROACTIVE_POKE_LINE in poke_block
    assert PROACTIVE_HISTORY_LINE not in poke_block
    # 尾部免责声明两块都有
    assert PROACTIVE_FOOTER_LINE in history_block and PROACTIVE_FOOTER_LINE in poke_block


def test_no_block_when_no_relevant_tool():
    assert build_proactive_instruction(_cfg(), "你好", available_tools=NO_TOOLS) is None


def test_unknown_tools_keeps_both_lines():
    """不传 available_tools（旧调用方/单测）时保留完整协议。"""
    block = build_proactive_instruction(_cfg(), "")
    assert PROACTIVE_HISTORY_LINE in block and PROACTIVE_POKE_LINE in block


# ---------- 意图补强（仍属同一块） ----------


def test_history_intent_detection_is_conservative():
    for text in ["刚才我说了什么", "之前聊到哪了", "你指的是哪条消息", "还记得我们约的事吗", "翻一下聊天记录"]:
        assert history_intent(text), text
    # 不能把「帮我记录一下」误判成查历史
    for text in ["帮我记录一下这件事", "记录一下我的生日", "明天下午三点提醒我"]:
        assert not history_intent(text), text


def test_nudge_added_inside_same_block_on_history_intent():
    block = build_proactive_instruction(_cfg(), "刚才我说了什么来着", available_tools=ALL_TOOLS)

    assert PROACTIVE_HISTORY_NUDGE in block
    assert block.count("### 主动性") == 1  # 仍是一块


def test_nudge_not_added_without_intent_or_when_disabled():
    plain = build_proactive_instruction(_cfg(), "今天天气不错", available_tools=ALL_TOOLS)
    assert PROACTIVE_HISTORY_NUDGE not in plain

    off = build_proactive_instruction(
        _cfg(proactive_history_intent_nudge=False), "刚才我说了什么", available_tools=ALL_TOOLS
    )
    assert PROACTIVE_HISTORY_NUDGE not in off
    assert PROACTIVE_HISTORY_LINE in off


# ---------- chat 侧接入 ----------


def test_chat_helper_uses_raw_user_text_and_available_tools():
    from app.llm.chat import _proactive_instruction

    ctx = SimpleNamespace(state={"user_context": {"sent_text": "刚才我说了什么"}})
    specs = [SimpleNamespace(name="get_chat_history"), SimpleNamespace(name="send_poke")]

    block = _proactive_instruction(_cfg(), specs, "(时间：...) 刚才我说了什么", ctx)

    assert PROACTIVE_HISTORY_NUDGE in block
    assert block.count("### 主动性") == 1
