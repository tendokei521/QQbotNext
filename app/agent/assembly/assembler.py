"""LLM 消息组装：声明式块表 + 一次装配。

## 为什么有这个模块

同一类请求（普通回复 / 主动消息 / 定时任务 / 上下文压缩）此前在**四个地方各自组装**
messages：``chat.generate_response``、``chat.stream_response``、``scheduler._build_messages``、
``proactive``（内联）。块顺序、同名块的取值口径、截断与清洗都各写一遍，后果是
「为什么主动消息没有格式说明」「为什么记忆块在背景之前」这类问题无法从一处回答。

现在改为：

```
PromptRequest（一次请求的全部归一化输入）
      ↓
PromptAssembler.build()   ← 固定按 BLOCK_ORDER 生成命名块
      ↓
place_images → sanitize_contexts_by_modalities   ← 归位在前、清洗在最后（清洗是唯一兜底）
      ↓
messages（OpenAI 风格）
```

- **顺序只看 ``BLOCK_ORDER``**，不藏在函数体里；
- **每个块是独立函数** ``build_xxx(req) -> list[dict]``，只读 req、不产生副作用，可单测；
- **`describe(req)`** 返回 ``[{block, role, chars}]``，`[Prompt]` 调试日志与黄金快照测试都用它，
  想回答"这次请求发了什么"不必再打印全文；
- **组装风格可切**：``system_prompt_style="single"`` 时把所有 system 块折成一条
  （``fold_system_blocks``），对齐第三方框架「一整条 system prompt」的形态；默认
  ``blocks`` 保持上表的既有形态不变。

## 块顺序（与重构前的既有顺序保持一致）

| # | 块 | 角色 | 来源 | 条件 |
|---|---|---|---|---|
| 1 | persona | system | ``config.persona_preset_id``（活引用）或 ``config.system_prompt`` | 始终 |
| 2 | behavior | system | ``persona_rules``（行为规则段） | ``assembly_profile=qq_agent`` 且未关 |
| 3 | emoji_hint | system | ``emoji_lexicon.available_tags`` | 能贴表情且活跃度 > 0 |
| 4 | schedule | system | ``prompt.SCHEDULE_INSTRUCTION`` | ``schedule_enable`` |
| 5 | proactive | system | ``prompt.build_proactive_instruction`` | 有可用能力时 |
| 6 | message_meta | system | ``LEGACY_/MESSAGE_META_/SINGLE_LINE_…`` | 消歧说明开启时 |
| 7 | skills | system ×N | ``runtime.skills.prompt_blocks()`` | 有技能时 |
| 8 | memory | system | ``memory.recall_block_async`` | 记忆有召回 |
| 9 | background | system | 指代块 + 聊天环境背景（合并为一块） | 任一非空 |
| 10 | group_log | system | 群聊环境记录（消息 + 互动 + 我的动作） | 群聊记录模块在场且有记录 |
| 11 | history | 对话 | 会话历史（已渲染打标） | 非空 |
| 12 | user | user | 本轮消息（可含额外内容块与图片块） | 始终 |

注意：块 8 在块 9 之前是历史既有行为；块 9 内部"指代在前、环境在后"用 ``\n\n`` 连接。
块 10 与块 9 是两个**不同来源**（持续记录 vs 按需拉取），正文按 message_id 去重。
块 2/3 只在 ``assembly_profile=qq_agent`` 档出现，``legacy`` 档恒为空（见 ``persona_rules``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

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
from app.core.logger import logger


@dataclass
class PromptRequest:
    """一次 LLM 请求的全部输入（已归一化，装配器不再回读运行时状态）。

    字段划分：

    - 会话身份：``session_id``（``group_xxx`` / ``private_xxx``）、``is_private``、``user_id``、``group_id``
    - 文本输入：``user_text``（本轮最终 user 内容）、``memory_source_text``（记忆检索用原始正文）
    - 已备好的块原料：``history``、``pre_history_text``、``referent_text``、``skill_blocks``
    - 本轮能力：``available_tools``（主动性块按此裁剪）、``modalities``（决定是否带图）
    - 多模态：``event``（图片从这里取）、``user_images``（已解析结果，None=尚未解析）
    """

    # 身份与载体
    runtime: Any = None
    event: Any = None
    ctx: Any = None
    config: Any = None
    session_id: str = ""
    is_private: bool = False
    user_id: Any = None
    group_id: Any = None
    kind: str = "chat"          # chat / proactive / schedule / summary

    # 文本
    user_text: str = ""
    #: 用户原始正文（未包裹「发送者/时间」元信息）：记忆检索与意图判定都要用它，
    #: 否则元信息里的 QQ 号会被当成消息 id、昵称会污染检索
    raw_user_text: str = ""
    memory_source_text: str = ""

    # 块原料
    history: list[dict] = field(default_factory=list)
    pre_history_text: str = ""
    referent_text: str = ""
    skill_blocks: list[str] = field(default_factory=list)
    memory_text: str = ""
    #: 群聊环境记录块（群聊记录模块产出，见 ``app.agent.group_log.context``）。
    #: 与 ``pre_history_text``（在线历史拉取）是**两个来源**：前者含互动（表情/戳/撤回）
    #: 与"我做过什么"，后者只有消息正文。两者正文按 message_id 去重，不会重复出现。
    group_log_text: str = ""

    # 能力与模态
    available_tools: set[str] | None = None
    modalities: list[str] | None = None
    schedule_enable: bool = True

    # 多模态
    user_images: list[dict] | None = None
    with_images: bool = True
    #: user 侧「额外内容块」（纯文本）：如 ``<system_reminder>``、``<Quoted Message>``、
    #: ``[Image Attachment: path …]``。对齐 AstrBot 的 ``extra_user_content_parts``：
    #: 有值时 user content 变成多块数组，顺序为「正文 → 额外块 → 图片」。
    extra_user_content_parts: list[str] = field(default_factory=list)

    def cfg(self, key: str, default: Any = None) -> Any:
        """读配置（缺 config / 异常时返回 default，避免块因配置对象异常整体炸掉）。"""
        config = self.config
        if config is None:
            return default
        try:
            return config.get(key, default)
        except Exception:  # noqa: BLE001
            return default

    @property
    def intent_text(self) -> str:
        """意图判定用文本（原始正文优先）。"""
        return (self.raw_user_text or self.user_text or "").strip()

    @property
    def memory_query(self) -> str:
        """记忆检索用文本（原始正文优先）。"""
        return (self.memory_source_text or self.raw_user_text or self.user_text or "").strip()


@dataclass
class Block:
    """一个命名块：名称 + 适用条件 + 生成函数。"""

    name: str
    build: Callable[[PromptRequest], list[dict]]
    applies: Callable[[PromptRequest], bool] = lambda req: True


# ==================== 各块实现（只读 req，无副作用） ====================


def resolve_persona_preset(req: PromptRequest) -> dict | None:
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
        logger.add_info(f"#{getattr(req.runtime, 'bot_id', '?')}").warning(
            f"[Assembly] 读取角色预设失败（已忽略）: {preset_id}: {e}"
        )
        return None
    if not isinstance(preset, dict):
        logger.add_info(f"#{getattr(req.runtime, 'bot_id', '?')}").warning(
            f"[Assembly] 角色预设不存在（悬挂引用）: {preset_id}，已回退 system_prompt"
        )
        return None
    return preset


def build_persona(req: PromptRequest) -> list[dict]:
    """基础人设：优先 ``persona_preset_id``（活引用），否则用 ``system_prompt``。"""
    preset = resolve_persona_preset(req)
    if preset is not None:
        text = str(preset.get("system_prompt") or "").strip()
        if text:
            return [{"role": "system", "content": text}]
        logger.add_info(f"#{getattr(req.runtime, 'bot_id', '?')}").warning(
            "[Assembly] 角色预设没有 system_prompt，已回退 system_prompt"
        )
    system_prompt = req.cfg("system_prompt", "你是一个友好的助手。")
    return [{"role": "system", "content": system_prompt}]


def persona_begin_dialogs(req: PromptRequest) -> list[dict]:
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


def build_schedule_instruction(req: PromptRequest) -> list[dict]:
    """定时任务协议（主动消息/定时任务自身不需要再建任务，故关闭）。"""
    if not req.schedule_enable:
        return []
    return [{"role": "system", "content": SCHEDULE_INSTRUCTION}]


def assembly_profile(req: PromptRequest) -> str:
    """组装档位：``legacy``（默认，旧行为逐字不变）或 ``qq_agent``（对齐外部实现）。

    用**一个总闸**而不是散落的开关，是为了能一键回退与 A/B：某次改动效果不好时，
    把档位切回 ``legacy`` 就回到改造前，不必逐个关块。
    """
    return str(req.cfg("assembly_profile", "legacy") or "legacy").strip().lower()


def build_behavior(req: PromptRequest) -> list[dict]:
    """行为规则块：拟人化的主来源（独立成段，不塞进人格正文）。

    三条纪律：

    1. **只在 ``qq_agent`` 档注入**——``legacy`` 下整块消失，改造前的提示词逐字不变；
    2. **段落里点名的工具按本轮能力裁剪**（见 ``persona_rules.detect_caps``）：
       工具没开就整句消失，否则模型会去调不存在的工具；
    3. **``behavior_rules_override`` 可整体替换**，但安全规则与工作方式强制追加——
       一次误配置不该能把"不越权 / 不泄露 / 怎么发言"整套删掉。
    """
    if assembly_profile(req) != "qq_agent":
        return []
    if not bool(req.cfg("behavior_rules_enable", True)):
        return []

    from app.agent.persona import rules as persona_rules

    caps = persona_rules.detect_caps(req.available_tools, req.config, req.modalities)
    override = str(req.cfg("behavior_rules_override", "") or "").strip()
    if override:
        text = persona_rules.override_with_safety(override, caps)
    else:
        text = persona_rules.render(req.runtime, req.available_tools, req.config, req.modalities)
    return [{"role": "system", "content": text}] if str(text).strip() else []


def build_emoji_hint(req: PromptRequest) -> list[dict]:
    """【可用表情】提示行：把可贴标签摆进 prompt，省得模型去翻工具文档。

    三条注入条件（任一不满足就整块不出现）：本轮真的能贴表情、活跃度 > 0、
    词典里确实有标签。标签表是静态的，所以这段在会话内逐字稳定，不破坏前缀缓存。
    """
    if assembly_profile(req) != PROFILE_QQ_AGENT:
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
        logger.add_info(f"#{getattr(req.runtime, 'bot_id', '?')}").warning(
            f"[Assembly] 读取表情词典失败（已跳过表情提示）: {e}"
        )
        return []
    text = emoji_hint_line(tags)
    return [{"role": "system", "content": text}] if text else []


def build_proactive(req: PromptRequest) -> list[dict]:
    """「主动性」协议块：按本轮实际可用工具裁剪，没有可用能力时整块不注入。

    意图判定用**原始正文**（``req.intent_text``），避免元信息前缀干扰。
    ``qq_agent`` 档下去重：通用用法归行为段，这里只留本轮的意图补强与标记语义。
    """
    text = build_proactive_instruction(
        req.config,
        req.intent_text,
        available_tools=req.available_tools,
        dedupe=assembly_profile(req) == "qq_agent",
    )
    return [{"role": "system", "content": text}] if text else []


def message_meta_instruction(req: PromptRequest) -> str | None:
    """消息格式说明：让模型知道"发送者：…"是元信息、只有正文才是用户说的话。

    模式（``meta_instruction_mode`` 优先，兼容 ``meta_sender_style``）：
    ``off`` 不注入；``new`` 用单行脱敏版；其余用默认真人感版。
    此外，仅当本轮**真的注入了元信息**（``ctx.state["message_meta_injected"]``）时才注入说明——
    否则说明本身就是噪音，会诱导模型去找并不存在的"发送者："前缀。

    ``qq_agent`` 档 + 单行格式时改用**单行格式图例**：老说明讲的是"发送者：/发送了："
    那一套，格式已经变了却还在解释旧格式，等于给模型一本错的手册。
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
    if assembly_profile(req) == PROFILE_QQ_AGENT and user_message_format(req.config) == "single_line":
        return SINGLE_LINE_MESSAGE_META_INSTRUCTION
    if mode == "new":
        return MESSAGE_META_INSTRUCTION
    if bool(req.cfg("experimental_long_term_memory", False)):
        return MESSAGE_META_INSTRUCTION
    return LEGACY_MESSAGE_META_INSTRUCTION


