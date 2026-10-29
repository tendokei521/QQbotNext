"""行为规则段（``app.agent.persona.rules``）与组装接线（``assembly.build_behavior``）的护栏测试。

这批测试守的是三条**会直接让拟人化反噬**的红线：

1. **提示词里点名的工具必须真实存在**：写进提示词的每个工具名都得在本轮 ``available_tools``
   里，否则模型会去调不存在的工具（或者以为能力被禁用）；
2. **能力关掉，对应句子必须整句消失**（不是留空壳）；
3. **``assembly_profile=legacy`` 下输出逐字不变**：改造必须可一键回退。

除工具名子集外，只断言"结构/包含关系"，不对全文做脆弱的逐字快照——文案会持续打磨。
"""

from __future__ import annotations

import re

from app.agent.assembly.assembler import PromptRequest, assembly_profile
from app.agent.persona import rules as persona_rules

#: 工具名形态：小写字母开头、至少含一个下划线（``memory_save`` / ``expand_message`` …）。
#: 正文里的英文单词（Markdown / QQ / AI）不含下划线，因此不会被误抓。
_TOOL_TOKEN_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")

ALL_TOOLS = {
    "send_message",
    "finish",
    "send_poke",
    "set_msg_emoji_like",
    "tavily_search",
    "expand_message",
    "expand_recent",
    "expand_user",
    "expand_image",
    "get_chat_history",
    "get_current_session",
    "schedule_task",
    "knowledge_search",
    "memory_save",
    "memory_recall",
    "memory_correct",
    "memory_delete",
}

EMPTY_CONFIG: dict = {}

#: 发言通道生效的完整条件（档位 + 开关 + 流式）——多处复用，避免各写一遍而漂移
SPEECH_CONFIG: dict = {
    "assembly_profile": "qq_agent",
    "speech_via_tool": True,
    "stream_output": True,
}


def _render(tools=None, config=None, modalities=None) -> str:
    return persona_rules.render(
        None, tools, config if config is not None else EMPTY_CONFIG, modalities
    )


def _tool_tokens(text: str) -> set[str]:
    return set(_TOOL_TOKEN_RE.findall(text))


def test_rendered_tool_names_are_real_tools():
    """护栏 1：提示词里出现的工具名 ⊆ 本轮可用工具。"""
    text = _render(ALL_TOOLS)
    stray = _tool_tokens(text) - ALL_TOOLS
    assert not stray, f"提示词点名了本轮不存在的工具: {sorted(stray)}"


def test_every_segment_present_with_full_capabilities():
    text = _render(ALL_TOOLS)
    for header in (
        "【安全规则",
        "【工作方式】",
        "【反 AI 味",
        "【保持主体性】",
        "【该说/不该说】",
        "【群聊不是客服队列】",
        "【像真人一样】",
        "【不要当群管家/主持人】",
        "【引用与点名",
        "【轻量记忆",
        "【表情与拍一拍",
        "【QQ 场景规则】",
        "【发送与汇报禁令",
    ):
        assert header in text, f"缺少行为段落: {header}"


def test_segment_order_is_stable():
    """硬的约束在前：安全 → 工作方式 → 反AI味 → … → 禁令。"""
    text = _render(ALL_TOOLS)
    order = [
        "【安全规则",
        "【工作方式】",
        "【反 AI 味",
        "【保持主体性】",
        "【该说/不该说】",
        "【群聊不是客服队列】",
        "【像真人一样】",
        "【不要当群管家/主持人】",
        "【引用与点名",
        "【轻量记忆",
        "【表情与拍一拍",
        "【QQ 场景规则】",
        "【发送与汇报禁令",
    ]
    positions = [text.index(h) for h in order]
    assert positions == sorted(positions), "行为段顺序被打乱"


def test_speech_contract_follows_capability():
    """护栏 2（发言通道）：文本是否会被发出，由发言通道是否生效决定，两者不可混说。

    生效条件只剩 ``speech_via_tool`` 一个开关（档位/流式不再是门槛，见
    ``speech.speech_via_tool``）——提示词必须与运行时同一口径。
    """
    by_text = _render(ALL_TOOLS, {"speech_via_tool": False})
    assert "直接发到" in by_text
    assert "不会发到 QQ" not in by_text
    assert "send_message" not in by_text, "文本即发言时不应教模型调 send_message"

    # 只开开关即可（不再要求 qq_agent / 流式）
    half = _render(ALL_TOOLS, {"speech_via_tool": True})
    assert "不会发到 QQ" in half
    assert "send_message" in half

    by_tool = _render(ALL_TOOLS, SPEECH_CONFIG)
    assert "不会发到 QQ" in by_tool
    assert "send_message" in by_tool


def test_missing_capabilities_drop_their_sentences():
    """护栏 2：能力关掉 → 对应句子整句消失（不留空壳）。"""
    bare = {"get_current_session"}
    text = _render(bare, {"image_understanding_enable": False})
    assert "tavily_search" not in text
    assert "expand_message" not in text
    assert "set_msg_emoji_like" not in text
    assert "send_poke" not in text
    assert "memory_save" not in text
    assert "get_chat_history" not in text
    assert "【表情与拍一拍" not in text, "没有贴表情/拍一拍工具时整段不该出现"
    assert "【轻量记忆" not in text, "没有记忆工具时整段不该出现"
    assert "看不到图" in text, "没有视觉能力时要明确说看不到图（不能装作看得见）"
    assert "你没有联网能力" in text


