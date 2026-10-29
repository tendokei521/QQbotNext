"""内置的 12 段提示词（从 ``assembler.py`` 的硬编码块表迁出）。

## 为什么单独成文件

``assembler.py`` 负责「怎么装配」（解析注册表、折叠、清洗、图片归位），
本文件负责「默认有哪些段、每段的适用条件与文案」。拆开后：

- 默认段顺序/条件只有这一处，``default_sections()`` 是「默认段总是存在」的唯一来源；
- 插件 `register_section` 只加一段，不必读/改装配器；
- 迁移是**逐条搬运**：每个 builder 与改造前的 ``build_xxx(req)`` 一字不差
  （条件写进 builder 内部，不适用时返回 ``[]``），输出由 ``tests/agent/assembly``
  的既有黄金快照兜底。

## 顺序与条件（与改造前块表一致）

| order | 段 | 角色 | 条件 |
|---|---|---|---|
| 10 | persona | system | 始终 |
| 20 | behavior | system | 选中提示词方案，或 ``assembly_profile=qq_agent`` 且未关 |
| 30 | emoji_hint | system | 能贴表情且活跃度 > 0、词典有标签 |
| 40 | schedule | system | ``schedule_enable`` |
| 50 | proactive | system | 有可用能力时 |
| 60 | message_meta | system | 消歧说明开启且本轮确实注入了元信息 |
| 70 | skills | system ×N | 有技能时 |
| 80 | memory | system | 记忆有召回 |
| 90 | background | system | 指代块或聊天环境背景非空 |
| 100 | group_log | system | 群聊记录模块在场且有记录 |
| 110 | history | 对话 | 非空（角色预设的预设对话前插） |
| 120 | user | user | 始终 |

``EXTRA_SECTION``（order=105）**不是**默认段：它承载模块贡献的
``PromptRequest.extra_blocks``，由装配器在渲染时插回 ``group_log`` 与 ``history``
之间，保持改造前的位置与 ``describe()`` 的那一行「extra」。
"""

from __future__ import annotations

from typing import Any

from app.agent.assembly.prompt import (
    LEGACY_MESSAGE_META_INSTRUCTION,
    MESSAGE_META_INSTRUCTION,
    PROFILE_QQ_AGENT,
    SCHEDULE_INSTRUCTION,
    SINGLE_LINE_MESSAGE_META_INSTRUCTION,
    build_proactive_instruction,
    emoji_hint_line,
    user_message_format,
)
from app.agent.assembly.sections import Section
from app.core.logger import account_label, logger

# ==================== 各块实现（只读 req，无副作用） ====================


def resolve_persona_preset(req: Any) -> dict | None:
    """按 ``persona_preset_id`` 解析角色预设（同步读配置中心，不阻塞）。

    只认 ``req.cfg("persona_preset_id")`` 指向的预设；预设不存在（悬挂引用）或读不到时
    返回 ``None`` 并留痕，由调用方回退到 ``system_prompt``。
    """
    preset_id = str(req.cfg("persona_preset_id", "") or "").strip()
    if not preset_id:
        return None
    service = getattr(req.runtime, "config_service", None)
    getter = getattr(service, "get_role_preset", None)
    if not callable(getter):
        return None
    try:
        preset = getter(preset_id)
    except Exception as e:  # noqa: BLE001 —— 预设读取失败不该让整轮请求炸掉
        logger.add_info(account_label(getattr(req.runtime, 'bot_id', '?'))).warning(
            f"[Assembly] 读取角色预设失败（已忽略）: {preset_id}: {e}"
        )
        return None
    if not isinstance(preset, dict):
        logger.add_info(account_label(getattr(req.runtime, 'bot_id', '?'))).warning(
            f"[Assembly] 角色预设不存在（悬挂引用）: {preset_id}，已回退 system_prompt"
        )
        return None
    return preset


def build_persona(req: Any) -> list[dict]:
    """基础人设：优先 ``persona_preset_id``（活引用），否则用 ``system_prompt``。"""
    preset = resolve_persona_preset(req)
    if preset is not None:
        text = str(preset.get("system_prompt") or "").strip()
        if text:
            return [{"role": "system", "content": text}]
        logger.add_info(account_label(getattr(req.runtime, 'bot_id', '?'))).warning(
            "[Assembly] 角色预设没有 system_prompt，已回退 system_prompt"
        )
    system_prompt = req.cfg("system_prompt", "你是一个友好的助手。")
    return [{"role": "system", "content": system_prompt}]