def build_message_meta(req: PromptRequest) -> list[dict]:
    text = message_meta_instruction(req)
    return [{"role": "system", "content": text}] if text else []


def build_skills(req: PromptRequest) -> list[dict]:
    """技能块（可能多块，各自一条 system）。"""
    return [{"role": "system", "content": block} for block in (req.skill_blocks or []) if block]


def build_memory(req: PromptRequest) -> list[dict]:
    """长期记忆召回块。"""
    return [{"role": "system", "content": req.memory_text}] if req.memory_text else []


def build_background(req: PromptRequest) -> list[dict]:
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


def build_history(req: PromptRequest) -> list[dict]:
    """会话历史（调用方已渲染打标并压缩）；角色预设的预设对话前插到最前。"""
    return list(persona_begin_dialogs(req)) + list(req.history or [])


def build_group_log(req: PromptRequest) -> list[dict]:
    """群聊环境记录块（消息 + 互动 + 我的动作）。

    独立成块（而不是并进 ``background``）是为了让"这次请求为什么带了/没带群聊记录"
    能从块表一眼看出：它是**另一个来源**，有自己的开关、窗口与预算。
    """
    text = str(req.group_log_text or "").strip()
    return [{"role": "system", "content": text}] if text else []


def _extra_text_parts(req: PromptRequest) -> list[dict]:
    """把 ``extra_user_content_parts`` 转成多模态 text 块；空白项丢弃。"""
    return [
        {"type": "text", "text": str(part)}
        for part in (req.extra_user_content_parts or [])
        if str(part or "").strip()
    ]


