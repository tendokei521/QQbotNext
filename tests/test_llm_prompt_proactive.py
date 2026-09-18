"""「主动性」提示块测试。

约定：提示词部分只增加**一块** system（`proactive_instruction`），本文件同时锁住
「块的存在性 / 单一性 / 按能力裁剪 / 意图补强 / 可关闭」这几条不变量。
"""

from types import SimpleNamespace

from app.llm.prompt import (
    PROACTIVE_ENV_LINE,
    PROACTIVE_ENV_NUDGE,
    PROACTIVE_FOOTER_LINE,
    PROACTIVE_FOOTER_UNRESOLVED_LINE,
    PROACTIVE_HISTORY_LINE,
    PROACTIVE_HISTORY_NUDGE,
    PROACTIVE_POKE_LINE,
    PROACTIVE_QUOTE_LINE,
    PROACTIVE_UNRESOLVED_LINE,
    build_messages,
    build_proactive_instruction,
    env_intent,
    env_line,
    history_intent,
)

ALL_TOOLS = {"get_chat_history", "send_poke", "get_current_session", "schedule_task"}
HISTORY_ONLY = {"get_chat_history", "get_current_session"}
POKE_ONLY = {"send_poke"}
ENV_ONLY = {"get_current_session"}
EXPAND_TOOLS = {"expand_recent", "expand_message", "expand_user"}
ENV_WITH_EXPAND = {"get_current_session"} | EXPAND_TOOLS
HISTORY_NO_ENV = {"get_chat_history"}   # 有历史能力但没有任何环境能力
# 与环境/历史/戳一戳都无关的能力：不应触发任何一行
UNRELATED_TOOLS = {"schedule_task"}


def _cfg(**over):
    base = {
        "proactive_prompt_enable": True,
        "proactive_history_intent_nudge": True,
        "outbound_directive_enable": True,
    }
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


def test_no_block_when_no_relevant_capability():
    # 与环境/历史/戳一戳都无关的能力，且输出通道关闭 → 整块不注入
    assert build_proactive_instruction(
        _cfg(outbound_directive_enable=False), "你好", available_tools=UNRELATED_TOOLS
    ) is None


def test_quote_line_only_when_outbound_channel_enabled():
    on = build_proactive_instruction(_cfg(), "", available_tools=UNRELATED_TOOLS)
    off = build_proactive_instruction(_cfg(outbound_directive_enable=False), "", available_tools=UNRELATED_TOOLS)

    assert PROACTIVE_QUOTE_LINE in on
    assert off is None  # 无环境/历史/戳 + 通道关闭 → 无块


# ---------- 环境感知 ----------


def test_env_line_added_when_session_tool_available():
    """get_current_session 是"聊天环境信息"的工具，必须在场时被讲出来。"""
    block = build_proactive_instruction(_cfg(outbound_directive_enable=False), "", available_tools=ENV_ONLY)

    assert block is not None
    assert env_line(False) in block
    assert "get_current_session" in block
    assert "expand_message" not in block  # 本轮没有该工具就不许提


def test_env_line_names_expand_tools_when_available():
    block = build_proactive_instruction(_cfg(), "", available_tools=ENV_WITH_EXPAND)

    assert env_line(True) in block
    for name in ("expand_recent", "expand_message", "expand_user"):
        assert name in block
    assert PROACTIVE_UNRESOLVED_LINE in block


def test_env_line_absent_without_env_capability():
    block = build_proactive_instruction(_cfg(), "", available_tools=HISTORY_NO_ENV)

    assert "先自己取" not in block
    assert PROACTIVE_UNRESOLVED_LINE not in block


def test_unresolved_line_requires_expand_tool():
    with_expand = build_proactive_instruction(_cfg(), "", available_tools=ENV_WITH_EXPAND)
    without_expand = build_proactive_instruction(_cfg(), "", available_tools=ENV_ONLY)

    assert PROACTIVE_UNRESOLVED_LINE in with_expand
    assert PROACTIVE_UNRESOLVED_LINE not in without_expand
    # 无未展开能力时，footer 的例外条款也不该出现（避免承诺不存在的手段）
    assert PROACTIVE_FOOTER_UNRESOLVED_LINE in with_expand
    assert PROACTIVE_FOOTER_UNRESOLVED_LINE not in without_expand


def test_env_can_be_disabled_independently():
    off_env = build_proactive_instruction(
        _cfg(proactive_env_prompt_enable=False), "", available_tools=ENV_WITH_EXPAND
    )
    off_marker = build_proactive_instruction(
        _cfg(proactive_unresolved_prompt_enable=False), "", available_tools=ENV_WITH_EXPAND
    )

    assert "先自己取" not in off_env
    assert PROACTIVE_UNRESOLVED_LINE in off_env  # 标记行独立于环境行
    assert PROACTIVE_UNRESOLVED_LINE not in off_marker
    assert "先自己取" in off_marker


def test_unknown_tools_keeps_both_lines():
    """不传 available_tools（旧调用方/单测）时保留完整协议。"""
    block = build_proactive_instruction(_cfg(), "")
    assert PROACTIVE_HISTORY_LINE in block and PROACTIVE_POKE_LINE in block
    assert PROACTIVE_QUOTE_LINE in block
    assert "先自己取" in block and PROACTIVE_UNRESOLVED_LINE in block


# ---------- 意图补强（仍属同一块） ----------


def test_history_intent_detection_is_conservative():
    for text in ["刚才我说了什么", "之前聊到哪了", "你指的是哪条消息", "还记得我们约的事吗", "翻一下聊天记录"]:
        assert history_intent(text), text
    # 不能把「帮我记录一下」误判成查历史
    for text in ["帮我记录一下这件事", "记录一下我的生日", "明天下午三点提醒我"]:
        assert not history_intent(text), text


def test_env_intent_detection_is_conservative():
    for text in ["这个群是干什么的", "群里刚才谁在说游戏", "你知道张三这个人吗", "上面那条消息说的是啥", "你被@了吗"]:
        assert env_intent(text), text
    for text in ["今天天气不错", "帮我写个周报", "明天下午三点提醒我", "1加1等于几"]:
        assert not env_intent(text), text


def test_env_nudge_added_inside_same_block_on_env_intent():
    block = build_proactive_instruction(_cfg(), "这个群是干什么的", available_tools=ENV_WITH_EXPAND)

    assert PROACTIVE_ENV_NUDGE in block
    assert block.count("### 主动性") == 1

    plain = build_proactive_instruction(_cfg(), "今天天气不错", available_tools=ENV_WITH_EXPAND)
    assert PROACTIVE_ENV_NUDGE not in plain

    off = build_proactive_instruction(
        _cfg(proactive_env_intent_nudge=False), "这个群是干什么的", available_tools=ENV_WITH_EXPAND
    )
    assert PROACTIVE_ENV_NUDGE not in off
    assert "先自己取" in off


def test_footer_keeps_soft_tone_but_exempts_unresolved():
    """footer 既要保住"别机械调用"，又要让"环境缺口"成为必须做的事。"""
    block = build_proactive_instruction(_cfg(), "", available_tools=ENV_WITH_EXPAND)

    assert "拿不准就不用" in block
    assert "必须先展开" in block
    assert "照常回答" in block  # 没有手段时不许回"我无法完整回答"


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
