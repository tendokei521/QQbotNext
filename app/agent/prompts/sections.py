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
| L3 钩子注入 | emoji_hint(62) / skills(70) / memory(80) / background(90，只剩指代) / group_log(100，恒空，兼容段名) / extra(105) / identity_context(106) | system | 各自有内容才出现（identity_context 恒有） |
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

from .system import emoji_hint_line
from .system import ACCOUNT_LINE, GROUP_LINE_TEMPLATE, MANAGER_LINE, OWNER_LINE
from .system import ONEBOT_AUTH_TEXT
from .system import DEFAULT_PERSONA_PROMPT
from .system import SPEAKER_AUTHORITY_LINE

# ==================== 各块实现（只读 req，无副作用） ====================


# ---- L1 10 persona · 人格预设解析 ----
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


# ---- L1 10 persona ----
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
    system_prompt = req.cfg("system_prompt", DEFAULT_PERSONA_PROMPT)
    return [{"role": "system", "content": system_prompt}]


# ---- L1 20 behavior · 方案解析 ----
def prompt_preset(req: Any):
    """本轮选中的「提示词方案」（见 ``prompts/presets.py``）。

    产品口径是二选一：配置带该键时恒返回两套之一（空/未知回落 chat）；
    只有根本不带该键的库层调用才返回 ``None``（保留旧版装配结果）。
    """
    from . import presets

    return presets.resolve(req.config)


# ---- L1 20 behavior ----
def build_behavior(req: Any) -> list[dict]:
    """行为规则块：拟人化的主来源（独立成段，不塞进人格正文）。

    三条纪律：

    1. **选中提示词方案就注入**——方案本身就是一套行为文案（差异主要在这里）。
       方案的开关就是它自己：清空 ``prompt_preset`` 即回退（库层直调才可能没有方案）。
    2. **段落里点名的工具按本轮能力裁剪**（见 ``persona_rules.detect_caps``）；
    3. **``behavior_rules_override`` 可整体替换**，但安全规则与工作方式强制追加——
       一次误配置不该能把"不越权 / 不泄露 / 怎么发言"整套删掉。
    """
    from . import presets as prompt_presets
    from app.agent.persona import rules as persona_rules

    preset = prompt_preset(req)
    if preset is None:
        # 库层直调（配置里根本没这个键）：仍按聊天段落表渲染，行为段不能消失
        text = persona_rules.render(
            req.runtime,
            req.available_tools,
            req.config,
            req.modalities,
            segments=prompt_presets.CHAT_SEGMENTS,
        )
        return [{"role": "system", "content": text}] if str(text).strip() else []
    # 设置页的「系统提示词覆盖」已暂时停用（2026-11-03）：两套文案以仓内代码为准，
    # 不再读 prompt_preset_service 的全局 kv 覆盖（service 与 API 保留，便于以后恢复）。
    text = prompt_presets.render(
        preset,
        runtime=req.runtime,
        available_tools=req.available_tools,
        config=req.config,
        modalities=req.modalities,
    )
    return [{"role": "system", "content": text}] if str(text).strip() else []


# ---- L1 62 emoji_hint ----
def build_emoji_hint(req: Any) -> list[dict]:
    """【可用表情】提示段（L3，order 62）：本轮真能贴表情时才出现。

    2026-11-03 随 OneBot 工具并入框架：以前由「工具扩展」模块注册，
    现在工具是框架内置能力，所以提示段也回到默认段表（工具不在时整段消失）。
    顺序放在 message_meta 之后：能力清单是补充信息，排在规则/协议之后。
    """
    tools = getattr(req, "available_tools", None)
    if tools is not None and "set_msg_emoji_like" not in tools:
        return []
    try:
        from app.agent.persona.emoji import available_tags

        tags = available_tags(20)
    except Exception as e:  # noqa: BLE001 —— 词典不可用不该影响整轮组装
        logger.add_info(account_label(getattr(getattr(req, "runtime", None), "bot_id", "?"))).warning(
            f"[Assembly] 读取表情词典失败（已跳过表情提示）: {e}"
        )
        return []
    text = emoji_hint_line(tags)
    return [{"role": "system", "content": text}] if text else []


# ---- L1 54 reply_rules ----
def build_participation(req: Any) -> list[dict]:
    """回复触发规则段（随回复设置变化，order 54）。"""
    from .participation import reply_rules_section

    return reply_rules_section(req)