def build_user(req: PromptRequest) -> list[dict]:
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


def build_all_blocks(req: PromptRequest) -> list[tuple[Block, list[dict]]]:
    """按固定顺序生成所有块，返回 (块定义, 消息) 以便 describe/调试归因。

    ``system_prompt_style == "single"`` 时把所有 system 块折成一条（见
    ``fold_system_blocks``）；默认 ``blocks`` 保持既有形态不变。
    """
    built: list[tuple[Block, list[dict]]] = []
    for block in BLOCKS:
        try:
            if not block.applies(req):
                built.append((block, []))
                continue
            built.append((block, block.build(req) or []))
        except Exception as e:  # noqa: BLE001 —— 单块失败不应炸掉整轮请求
            logger.add_info(f"#{getattr(req.runtime, 'bot_id', '?')}").warning(
                f"[Assembly] 块 {block.name} 构建失败（已跳过）: {e}"
            )
            built.append((block, []))
    if system_style(req) == SYSTEM_STYLE_SINGLE:
        built = fold_system_blocks(built)
    return built


#: 组装风格：各块各自一条 system（默认，保持既有形态）
SYSTEM_STYLE_BLOCKS = "blocks"
#: 组装风格：所有 system 块折成一条（对齐 AstrBot 「一整条 system prompt」的形态）
SYSTEM_STYLE_SINGLE = "single"