def prompt_preset(req: Any):
    """本轮选中的「提示词方案」（见 ``assembly.presets``）；未选/未知值返回 ``None``。"""
    from app.agent.assembly import presets

    return presets.resolve(req.config)


def persona_begin_dialogs(req: Any) -> list[dict]:
    """角色预设的预设对话（前插到历史最前，对齐 AstrBot 的 ``begin_dialogs``）。

    只保留 ``{role: user|assistant, content: 非空}``，脏条目丢弃而不是抛错。
    """
    preset = resolve_persona_preset(req)
    if preset is None:
        return []
    rows = preset.get("begin_dialogs")
    if not isinstance(rows, list):
        return []
    result: list[dict] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").lower()
        content = str(item.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            result.append({"role": role, "content": content})
    return result


def build_schedule_instruction(req: Any) -> list[dict]:
    """定时任务协议（主动消息/定时任务自身不需要再建任务，故关闭）。"""
    if not req.schedule_enable:
        return []
    preset = prompt_preset(req)
    text = preset.schedule_instruction if preset is not None else SCHEDULE_INSTRUCTION
    return [{"role": "system", "content": text}] if text else []


def assembly_profile(req: Any) -> str:
    """组装档位：``legacy``（默认，旧行为逐字不变）或 ``qq_agent``（对齐外部实现）。

    用**一个总闸**而不是散落的开关，是为了能一键回退与 A/B：某次改动效果不好时，
    把档位切回 ``legacy`` 就回到改造前，不必逐个关块。
    """
    return str(req.cfg("assembly_profile", "legacy") or "legacy").strip().lower()


def build_behavior(req: Any) -> list[dict]:
    """行为规则块：拟人化的主来源（独立成段，不塞进人格正文）。

    四条纪律：

    1. **选中提示词方案时无条件注入**——方案本身就是一套行为文案（差异主要在这里），
       不再看 ``assembly_profile``；否则用户选了方案却因模块默认档位而拿不到行为段，
       等于点了没反应。方案的开关就是它自己：清空 ``prompt_preset`` 即回退。
    2. **未选方案时只在 ``qq_agent`` 档注入**——``legacy`` 下整块消失，改造前逐字不变；
    3. **段落里点名的工具按本轮能力裁剪**（见 ``persona_rules.detect_caps``）；
    4. **``behavior_rules_override`` 可整体替换**，但安全规则与工作方式强制追加——
       一次误配置不该能把"不越权 / 不泄露 / 怎么发言"整套删掉。
    """
    from app.agent.assembly import presets as prompt_presets
    from app.agent.persona import rules as persona_rules

    preset = prompt_preset(req)
    if preset is None and (
        assembly_profile(req) != "qq_agent" or not bool(req.cfg("behavior_rules_enable", True))
    ):
        return []
    override = str(req.cfg("behavior_rules_override", "") or "").strip()
    if preset is not None and not override:
        # 设置页里编辑过的「系统提示词」覆盖（全局、按方案）：静态文本，优先于默认渲染
        from app.application import prompt_preset_service

        preset_override = prompt_preset_service.get_override(preset.key)
        if preset_override:
            return [{"role": "system", "content": preset_override}]
    if preset is not None:
        text = prompt_presets.render(
            preset,
            runtime=req.runtime,
            available_tools=req.available_tools,
            config=req.config,
            modalities=req.modalities,
            override=override,
        )
    elif override:
        caps = persona_rules.detect_caps(req.available_tools, req.config, req.modalities)
        text = persona_rules.override_with_safety(override, caps)
    else:
        text = persona_rules.render(req.runtime, req.available_tools, req.config, req.modalities)
    return [{"role": "system", "content": text}] if str(text).strip() else []


def build_emoji_hint(req: Any) -> list[dict]:
    """【可用表情】提示行：把可贴标签摆进 prompt，省得模型去翻工具文档。

    三条注入条件（任一不满足就整块不出现）：本轮真的能贴表情、活跃度 > 0、
    词典里确实有标签。标签表是静态的，所以这段在会话内逐字稳定，不破坏前缀缓存。
    选中提示词方案时不再要求 ``qq_agent`` 档（方案自带这套文案的口径）。
    """
    preset = prompt_preset(req)
    if preset is None and assembly_profile(req) != PROFILE_QQ_AGENT:
        return []
    tools = req.available_tools
    if tools is not None and "set_msg_emoji_like" not in tools:
        return []
    try:
        level = int(req.cfg("emoji_react_level", 1) or 0)
    except (TypeError, ValueError):
        level = 1
    if level <= 0:
        return []
    try:
        from app.agent.persona.emoji import available_tags

        tags = available_tags(20)
    except Exception as e:  # noqa: BLE001 —— 词典不可用不该影响整轮组装
        logger.add_info(account_label(getattr(req.runtime, 'bot_id', '?'))).warning(
            f"[Assembly] 读取表情词典失败（已跳过表情提示）: {e}"
        )
        return []
    render_hint = preset.emoji_hint if preset is not None else emoji_hint_line
    text = render_hint(tags)
    return [{"role": "system", "content": text}] if text else []


def build_proactive(req: Any) -> list[dict]:
    """「主动性」协议块：按本轮实际可用工具裁剪，没有可用能力时整块不注入。

    意图判定用**原始正文**（``req.intent_text``），避免元信息前缀干扰。
    ``qq_agent`` 档或选中提示词方案时去重：通用用法归行为段，这里只留本轮的意图补强
    与标记语义。方案的措辞只覆盖 ``PROACTIVE_LINES`` 里若干行，能力裁剪不变。
    """
    preset = prompt_preset(req)
    text = build_proactive_instruction(
        req.config,
        req.intent_text,
        available_tools=req.available_tools,
        dedupe=preset is not None or assembly_profile(req) == "qq_agent",
        lines=preset.proactive_lines if preset is not None else None,
    )
    return [{"role": "system", "content": text}] if text else []


def message_meta_instruction(req: Any) -> str | None:
    """消息格式说明：让模型知道"发送者：…"是元信息、只有正文才是用户说的话。

    模式（``meta_instruction_mode`` 优先，兼容 ``meta_sender_style``）：
    ``off`` 不注入；``new`` 用单行脱敏版；其余用默认真人感版。
    此外，仅当本轮**真的注入了元信息**（``ctx.state["message_meta_injected"]``）时才注入说明——
    否则说明本身就是噪音，会诱导模型去找并不存在的"发送者："前缀。

    ``qq_agent`` 档 + 单行格式时改用**单行格式图例**：老说明讲的是"发送者：/发送了："
    那一套，格式已经变了却还在解释旧格式，等于给模型一本错的手册。
    选中提示词方案时用**方案自己的两份说明**（同样是"单行讲单行、多行讲多行"）。
    """
    ctx = req.ctx
    if ctx is not None and not ctx.state.get("message_meta_injected"):
        return None
    mode = req.cfg("meta_instruction_mode")
    if mode is None:
        mode = req.cfg("meta_sender_style", "legacy")
    mode = str(mode or "legacy").lower()
    if mode == "off":
        return None
    preset = prompt_preset(req)
    if preset is not None:
        # 方案自己保证"单行讲单行、多行讲多行"：直接读配置原值，不经过 qq_agent 档门控
        fmt = str(req.cfg("user_message_format", "single_line") or "").strip().lower()
        return preset.message_meta_single_line if fmt != "legacy" else preset.message_meta_legacy
    if assembly_profile(req) == PROFILE_QQ_AGENT and user_message_format(req.config) == "single_line":
        return SINGLE_LINE_MESSAGE_META_INSTRUCTION
    if mode == "new":
        return MESSAGE_META_INSTRUCTION
    if bool(req.cfg("experimental_long_term_memory", False)):
        return MESSAGE_META_INSTRUCTION
    return LEGACY_MESSAGE_META_INSTRUCTION


def build_message_meta(req: Any) -> list[dict]:
    text = message_meta_instruction(req)
    return [{"role": "system", "content": text}] if text else []


def build_skills(req: Any) -> list[dict]:
    """技能块（可能多块，各自一条 system）。"""
    return [{"role": "system", "content": block} for block in (req.skill_blocks or []) if block]


def build_memory(req: Any) -> list[dict]:
    """长期记忆召回块。"""
    return [{"role": "system", "content": req.memory_text}] if req.memory_text else []


def build_background(req: Any) -> list[dict]:
    """背景块：指代消解行 + 聊天环境背景合并为一条 system（指代在前，信息密度更高）。

    可用 ``history_background_enable`` 关掉"聊天环境背景"部分（指代行保留）——
    目标是把会话历史作为唯一历史来源时，避免同一批消息在两处出现。
    """
    parts: list[str] = []
    if req.referent_text and req.referent_text.strip():
        parts.append(req.referent_text)
    if req.cfg("history_background_enable", True) and req.pre_history_text and req.pre_history_text.strip():
        parts.append(req.pre_history_text)
    if not parts:
        return []
    return [{"role": "system", "content": "\n\n".join(parts)}]


def build_history(req: Any) -> list[dict]:
    """会话历史（调用方已渲染打标并压缩）；角色预设的预设对话前插到最前。"""
    return list(persona_begin_dialogs(req)) + list(req.history or [])


def build_group_log(req: Any) -> list[dict]:
    """群聊环境记录块（消息 + 互动 + 我的动作）。

    独立成块（而不是并进 ``background``）是为了让"这次请求为什么带了/没带群聊记录"
    能从块表一眼看出：它是**另一个来源**，有自己的开关、窗口与预算。
    """
    text = str(req.group_log_text or "").strip()
    return [{"role": "system", "content": text}] if text else []


def build_extra_blocks(req: Any) -> list[dict]:
    """模块贡献的额外 system 块（按收集顺序，空块已在收集侧丢弃）。

    位置固定在 ``group_log`` 之后、``history`` 之前：既在"环境信息"之后，
    又贴着历史之前 —— 模型读到的顺序是"我是谁 → 现在什么情况 → 之前聊了什么 → 本轮"。
    """
    return [
        {"role": "system", "content": str(text).strip()}
        for _name, text in (req.extra_blocks or [])
        if str(text or "").strip()
    ]


def _extra_text_parts(req: Any) -> list[dict]:
    """把 ``extra_user_content_parts`` 转成多模态 text 块；空白项丢弃。"""
    return [
        {"type": "text", "text": str(part)}
        for part in (req.extra_user_content_parts or [])
        if str(part or "").strip()
    ]


def build_user(req: Any) -> list[dict]:
    """本轮消息。图片块由 ``place_images`` 在清洗前归位。

    没有 ``extra_user_content_parts`` 时保持**纯字符串** content（与旧行为逐字一致）；
    有值时改多块数组（正文 → 额外块），空正文只留额外块。
    """
    parts = _extra_text_parts(req)
    if not parts:
        return [{"role": "user", "content": req.user_text}]
    content: list[dict] = []
    if str(req.user_text or "").strip():
        content.append({"type": "text", "text": req.user_text})
    content.extend(parts)
    return [{"role": "user", "content": content}]


# ==================== 默认段 / 兼容的 extra 通道 ====================


def default_sections() -> list[Section]:
    """默认 12 段（order 升序；每次调用返回新列表，Section 本身不可变）。"""
    return [
        Section("persona", 10, build_persona, source="builtin"),
        # 行为规则紧跟人格：人格讲"我是谁"，行为段讲"在这个群里怎么说话"，
        # 越硬的约束越靠前（对齐 QQ-agent 的段落顺序，见 persona_rules 模块 docstring）
        Section("behavior", 20, build_behavior, source="builtin"),
        # 表情标签提示紧随行为段（行为段的【表情与拍一拍】指着它）
        Section("emoji_hint", 30, build_emoji_hint, source="builtin"),
        Section("schedule", 40, build_schedule_instruction, source="builtin"),
        Section("proactive", 50, build_proactive, source="builtin"),
        Section("message_meta", 60, build_message_meta, source="builtin"),
        Section("skills", 70, build_skills, source="builtin"),
        Section("memory", 80, build_memory, source="builtin"),
        Section("background", 90, build_background, source="builtin"),
        Section("group_log", 100, build_group_log, source="builtin"),
        Section("history", 110, build_history, source="builtin"),
        Section("user", 120, build_user, source="builtin"),
    ]


#: ``extra_blocks`` 兼容通道的 order：改造前该块位于 ``group_log`` 与 ``history`` 之间。
EXTRA_ORDER = 105

#: 兼容段：模块贡献的 ``req.extra_blocks``。不是默认段（不进 ``default_sections()``），
#: 但装配器每次渲染都会把它按 ``EXTRA_ORDER`` 插回原位置——行为与改造前逐字节一致。
EXTRA_SECTION = Section(
    "extra",
    EXTRA_ORDER,
    build_extra_blocks,
    source="builtin:extra_blocks",
)


__all__ = [
    "EXTRA_ORDER",
    "EXTRA_SECTION",
    "assembly_profile",
    "build_background",
    "build_behavior",
    "build_emoji_hint",
    "build_extra_blocks",
    "build_group_log",
    "build_history",
    "build_memory",
    "build_message_meta",
    "build_persona",
    "build_proactive",
    "build_schedule_instruction",
    "build_skills",
    "build_user",
    "default_sections",
    "message_meta_instruction",
    "persona_begin_dialogs",
    "prompt_preset",
    "resolve_persona_preset",
]
