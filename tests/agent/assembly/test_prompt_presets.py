"""提示词方案（``app.agent.assembly.presets``）的护栏测试。

守的是四条**会让"选个方案"变成事故**的红线：

1. **默认不生效**：``prompt_preset`` 留空/拼错时装配结果与改造前逐字一致，
   存量 bot 升级后一个字符都不变（选了才接管，清空即回退）；
2. **安全段不可被方案删掉**：三套方案都必须含「安全规则」，用户写了
   ``behavior_rules_override`` 也一样——只能整体替换风格段落，换不掉底线；
3. **工作方式段必须说真话**：``speech_by_tool`` 开/关对应的措辞不能错位
   （说错会让模型整轮静默或把思考发出去）；
4. **能力裁剪在方案下仍生效**：方案正文里不允许出现本轮不存在的工具名。

除此之外只断言"结构/包含关系"，不对全文做逐字快照——文案会持续打磨。
"""

from __future__ import annotations

import re

from app.agent.assembly import presets
from app.agent.assembly.assembler import PromptAssembler, PromptRequest
from app.agent.persona import rules as persona_rules

#: 工具名形态：小写字母开头、至少含一个下划线（``memory_save`` / ``expand_message`` …）
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
    "memory_save",
    "memory_recall",
}

#: 发言通道生效的完整条件（档位 + 开关 + 流式）
SPEECH_CONFIG = {"assembly_profile": "qq_agent", "speech_via_tool": True, "stream_output": True}


def _behavior(req: PromptRequest) -> str:
    """取本轮 behavior 块正文（没有该块时返回空串）。"""
    for msg in PromptAssembler().build(req):
        if msg.get("role") == "system" and "【安全规则" in str(msg.get("content") or ""):
            return str(msg["content"])
    return ""


def _tool_tokens(text: str) -> set[str]:
    return set(_TOOL_TOKEN_RE.findall(text))


# ==================== 1. 默认不生效 ====================


def test_unset_preset_keeps_legacy_assembly_verbatim():
    """未选方案时装配结果与"没有这个键"逐字一致（纯增量，零回归）。"""
    base = {"system_prompt": "你是助手", "outbound_directive_enable": True}
    without = PromptAssembler().build(PromptRequest(config=dict(base), user_text="hi"))
    empty = PromptAssembler().build(
        PromptRequest(config={**base, "prompt_preset": ""}, user_text="hi")
    )
    assert without == empty


def test_unknown_preset_is_treated_as_unset():
    """拼错方案名只会「不生效」，不会「半生效」。"""
    assert presets.resolve({"prompt_preset": "role_play"}) is None
    assert presets.resolve({"prompt_preset": "  "}) is None
    assert presets.resolve({}) is None
    assert presets.resolve(None) is None


def test_preset_keys_are_exactly_the_three_ui_options():
    assert presets.keys() == ("chat", "roleplay", "assistant")
    labels = [p["label"] for p in presets.describe()]
    assert labels == ["聊天", "角色扮演", "助手"]


# ==================== 2. 三套互不相同，但底线相同 ====================


def test_three_presets_render_distinct_behavior():
    texts = {
        p.key: presets.render(p, available_tools=ALL_TOOLS, config={"participation": "medium"})
        for p in presets.PRESETS
    }
    assert all(texts.values())
    assert len(set(texts.values())) == 3, "三套方案的行为段必须互不相同"


def test_chat_matches_builtin_rules_verbatim():
    """``chat`` 就是改造前的口径：逐字等于 ``persona_rules.render``。"""
    cfg = {"participation": "medium"}
    assert presets.render(
        presets.CHAT, available_tools=ALL_TOOLS, config=cfg
    ) == persona_rules.render(None, ALL_TOOLS, cfg)


def test_every_preset_keeps_security_rules():
    for preset in presets.PRESETS:
        text = presets.render(preset, available_tools=ALL_TOOLS, config={})
        assert "【安全规则（最高优先级，不可违反）】" in text, f"{preset.key} 丢了安全段"


def test_override_cannot_drop_security_or_work_protocol():
    """``behavior_rules_override`` 能换风格段落，但换不掉安全段与工作方式段。"""
    for preset in presets.PRESETS:
        text = presets.render(
            preset,
            available_tools=ALL_TOOLS,
            config={},
            override="自定义行为规则正文",
        )
        assert text.startswith("自定义行为规则正文")
        assert "【安全规则（最高优先级，不可违反）】" in text
        assert "工作方式" in text


def test_preset_style_hints_differ():
    """三套方案的定位差异要能在正文里认出来（防"复制一份没改"）。"""
    cfg = {"participation": "medium"}
    chat = presets.render(presets.CHAT, available_tools=ALL_TOOLS, config=cfg)
    roleplay = presets.render(presets.ROLEPLAY, available_tools=ALL_TOOLS, config=cfg)
    assistant = presets.render(presets.ASSISTANT, available_tools=ALL_TOOLS, config=cfg)
    assert "反 AI 味" in chat
    assert "角色一致性" in roleplay and "跳出" in roleplay
    assert "有求必应" in assistant


# ==================== 3. 工作方式段说真话 ====================


def test_work_protocol_tracks_real_speech_channel():
    """发言通道开/关时，三套方案都必须换到对应措辞（不许两套说辞并存或错位）。"""
    for preset in presets.PRESETS:
        by_tool = presets.render(preset, available_tools=ALL_TOOLS, config=dict(SPEECH_CONFIG))
        plain = presets.render(preset, available_tools=ALL_TOOLS, config={})
        assert "只是思考" in by_tool, f"{preset.key}: 发言走工具时没说真话"
        assert "会直接发到" in plain, f"{preset.key}: 文本直发时没说真话"
        assert "只是思考" not in plain