def system_style(req: PromptRequest) -> str:
    """读取本轮组装风格（未知值一律按默认 ``blocks`` 处理）。"""
    style = str(req.cfg("system_prompt_style", SYSTEM_STYLE_BLOCKS) or SYSTEM_STYLE_BLOCKS)
    return SYSTEM_STYLE_SINGLE if style.strip().lower() == SYSTEM_STYLE_SINGLE else SYSTEM_STYLE_BLOCKS


def fold_system_blocks(built: list[tuple[Block, list[dict]]]) -> list[tuple[Block, list[dict]]]:
    """把各块的 system 消息折成一条，挂在**第一个**含 system 的块上。

    - 只折叠 ``role == "system"`` 且 content 为字符串的消息，文本按块顺序用
      ``\\n\\n`` 连接（空内容丢弃）；其余角色（历史 / 本轮 user）与多模态块原样保留；
    - 在**块结果**上折叠而不是在摊平后的消息列表上，是为了让 ``describe()`` 的
      「块 ↔ 消息」对位继续成立：被折走消息的块显示为无消息，一眼能看出它去了哪。
    """
    texts: list[str] = []
    target: int | None = None
    kept_blocks: list[list[dict]] = []
    for index, (_block, msgs) in enumerate(built):
        kept: list[dict] = []
        for msg in msgs:
            content = msg.get("content")
            if msg.get("role") == "system" and isinstance(content, str):
                if target is None:
                    target = index
                if content.strip():
                    texts.append(content)
                continue
            kept.append(msg)
        kept_blocks.append(kept)

    if target is None:
        return built  # 没有任何 system 块 → 原样返回

    kept_blocks[target].insert(0, {"role": "system", "content": "\n\n".join(texts)})
    return [(block, kept_blocks[index]) for index, (block, _msgs) in enumerate(built)]


