"""提示词方案（``app.agent.assembly.presets``）的护栏测试。

守的是四条**会让"选个方案"变成事故**的红线：

1. **三选一，没有"不选"**：配置里带了这个键但为空/拼错时回落默认方案（chat），
   保证产品路径不会退回"没有行为规则段"的旧文案；只有完全不携带该键的库层调用
   才保留旧装配结果（测试/脚本直接构造 config）；
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

def _l1_text(req) -> str:
    """取 L1 系统提示词正文。

    层模型里 L1 是**一条** system（层内合并），排在 L2 人格之前，
    所以"第一条 system"就是它。
    """
    for msg in PromptAssembler().build(req):
        if msg.get("role") == "system":
            return str(msg.get("content") or "")
    return ""


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
SPEECH_CONFIG = {"reply_mode": "participation"}


def _behavior(req: PromptRequest) -> str:
    """取本轮 behavior 块正文（没有该块时返回空串）。"""
    for msg in PromptAssembler().build(req):
        if msg.get("role") == "system" and "【安全规则" in str(msg.get("content") or ""):
            return str(msg["content"])
    return ""


def _tool_tokens(text: str) -> set[str]:
    return set(_TOOL_TOKEN_RE.findall(text))


# ==================== 1. 默认不生效 ====================


def test_empty_preset_falls_back_to_default_chat():
    """带键但为空 → 按默认方案（chat）装配，不会退回"没有行为段"的旧文案。"""
    base = {"system_prompt": "你是助手", "outbound_directive_enable": True}
    empty = PromptAssembler().build(
        PromptRequest(config={**base, "prompt_preset": ""}, user_text="hi")
    )
    chat = PromptAssembler().build(
        PromptRequest(config={**base, "prompt_preset": "chat"}, user_text="hi")
    )
    assert empty == chat
    # 完全不携带该键的库层调用仍保留旧版结果（产品路径永远带键）
    without = PromptAssembler().build(PromptRequest(config=dict(base), user_text="hi"))
    assert without != empty


def test_unknown_preset_falls_back_to_default_chat():
    """拼错方案名 → 回落 chat，不会「不生效/半生效」。"""
    assert presets.resolve({"prompt_preset": "role_play"}) is presets.CHAT
    assert presets.resolve({"prompt_preset": "  "}) is presets.CHAT
    assert presets.resolve({"prompt_preset": ""}) is presets.CHAT
    # 不带键 / 非配置对象：保留库层旧行为（None）
    assert presets.resolve({}) is None
    assert presets.resolve(None) is None


class _GetOnlyConfig:
    """只有 ``get()`` 的配置门面——运行时真实传入的形状（``AgentConfig`` / ``ChainedConfig``）。

    它们**没有** ``__contains__``，所以"用 ``in`` 判断键是否存在"的写法会抛
    ``TypeError`` 并被吞掉、误判成"没有这个键"。历史上这里让三选一与
    「编辑系统提示词」在真实路径上全部静默失效（见
    commit 历史）。
    """

    def __init__(self, data: dict) -> None:
        self._data = dict(data)

    def get(self, key, default=None):
        return self._data.get(key, default)


def test_resolve_works_with_get_only_config_objects():
    """真实配置对象不是 dict：``resolve`` 绝不能依赖 ``in`` / ``[]`` 这类容器协议。"""
    base = {"system_prompt": "你是助手"}
    for config in (
        _GetOnlyConfig({**base, "prompt_preset": "chat"}),
        _GetOnlyConfig({**base, "prompt_preset": "roleplay"}),
        _GetOnlyConfig({**base, "prompt_preset": ""}),
        _GetOnlyConfig({**base, "prompt_preset": "拼错了"}),
    ):
        assert presets.resolve(config) is not None, f"{config.get('prompt_preset')!r} 没解析出方案"

    # 不带该键时仍是 None（库层旧行为）
    assert presets.resolve(_GetOnlyConfig(base)) is None


def test_behavior_block_survives_get_only_config():
    """端到端：真实形状的配置对象也必须装配出行为规则段（本次事故的回归守卫）。"""

    def blocks(config):
        return [str(m.get("content") or "") for m in PromptAssembler().build(
            PromptRequest(config=config, user_text="hi")
        )]

    by_dict = blocks({"system_prompt": "人格", "prompt_preset": "chat"})
    by_facade = blocks(_GetOnlyConfig({"system_prompt": "人格", "prompt_preset": "chat"}))

    assert by_facade == by_dict, "配置对象形态不该改变装配结果"
    assert any("【安全规则" in text for text in by_facade), "行为规则段必须真的进 messages"


# ==================== 2. 三套互不相同，但底线相同 ====================


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






def test_selecting_preset_injects_behavior_without_qq_agent_profile():
    """选了方案就该有行为段（不依赖任何档位开关）。

    否则用户在本体页面点了"角色扮演"，却因为模块默认档位是 legacy 而拿不到行为段，
    表现为"点了没反应"。
    """
    req = PromptRequest(
        config={"prompt_preset": "assistant", "system_prompt": "人设"},
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
        systems = [m for m in PromptAssembler().build(req) if m.get("role") == "system"]
        assert {"role": "system", "content": "我是独特的人设正文"} in systems
        assert _l1_text(req) != "我是独特的人设正文", "人设不该被并进 L1 规则段"


def test_layers_stay_separate_after_preset_selection():
    """层各自成条（旧的整体折叠风格已删除）：L1 规则一条、L2 人设一条。"""
    req = PromptRequest(
        config={"prompt_preset": "roleplay", "system_prompt": "人设", "outbound_directive_enable": True},
        user_text="hi",
        available_tools=ALL_TOOLS,
    )
    systems = [m["content"] for m in PromptAssembler().build(req) if m["role"] == "system"]
    assert len(systems) >= 2
    assert "【角色一致性：不许跳出】" in systems[0], "L1 规则必须是第一条"
    assert systems[1] == "人设", "L2 人格紧跟其后、独立成条"


def test_every_preset_explains_tool_speech():
    """三套方案都要讲清「文本=思考 / send_message=发言 / 分条自检 / 别汇报」。

    机制默认改为工具发言后，roleplay / assistant 也不能只说"文本会直接发出去"，
    否则模型会按错误事实行动（整轮沉默或把思考当发言）。
    """
    for preset in presets.PRESETS:
        cfg = {"reply_mode": "participation", "system_prompt": "人设"}
        text = persona_rules.render(
            None, ALL_TOOLS, cfg, segments=presets.segments_of(preset)
        )
        assert "只是思考" in text, f"{preset.key} 没说明文本只是思考"
        assert "send_message" in text, f"{preset.key} 没给出真正的发言通道"
        assert "工具调用是动作" in text, f"{preset.key} 缺「工具是动作」口径"
        assert "直接发到" not in text, f"{preset.key} 在工具发言下仍说文本会直接发出"
        assert "不要用文本再重复一遍" in text, f"{preset.key} 缺汇报禁令"


def test_presets_keep_text_mode_when_channel_off():
    """关掉发言通道后，三套方案都要回到「文本即回复」口径（可回滚）。"""
    for preset in presets.PRESETS:
        cfg = {"reply_mode": "legacy"}
        text = persona_rules.render(
            None, ALL_TOOLS, cfg, segments=presets.segments_of(preset)
        )
        assert "直接发到" in text, f"{preset.key} 关掉通道后没回到文本发言"
        assert "只是思考" not in text, f"{preset.key} 关掉通道后仍说文本只是思考"


def test_presets_carry_only_system_prompt_text():
    """三套方案只负责「系统提示词正文」：能力文案（定时/主动性/格式/表情）已归插件。

    这条是架构契约：`PromptPreset` 不再有 schedule/meta/emoji/proactive 字段；
    渲染结果末尾统一带「底线再声明」。
    """
    from app.agent.assembly import presets as prompt_presets

    for preset in prompt_presets.PRESETS:
        assert not hasattr(preset, "schedule_instruction"), preset.key
        assert not hasattr(preset, "proactive_lines"), preset.key
        assert not hasattr(preset, "message_meta_single_line"), preset.key
        assert not hasattr(preset, "emoji_hint"), preset.key
        rendered = prompt_presets.render(
            preset, runtime=None, available_tools=None, config={}, modalities=[]
        )
        assert rendered.strip(), preset.key
        assert "【底线再声明" in rendered, f"{preset.key} 缺少底线再声明"