def test_work_protocol_numbering_stays_consecutive():
    """能力少一项就少一条，但编号必须连续（跳号会让模型猜"第 N 条被删了什么"）。"""
    text = _render({"get_current_session"}, {"image_understanding_enable": False})
    work = text.split("【工作方式】", 1)[1].split("\n\n【", 1)[0]
    numbers = [
        int(line.split(".", 1)[0])
        for line in work.splitlines()
        if line[:1].isdigit() and "." in line[:3]
    ]
    assert numbers == list(range(1, len(numbers) + 1)), numbers


def test_search_and_vision_flip_with_capability():
    no_search = _render({"get_current_session"}, {"image_understanding_enable": True})
    assert "你没有联网能力" in no_search
    assert "不要编造图片内容" in no_search

    with_search = _render({"tavily_search"}, {"image_understanding_enable": False})
    assert "tavily_search" in with_search
    assert "看不到图" in with_search


def test_quote_channel_follows_tool_mode():
    """引用通道：工具模式讲参数名，文本模式讲 [reply]/[@QQ]。"""
    by_tool = _render(ALL_TOOLS, SPEECH_CONFIG)
    assert "reply_to_message_id" in by_tool
    assert "[reply]" not in by_tool

    by_text = _render(ALL_TOOLS, {"speech_via_tool": False})
    assert "[reply]" in by_text
    assert "reply_to_message_id" not in by_text

    disabled = _render(ALL_TOOLS, {"outbound_directive_enable": False})
    assert "【引用与点名" not in disabled


def test_participation_and_emoji_level_rewrite_lines():
    low = _render(ALL_TOOLS, {"participation": "low"})
    high = _render(ALL_TOOLS, {"participation": "high"})
    assert "安静型" in low and "活跃型" in high
    assert low != high

    level0 = _render(ALL_TOOLS, {"emoji_react_level": 0})
    level3 = _render(ALL_TOOLS, {"emoji_react_level": 3})
    assert "备选项" in level0
    assert "表情爱好者" in level3


def test_override_keeps_safety_and_work_protocol():
    """覆盖模式：用户文本替换行为段，但安全规则与工作方式强制保留。"""
    caps = persona_rules.detect_caps(ALL_TOOLS, EMPTY_CONFIG)
    text = persona_rules.override_with_safety("你是一只猫，只会喵。", caps)
    assert text.startswith("你是一只猫")
    assert "【安全规则" in text
    assert "【工作方式】" in text
    assert "【反 AI 味" not in text


def _request(**overrides) -> PromptRequest:
    """最小 PromptRequest：只喂装配需要的字段，config 用 dict（``cfg`` 支持 dict.get）。"""
    config = {"system_prompt": "你是测试角色。", "reply_cooldown": 1}
    config.update(overrides.pop("config", {}))
    req = PromptRequest(
        session_id="group_1",
        is_private=False,
        user_text="在吗",
        config=config,
    )
    for key, value in overrides.items():
        setattr(req, key, value)
    return req


def test_legacy_profile_injects_no_behavior_block():
    """护栏 3：默认档位下行为块整块消失（改造前行为逐字不变的保证）。"""
    req = _request(config={"assembly_profile": "legacy"})
    req.available_tools = ALL_TOOLS
    assert assembly_profile(req) == "legacy"
    names = [name for name, _ in _block_names(req)]
    assert "behavior" not in names


def _block_names(req):
    """只返回**实际产出内容**的块名（``build_all_blocks`` 会把空块也列出来）。"""
    from app.agent.assembly.assembler import build_all_blocks

    return [(block.name, messages) for block, messages in build_all_blocks(req) if messages]


def test_qq_agent_profile_injects_behavior_after_persona():
    req = _request(config={"assembly_profile": "qq_agent"})
    req.available_tools = ALL_TOOLS
    names = [name for name, _ in _block_names(req)]
    assert "behavior" in names
    assert names.index("behavior") == names.index("persona") + 1
    text = "".join(
        str(m.get("content") or "")
        for name, messages in _block_names(req)
        if name == "behavior"
        for m in messages
    )
    assert "【安全规则" in text
    assert "【工作方式】" in text


def test_behavior_block_can_be_disabled():
    req = _request(
        config={"assembly_profile": "qq_agent", "behavior_rules_enable": False}
    )
    req.available_tools = ALL_TOOLS
    names = [name for name, _ in _block_names(req)]
    assert "behavior" not in names


def test_proactive_block_dedupes_in_qq_agent_profile():
    """行为段已讲通用用法：``qq_agent`` 档下「主动性」块不再重复引用/戳一戳的用法。"""
    from app.agent.assembly.prompt import build_proactive_instruction

    tools = ALL_TOOLS
    legacy = build_proactive_instruction({"proactive_prompt_enable": True}, "", available_tools=tools)
    deduped = build_proactive_instruction(
        {"proactive_prompt_enable": True}, "", available_tools=tools, dedupe=True
    )
    assert legacy and deduped
    assert "[reply]" not in deduped
    assert "send_poke" not in deduped
    assert "上下文补全" in deduped
    # 【未展开】标记语义归行为段（persona_rules.work_protocol 第 6 条），不再重复
    assert "未展开:用户123" not in deduped
    assert len(deduped) < len(legacy)