#: 块顺序 —— 想调整顺序/插块，只改这张表
BLOCKS: tuple[Block, ...] = (
    Block("persona", build_persona),
    # 行为规则紧跟人格：人格讲"我是谁"，行为段讲"在这个群里怎么说话"，
    # 越硬的约束越靠前（对齐 QQ-agent 的段落顺序，见 persona_rules 模块 docstring）
    Block("behavior", build_behavior),
    # 表情标签提示紧随行为段（行为段的【表情与拍一拍】指着它）
    Block("emoji_hint", build_emoji_hint),
    Block("schedule", build_schedule_instruction),
    Block("proactive", build_proactive),
    Block("message_meta", build_message_meta),
    Block("skills", build_skills),
    Block("memory", build_memory),
    Block("background", build_background),
    Block("group_log", build_group_log),
    Block("history", build_history),
    Block("user", build_user),
)


class PromptAssembler:
    """把 PromptRequest 装配成 final messages。"""

    def __init__(self, blocks: tuple[Block, ...] = BLOCKS) -> None:
        self.blocks = blocks

    def build(self, req: PromptRequest) -> list[dict]:
        """装配 messages：生成块 → 归位 → 按模态清洗（唯一兜底）→ 贴发言协议。"""
        built = build_all_blocks(req)
        messages = flatten(built)
        messages = place_images(messages, req)
        return apply_speech_protocol(sanitize(messages, req), req)

    def describe(self, req: PromptRequest) -> list[dict]:
        """返回 ``[{block, role, chars}]``：不打印全文也能看清本轮发了什么。

        统计的是**最终发出**的形态：图片归位后的 user 块按 ``user+media`` 记，
        被清洗掉的图片不会计入。块只生成一次（块函数允许有副作用，绝不能跑两遍）。
        """
        built = build_all_blocks(req)
        final = sanitize(place_images(flatten(built), req), req)
        return describe_blocks(built, final=final)

    # 便于外部按名字取块（测试/调试）
    def build_blocks(self, req: PromptRequest) -> list[dict]:
        return flatten(build_all_blocks(req))


def flatten(built: list[tuple[Block, list[dict]]]) -> list[dict]:
    """把 ``(块, 消息)`` 摊平成最终消息列表。"""
    return [msg for _block, messages in built for msg in messages]


def apply_speech_protocol(messages: list[dict], req: PromptRequest) -> list[dict]:
    """发言通道开启时，把「发言协议」贴到**本轮 user 消息末尾**。

    ## 为什么必须贴在 user 侧

    同一份真实请求（27 条 messages + 162 个工具，deepseek-v4-flash）的对照实验：

    - 协议只写在 system 行为段里 → 模型照旧输出纯文本 → 整轮静默；
    - 协议作为**尾部 system** 消息追加（"必须调用"）→ 仍然静默；
    - 协议贴在 user 消息里（同一条尾部或另起一条）→ 稳定调用 ``send_message``。

    根因不是"模型不会用工具"，而是**两条相反指令打架**：人设块里写着
    "输出：以第一人称回答…输出纯文本段落即可"，而行为段说"文本不会发出去"。
    system 层里模型跟着更近、更具体的那条走。把关于本程序的事实
    （"你输出的文本不会发出去"）贴到消息旁边，才压得住人设里的输出格式条款。

    副作用可控：只改最后一条 user 消息，历史前缀（system + 之前的对话）逐字不变，
    前缀缓存不受影响。

    另外这条**只在发言通道生效时**才贴（``speech.speech_via_tool``）：
    文本即发言的档位贴这句就是自相矛盾的假话。
    """
    from app.agent.delivery.speech import SPEECH_PROTOCOL, speech_via_tool

    config = getattr(req, "config", None)
    if config is None or not speech_via_tool(config):
        return messages

    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = f"{content}\n\n{SPEECH_PROTOCOL}"
        elif isinstance(content, list):
            content.append({"type": "text", "text": SPEECH_PROTOCOL})
        else:
            continue
        return messages
    return messages