# ==================== 4. 能力裁剪仍然生效 ====================


def test_no_phantom_tools_when_nothing_available():
    """本轮没有任何工具时，三套方案正文里不该出现任何工具名。"""
    for preset in presets.PRESETS:
        text = presets.render(preset, available_tools=set(), config={})
        assert _tool_tokens(text) == set(), f"{preset.key} 提到了本轮不存在的工具"


def test_tool_tokens_are_subset_of_available_tools():
    """有工具时，方案点名的工具也必须在本轮可用集合里。"""
    tools = {"send_message", "set_msg_emoji_like", "get_chat_history"}
    for preset in presets.PRESETS:
        text = presets.render(preset, available_tools=tools, config={})
        assert _tool_tokens(text) <= tools, f"{preset.key} 提到了不可用的工具"


# ==================== 5. 其余框架文案跟着方案走 ====================


def test_schedule_instruction_follows_preset():
    texts = {p.key: p.schedule_instruction for p in presets.PRESETS}
    assert len(set(texts.values())) == 3
    assert texts["chat"] == presets.SCHEDULE_INSTRUCTION
    for preset in presets.PRESETS:
        req = PromptRequest(
            config={"prompt_preset": preset.key, "system_prompt": "人设"},
            user_text="hi",
            available_tools=ALL_TOOLS,
        )
        contents = [str(m["content"]) for m in PromptAssembler().build(req)]
        assert preset.schedule_instruction in contents


def test_message_meta_matches_real_format():
    """单行/多行格式各取各的说明：不能讲一套、用的是另一套。"""
    for preset in presets.PRESETS:
        single = PromptRequest(
            config={"prompt_preset": preset.key, "user_message_format": "single_line"},
            user_text="hi",
            available_tools=ALL_TOOLS,
        )
        legacy = PromptRequest(
            config={"prompt_preset": preset.key, "user_message_format": "legacy"},
            user_text="hi",
            available_tools=ALL_TOOLS,
        )
        assert preset.message_meta_single_line in [m["content"] for m in PromptAssembler().build(single)]
        assert preset.message_meta_legacy in [m["content"] for m in PromptAssembler().build(legacy)]
        assert preset.message_meta_single_line != preset.message_meta_legacy


def test_proactive_lines_override_only_changes_listed_lines():
    """方案的主动性覆盖只改它列出的行，能力裁剪与其余行不受影响。"""
    from app.agent.assembly.prompt import PROACTIVE_LINES, build_proactive_instruction

    override = presets.ROLEPLAY.proactive_lines
    not_overridden = [k for k in PROACTIVE_LINES if k not in override]
    assert not_overridden, "方案至少要留下一批未覆盖的行"

    cfg = {"prompt_preset": "roleplay"}
    text = build_proactive_instruction(
        cfg,
        "你好",
        available_tools={"send_message", "send_poke", "get_chat_history"},
        lines=override,
    )
    assert text is not None
    assert override["poke"].strip() in text
    # 未覆盖的行仍是内置原文（这里用 history 行验证：它在 roleplay 侧没被改）
    assert "history" in not_overridden
    assert PROACTIVE_LINES["history"].strip() in text


def test_emoji_hint_follows_preset():
    from app.agent.assembly.prompt import emoji_hint_line

    tags = "赞 / 笑哭 / 狗头"
    variants = {p.key: p.emoji_hint(tags) for p in presets.PRESETS}
    assert len(set(variants.values())) == 3
    assert tags in variants["roleplay"] and tags in variants["assistant"]
    assert variants["chat"] == emoji_hint_line(tags)


# ==================== 6. 装配接线 ====================


def test_selecting_preset_injects_behavior_without_qq_agent_profile():
    """选了方案就该有行为段：不再要求 ``assembly_profile=qq_agent``。

    否则用户在本体页面点了"角色扮演"，却因为模块默认档位是 legacy 而拿不到行为段，
    表现为"点了没反应"。
    """
    req = PromptRequest(
        config={"prompt_preset": "assistant", "assembly_profile": "legacy", "system_prompt": "人设"},
        user_text="hi",
        available_tools=ALL_TOOLS,
    )
    assert "【有求必应，但不糊弄】" in _behavior(req)


def test_preset_does_not_touch_persona_block():
    """人格块永远来自 system_prompt / 角色预设，方案不参与。"""
    for preset in presets.PRESETS:
        req = PromptRequest(
            config={"prompt_preset": preset.key, "system_prompt": "我是独特的人设正文"},
            user_text="hi",
            available_tools=ALL_TOOLS,
        )
        first = PromptAssembler().build(req)[0]
        assert first == {"role": "system", "content": "我是独特的人设正文"}


def test_folding_style_still_works_with_preset():
    """``system_prompt_style=single`` 与方案正交：折成一条后段落在里面。"""
    req = PromptRequest(
        config={
            "prompt_preset": "roleplay",
            "system_prompt": "人设",
            "system_prompt_style": "single",
            "outbound_directive_enable": True,
        },
        user_text="hi",
        available_tools=ALL_TOOLS,
    )
    messages = PromptAssembler().build(req)
    systems = [m for m in messages if m["role"] == "system"]
    assert len(systems) == 1
    assert "【角色一致性：不许跳出】" in systems[0]["content"]
