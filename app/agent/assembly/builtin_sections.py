"""内置段的定义（哪些段、每段的层与条件、每段文案）。

## 为什么单独成文件

``assembler.py`` 负责「怎么装配」（解析注册表、按层排序、层内合并、折叠、清洗、图片归位），
本文件负责「默认有哪些段、每段属于哪一层、适用条件与文案」：

- 默认段顺序/条件只有这一处，``default_sections()`` 是「默认段始终存在」的唯一来源；
- 插件 `register_section` 只加一段（默认落 L3，想进 L1 就显式写 ``layer="system_prompt"``）；
- 每个 builder 只读 ``req``、不适用时返回 ``[]``，输出由 ``tests/agent/assembly`` 兜底。

## 段 → 层（层序见 ``assembly/layers.py``）

| 层 | 段（层内 order 升序） | 角色 | 条件 |
|---|---|---|---|
| L1 系统提示词 | behavior(20) / schedule(40) / proactive(50) / message_meta(60) | system | 方案恒有；其余看开关与能力 |
| L2 人格预设 | persona(10) | system | 始终 |
| L3 钩子注入 | emoji_hint(30) / skills(70) / memory(80) / background(90) / group_log(100) / extra(105) | system | 各自有内容才出现 |
| L4 对话历史 | history(110) | 对话 | 非空（角色预设的预设对话前插） |
| L5 本轮消息 | user(120) | user | 始终 |

L1 的四段在装配时会**合并成一条 system**（``assembler.merge_layer_blocks``），
所以最终 messages 里 L1 只占一条；``order`` 仍决定它们在合并文本里的先后。

``EXTRA_SECTION``（order=105）**不是**默认段：它承载模块贡献的
``PromptRequest.extra_blocks``，由装配器在渲染时插回 ``group_log`` 与 ``history``
之间，保持改造前的位置与 ``describe()`` 的那一行「extra」。
"""

from __future__ import annotations

from typing import Any

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
    """本轮选中的「提示词方案」（见 ``assembly.presets``）。

    产品口径是三选一：配置带该键时恒返回三套之一（空/未知回落 chat）；
    只有根本不带该键的库层调用才返回 ``None``（保留旧版装配结果）。
    """
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


def build_behavior(req: Any) -> list[dict]:
    """行为规则块：拟人化的主来源（独立成段，不塞进人格正文）。

    三条纪律：

    1. **选中提示词方案就注入**——方案本身就是一套行为文案（差异主要在这里）。
       方案的开关就是它自己：清空 ``prompt_preset`` 即回退（库层直调才可能没有方案）。
    2. **段落里点名的工具按本轮能力裁剪**（见 ``persona_rules.detect_caps``）；
    3. **``behavior_rules_override`` 可整体替换**，但安全规则与工作方式强制追加——
       一次误配置不该能把"不越权 / 不泄露 / 怎么发言"整套删掉。
    """
    from app.agent.assembly import presets as prompt_presets
    from app.agent.persona import rules as persona_rules

    preset = prompt_preset(req)
    if preset is None:
        text = persona_rules.render(req.runtime, req.available_tools, req.config, req.modalities)
        return [{"role": "system", "content": text}] if str(text).strip() else []
    # 设置页里编辑过的「系统提示词」覆盖（全局、按方案）：静态文本，优先于默认渲染
    from app.application import prompt_preset_service

    preset_override = prompt_preset_service.get_override(preset.key)
    if preset_override:
        return [{"role": "system", "content": preset_override}]
    text = prompt_presets.render(
        preset,
        runtime=req.runtime,
        available_tools=req.available_tools,
        config=req.config,
        modalities=req.modalities,
    )
    return [{"role": "system", "content": text}] if str(text).strip() else []


def build_participation(req: Any) -> list[dict]:
    """回复触发规则段（随回复设置变化，order 54）。"""
    from app.agent.participation import reply_rules_section

    return reply_rules_section(req)


def build_message_meta(req: Any) -> list[dict]:
    """消息格式说明段（框架系统方案，order 60）。"""
    from app.agent.message_assembly.convention import convention_section

    return convention_section(req)


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


def _user_block_parts(req: Any) -> list[dict]:
    """模块贡献的 user 侧数据块（``req.user_blocks``）→ 多模态 text 块。"""
    return [
        {"type": "text", "text": str(text).strip()}
        for _name, text in (getattr(req, "user_blocks", None) or [])
        if str(text or "").strip()
    ]


def build_user(req: Any) -> list[dict]:
    """本轮消息。图片块由 ``place_images`` 在清洗前归位。

    没有 user 数据块 / ``extra_user_content_parts`` 时保持**纯字符串** content
    （与旧行为逐字一致）；有值时改多块数组：

    - 顺序：**模块 user 数据块 → 正文 → 额外内容块 → 图片**。数据块放在正文之前，
      模型先读到"历史资料"再读本轮问题，同时它属于 user 轮次而不是 system 指令位。
    - 空正文只留数据块 / 额外块。
    """
    blocks = _user_block_parts(req)
    parts = _extra_text_parts(req)
    if not blocks and not parts:
        return [{"role": "user", "content": req.user_text}]
    content: list[dict] = list(blocks)
    if str(req.user_text or "").strip():
        content.append({"type": "text", "text": req.user_text})
    content.extend(parts)
    return [{"role": "user", "content": content}]


# ==================== 默认段 / 兼容的 extra 通道 ====================


def default_sections() -> list[Section]:
    """默认段（人设/行为/回复规则/消息格式/能力/记忆/背景/环境/历史/本轮）。

    注：**发言协议不是段**——它必须贴在最后一条 user 消息末尾才生效
    （`assembler.apply_speech_protocol`，含对照实验结论）。
    """
    return [
        Section("persona", 10, build_persona, source="builtin"),
        # 行为规则紧跟人格：人格讲"我是谁"，行为段讲"在这个群里怎么说话"，
        # 越硬的约束越靠前（对齐 QQ-agent 的段落顺序，见 persona_rules 模块 docstring）
        Section("behavior", 20, build_behavior, source="builtin"),
        # 能力段（emoji_hint / schedule / proactive / message_meta）由对应插件注册；
        # 框架只保留人设 + 行为规则 + 材料通道 + 历史 + 本轮消息。
        Section("reply_rules", 54, build_participation, source="builtin"),
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
    "build_background",
    "build_behavior",
    "build_extra_blocks",
    "build_group_log",
    "build_history",
    "build_memory",
    "build_persona",
    "build_skills",
    "build_user",
    "default_sections",
    "persona_begin_dialogs",
    "prompt_preset",
    "resolve_persona_preset",
]