def describe_blocks(
    built: list[tuple[Block, list[dict]]],
    *,
    final: list[dict] | None = None,
) -> list[dict]:
    """把 (块, 消息) 压成可读摘要。

    每块一行：``{"block": 名字, "role": 角色, "chars": 文本长度, "messages": 条数}``；
    空块也保留一行（``role="-"``、``chars=0``），便于"一眼看出本轮缺哪块"。
    传入 ``final`` 时角色/长度以**最终发出**的消息为准（图片归位后为 ``user+media``）。
    """
    if final is None:
        rows: list[dict] = []
        for block, msgs in built:
            if not msgs:
                rows.append({"block": block.name, "role": "-", "chars": 0, "messages": 0})
                continue
            for msg in msgs:
                role, chars = _summarize(msg)
                rows.append({
                    "block": block.name,
                    "role": role,
                    "chars": chars,
                    "messages": 1,
                })
        return rows

    # 按最终消息对齐：块与消息顺序一致（块可产出 0/1/N 条）
    cursor = 0
    rows = []
    for block, msgs in built:
        if not msgs:
            rows.append({"block": block.name, "role": "-", "chars": 0, "messages": 0})
            continue
        for i in range(len(msgs)):
            role, chars = _summarize(final[cursor]) if cursor < len(final) else ("?", 0)
            rows.append({"block": block.name, "role": role, "chars": chars, "messages": 1})
            cursor += 1
    return rows


def _summarize(msg: dict) -> tuple[str, int]:
    """单条消息的 (角色, 文本长度)；多模态记为 ``<role>+media``。"""
    content = msg.get("content")
    if isinstance(content, list):
        chars = sum(
            len(str(p.get("text", "")))
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
        return f"{msg.get('role')}+media", chars
    return str(msg.get("role", "")), len(str(content or ""))


# ==================== 归位与清洗 ====================


def place_images(messages: list[dict], req: PromptRequest) -> list[dict]:
    """把本轮图片块挂到 user 消息上。

    门控：开关打开 + 模型声明 image 模态 + 本轮确有图片（``user_images`` 由调用方
    解析后传入：流水线在 pre_request 之后统一解析，不走流水线的入口自行解析）。
    没有可用图片时保持纯文本语义（``[图片]`` 占位）。
    """
    if not req.with_images or not _images_enabled(req):
        return messages
    from app.agent.providers.modalities import supports_image

    if not supports_image(req.modalities):
        return messages
    if not req.user_images:
        return messages
    from app.agent.media.image import build_user_content

    content = build_user_content(req.user_images, text=req.user_text)
    if not content:
        return messages
    extra = _extra_text_parts(req)
    if extra:
        # 额外块插在正文之后、图片之前（对齐 AstrBot assemble_context 的顺序）
        index = 1 if content and content[0].get("type") == "text" else 0
        content[index:index] = extra
    for msg in reversed(messages):
        if msg.get("role") == "user":
            msg["content"] = content
            break
    return messages


def _images_enabled(req: PromptRequest) -> bool:
    return bool(req.cfg("image_understanding_enable", True))


def sanitize(messages: list[dict], req: PromptRequest) -> list[dict]:
    """按模型模态清洗（最后一道兜底：即使某条路径漏了门控，不支持的能力也会被替换）。"""
    from app.agent.providers.modalities import sanitize_contexts_by_modalities

    return sanitize_contexts_by_modalities(messages, req.modalities)


# ==================== 便捷入口 ====================


def build_messages(req: PromptRequest) -> list[dict]:
    """模块级快捷方式（等价于 ``PromptAssembler().build(req)``）。"""
    return PromptAssembler().build(req)


def describe(req: PromptRequest) -> list[dict]:
    """模块级快捷方式（等价于 ``PromptAssembler().describe(req)``）。"""
    return PromptAssembler().describe(req)


# ==================== 可替换装配 + post_assemble 阶段 ====================


def build_for(runtime: Any, req: PromptRequest) -> list[dict]:
    """按「运行时装配器」装配 messages。

    装配器取自 ``runtime.assembler``（``AgentRuntime`` 默认 ``PromptAssembler()``）。
    插件可用 ``supersedes = ("assembly",)`` 声明接管，或直接赋值 ``runtime.assembler``
    换成一个自带 ``build(req) -> list[dict]`` 的对象 —— 这是 1:1 复刻第三方组装
    格式（例如 AstrBot 的 ``build_main_agent``）的接入点。

    装配器缺失或不合格时回退默认装配器并留痕，避免一个坏装配器让整轮请求炸掉。
    """
    assembler = getattr(runtime, "assembler", None)
    if not callable(getattr(assembler, "build", None)):
        if assembler is not None:
            logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").warning(
                f"[Assembly] runtime.assembler 不可用（{type(assembler).__name__}），已回退默认装配器"
            )
        assembler = PromptAssembler()
    return assembler.build(req)