# ---- L1 55 onebot_auth ----
def build_onebot_auth(req: Any) -> list[dict]:
    """OneBot 敏感工具的「消息凭证」约束段（L1，order 55）。

    只有本轮工具列表里真的存在 high/critical 的 OneBot 工具时才出现——
    模型没有这类工具时，谈凭证只会增加噪音。
    """
    tools = getattr(req, "available_tools", None)
    if not tools:
        return []
    try:
        from app.agent.tools.onebot.manifest import ONEBOT_TOOLS

        privileged = {
            str(tool.get("name", ""))
            for tool in ONEBOT_TOOLS
            if str(tool.get("sensitivity", "normal")) in ("high", "critical")
        }
    except Exception as e:  # noqa: BLE001 —— 清单不可用不该拖垮组装
        logger.debug(f"[Assembly] 读取 OneBot 清单失败（跳过凭证说明）: {e}")
        return []
    if not (set(tools) & privileged):
        return []
    return [{"role": "system", "content": ONEBOT_AUTH_TEXT}]


# ---- L1 60 message_meta ----
def build_message_meta(req: Any) -> list[dict]:
    """消息格式说明段（框架系统方案，order 60）。"""
    from .convention import convention_section

    return convention_section(req)


# ---- L1 70 skills ----
def build_skills(req: Any) -> list[dict]:
    """技能块（可能多块，各自一条 system）。"""
    return [{"role": "system", "content": block} for block in (req.skill_blocks or []) if block]


# ---- L1 80 memory ----
def build_memory(req: Any) -> list[dict]:
    """长期记忆召回块。"""
    return [{"role": "system", "content": req.memory_text}] if req.memory_text else []


# ---- L1 90 background ----
def build_background(req: Any) -> list[dict]:
    """背景块：只剩指代消解行。

    群聊旧史（``pre_history_text``）已迁到 user 侧数据块（见 assembler
    ``_fold_pulled_history``），不再占 system 指令位。
    """
    if req.referent_text and req.referent_text.strip():
        return [{"role": "system", "content": req.referent_text}]
    return []


# ---- L1 100 group_log ----
def build_group_log(req: Any) -> list[dict]:
    """群聊环境记录（已迁 user 侧数据块；此段保留名字以兼容注册表顺序）。"""
    return []


# ---- L1 105 extra ----
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


# ---- L1 106 identity_context ----
def build_identity_context(req: Any) -> list[dict]:
    """账号身份块：告诉模型"我是谁、谁是我的拥有者/管理员"。

    位置固定在**所有 system 块的最后**（L3 order=106，紧跟 ``extra``、在历史之前）：
    这是每轮都成立的稳定事实，靠后放既不打断"规则 → 人设 → 环境"的连续性，
    又离模型开口最近。没有配置的字段写「未设置」，不整块消失（否则模型会误以为
    "我没有拥有者"）。
    """
    runtime = getattr(req, "runtime", None)
    bot = getattr(runtime, "bot", None) if runtime is not None else None
    account_id = str(getattr(runtime, "bot_id", "") or "") if runtime is not None else ""

    # 自己的 QQ 号：优先取运行时登录快照，其次查账号快照表，最后退回账号 id
    account = str(getattr(bot, "bot_id", "") or "") if bot is not None else ""
    if not account and account_id:
        service = getattr(runtime, "config_service", None) if runtime is not None else None
        getter = getattr(service, "get_bot_account", None)
        if callable(getter):
            try:
                account = str((getter(account_id) or {}).get("user_id") or "")
            except Exception:  # noqa: BLE001 —— 身份块读失败不该影响本轮
                account = ""
    if not account:
        account = account_id or "未知"

    def _roster(key: str) -> str:
        """名册（string_list）→ 顿号分隔；空/缺失 → 「未设置」。"""
        getter = getattr(req, "cfg", None)
        values = getter(key, []) if callable(getter) else []
        if isinstance(values, str):
            values = values.replace("，", ",").replace(",", "\n").splitlines()
        items = [str(item).strip() for item in (values or []) if str(item).strip()]
        return "、".join(items) if items else "未设置"

    group_id = str(getattr(req, "group_id", "") or "")
    event = getattr(req, "event", None)
    group = getattr(event, "group", None)
    if not group_id and group is not None:
        group_id = str(getattr(group, "group_id", "") or "")
    group_name = str(getattr(group, "group_name", "") or "") if group is not None else ""

    first_lines: list[str] = []
    if group_id:
        # 群信息只在这里出现一次（移动自每条 user 消息的头部，2026-11-03）：放在「你的账号」前
        from app.agent.message_assembly.spec import quote

        inner = f"group_id={group_id}"
        if group_name:
            inner += f",group_name={quote(group_name)}"
        first_lines.append(GROUP_LINE_TEMPLATE.format(inner))

    content_lines = [
        *first_lines,
        ACCOUNT_LINE.format(account),
        OWNER_LINE.format(_roster('agent_owner_list')),
        MANAGER_LINE.format(_roster('agent_manager_list')),
    ]
    # 发言人身份（仅本轮）：拥有者/管理者发言时配合度更高；放在身份块最后一行，
    # 让模型先知道“我是谁、谁有权管理我”，再知道“这条消息是谁说的”。
    role = str(getattr(event, "permission_role", "") or "")
    if not role:
        if getattr(event, "is_bot_owner", False) or getattr(event, "is_agent_owner", False):
            role = "owner"
        elif getattr(event, "is_agent_manager", False):
            role = "manager"
    if role in ("owner", "manager"):
        content_lines.append(
            SPEAKER_AUTHORITY_LINE.format(role="拥有者" if role == "owner" else "管理者")
        )
    content = "\n".join(content_lines)
    return [{"role": "system", "content": content}]


# ---- L4 history · 角色预设的开场对话 ----
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


# ---- L4 history ----
def build_history(req: Any) -> list[dict]:
    """会话历史（调用方已渲染打标并压缩）；角色预设的预设对话前插到最前。"""
    return list(persona_begin_dialogs(req)) + list(req.history or [])


# ---- L5 user · extra 内容块 ----
def _extra_text_parts(req: Any) -> list[dict]:
    """把 ``extra_user_content_parts`` 转成多模态 text 块；空白项丢弃。"""
    return [
        {"type": "text", "text": str(part)}
        for part in (req.extra_user_content_parts or [])
        if str(part or "").strip()
    ]


# ---- L5 user · user 数据块 ----
def _user_block_parts(req: Any) -> list[dict]:
    """模块贡献的 user 侧数据块（``req.user_blocks``）→ 多模态 text 块。"""
    return [
        {"type": "text", "text": str(text).strip()}
        for _name, text in (getattr(req, "user_blocks", None) or [])
        if str(text or "").strip()
    ]


# ---- L5 user · 发送者 name 字段 ----
def _current_sender_name(req: Any) -> str:
    """本轮发送者昵称（供 OpenAI message 的 name 字段；不进正文）。"""
    event = getattr(req, "event", None)
    if event is None:
        return ""
    if bool(getattr(req, "is_private", False)):
        return "对方"
    user = getattr(event, "user", None)
    if user is None:
        return ""
    return str(getattr(user, "card", "") or getattr(user, "nickname", "") or "")


# ---- L5 user ----
def build_user(req: Any) -> list[dict]:
    """本轮消息。图片块由 ``place_images`` 在清洗前归位。

    没有 user 数据块 / ``extra_user_content_parts`` 时保持**纯字符串** content
    （与旧行为逐字一致）；有值时改多块数组：

    - 顺序：**模块 user 数据块 → 正文 → 额外内容块 → 图片**。数据块放在正文之前，
      模型先读到"历史资料"再读本轮问题，同时它属于 user 轮次而不是 system 指令位。
    - 空正文只留数据块 / 额外块。
    - 发送者昵称放在消息的 ``name`` 字段（不混进正文，避免昵称句被当内容）。
    """
    blocks = _user_block_parts(req)
    parts = _extra_text_parts(req)
    sender_name = _current_sender_name(req)
    if not blocks and not parts:
        message: dict = {"role": "user", "content": req.user_text}
    else:
        content: list[dict] = list(blocks)
        if str(req.user_text or "").strip():
            content.append({"type": "text", "text": req.user_text})
        content.extend(parts)
        message = {"role": "user", "content": content}
    if sender_name:
        message["name"] = sender_name
    return [message]


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
        # 能力段（schedule / proactive / message_meta）由对应插件注册；
        # 框架只保留人设 + 行为规则 + 材料通道 + 历史 + 本轮消息。
        Section("reply_rules", 54, build_participation, source="builtin"),
        # 需要权限的 OneBot 工具：消息凭证约束（order 55，紧跟在回复规则之后）
        Section("onebot_auth", 55, build_onebot_auth, source="builtin"),
        Section("message_meta", 60, build_message_meta, source="builtin"),
        # 能力清单排在规则/协议之后：先要求、后补充信息
        Section("emoji_hint", 62, build_emoji_hint, source="builtin"),
        Section("skills", 70, build_skills, source="builtin"),
        Section("memory", 80, build_memory, source="builtin"),
        Section("background", 90, build_background, source="builtin"),
        Section("group_log", 100, build_group_log, source="builtin"),
        # 账号身份（账号/拥有者/管理员）固定在所有 system 块的最后：order=106 紧跟
        # extra(105)、排在 history(110) 之前。
        Section("identity_context", 106, build_identity_context, source="builtin"),
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
    "build_emoji_hint",
    "build_extra_blocks",
    "build_group_log",
    "build_history",
    "build_identity_context",
    "build_memory",
    "build_onebot_auth",
    "build_persona",
    "build_skills",
    "build_user",
    "default_sections",
    "persona_begin_dialogs",
    "prompt_preset",
    "resolve_persona_preset",
]