def _request_limits(runtime: Any) -> tuple[int, int]:
    """本轮真正会用到的 ``(max_tokens, context_window)``。

    取回退链**第一个**可用配置的生效值（回退器会按各自的配置发请求，这里只需要
    "本轮打算给多少输出、模型窗口多大"这两个数来算输入上限）。任何一个读不出来
    都退化为 0 = 未知，收口函数随后改用用户自定的 ``context_token_budget``。
    """
    chain: list[dict] = []
    try:
        getter = getattr(runtime, "provider_chain", None)
        chain = list(getter() if callable(getter) else (getter or []))
    except Exception:  # noqa: BLE001 —— 拿不到链就不做窗口推断
        chain = []
    cfg = next((c for c in chain if isinstance(c, dict)), None)
    if not cfg:
        return 0, 0
    try:
        from app.agent.providers.policy import plan_max_tokens

        max_tokens = int(plan_max_tokens(cfg).value or 0)
    except Exception:  # noqa: BLE001
        max_tokens = 0
    try:
        window = int(cfg.get("context_window", 0) or 0)
    except (TypeError, ValueError):
        window = 0
    return max_tokens, window


async def apply_post_assemble(
    runtime: Any,
    ctx: Any,
    messages: list[dict],
    *,
    req: PromptRequest | None = None,
) -> list[dict] | None:
    """把本轮 messages 挂到 ``ctx``、执行 ``post_assemble`` 钩子，并按需落盘。

    四条组装路径（chat 非流式 / chat 流式 / 主动消息 / 定时任务）统一在此收口，
    因此插件不必按路径分别打补丁。

    - 钩子可原地改写 ``ctx.messages``（增删 / 重排 / 替换），也可整体重新赋值；
    - 返回 ``None`` 表示**本轮不请求 LLM**：钩子把 messages 置空，或标记
      ``ctx.job.skip`` / ``ctx.job.superseded``（与流水线其它阶段的中止语义一致）；
    - ``ctx`` 为 None（无流水线的直调场景）时构造合成上下文，钩子仍能拿到 messages；
    - ``req`` 给出时用于落盘归类（``prompt_dump_enable`` 开启才写，见 ``prompt_dump``）。
    """
    from app.agent.hooks import run_stage
    from app.agent.loop.context import LlmContext

    if ctx is None:
        ctx = LlmContext(event=None, runtime=runtime, bot=None, session_id="", user_text="")
    ctx.messages = list(messages or [])
    proceed = await run_stage(runtime, "post_assemble", ctx)
    final = getattr(ctx, "messages", None)
    if final is None:
        final = messages
    if not proceed or not final:
        return None
    result = list(final)

    # 请求大小收口（唯一一处）：知道"模型窗口 − 输出预算"就从最旧历史丢起，
    # system 与本轮提问必留；不知道窗口时退化为用户自定的 context_token_budget。
    from app.agent.assembly.token_budget import apply_request_policy

    _mt, _window = _request_limits(runtime)
    result = apply_request_policy(
        result,
        getattr(runtime, "config", None),
        max_tokens=_mt,
        context_window=_window,
        bot_id=getattr(runtime, "bot_id", None),
    )

    # 请求落盘（默认关闭）：把「最终发出去的东西」留成可比对的证据
    from app.agent.assembly.dump import record as _record_dump

    _record_dump(
        runtime,
        result,
        session_id=getattr(req, "session_id", "") or getattr(ctx, "session_id", ""),
        kind=getattr(req, "kind", ""),
    )
    return result
