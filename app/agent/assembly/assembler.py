"""LLM 消息组装：有序 section 注册表 + 一次装配。

## 为什么有这个模块

同一类请求（普通回复 / 主动消息 / 定时任务 / 上下文压缩）此前在**四个地方各自组装**
messages：``chat.generate_response``、``chat.stream_response``、``scheduler._build_messages``、
``proactive``（内联）。块顺序、同名块的取值口径、截断与清洗都各写一遍，后果是
「为什么主动消息没有格式说明」「为什么记忆块在背景之前」这类问题无法从一处回答。

现在改为：

```
PromptRequest（一次请求的全部归一化输入）
      ↓
registry_of(runtime).resolve()   ← 12 个默认段（builtin_sections）+ 插件注册段，按 order 排序
      ↓
逐段 builder(req)                ← 段内自带适用条件，不适用返回 []
      ↓
place_images → sanitize_contexts_by_modalities   ← 归位在前、清洗在最后（清洗是唯一兜底）
      ↓
messages（OpenAI 风格）
```

- **顺序只看 section 的 ``order``**（10 的倍数给内置段，见 ``builtin_sections``），
  不藏在函数体里；插件用 ``sections.register_section`` 加一段即可，不必接管装配器；
- **每个段是独立函数**（``builtin_sections.build_xxx(req) -> list[dict]``），
  只读 req、不产生副作用，可单测；
- **注册表按 runtime 实例隔离**（``runtime.prompt_sections``）；没有 runtime 的直调/单测
  用进程级默认表。默认段由 ``default_sections()`` 首次解析时注入，"默认段总是存在"；
- **`describe(req)`** 返回 ``[{block, role, chars, messages}]``，`[Prompt]` 调试日志与
  黄金快照测试都用它，想回答"这次请求发了什么"不必再打印全文；
- **组装风格可切**：``system_prompt_style="single"`` 时把所有 system 段折成一条
  （``fold_system_blocks``），对齐第三方框架「一整条 system prompt」的形态；默认
  ``blocks`` 保持既有形态不变。

## 段顺序（与改造前逐字一致；详见 ``builtin_sections`` 的表）

| order | 段 | 角色 | 条件 |
|---|---|---|---|
| 10 | persona | system | 始终 |
| 20 | behavior | system | 选中提示词方案，或 ``assembly_profile=qq_agent`` 且未关 |
| 30 | emoji_hint | system | 能贴表情且活跃度 > 0 |
| 40 | schedule | system | ``schedule_enable`` |
| 50 | proactive | system | 有可用能力时 |
| 60 | message_meta | system | 消歧说明开启且注入了元信息 |
| 70 | skills | system ×N | 有技能时 |
| 80 | memory | system | 记忆有召回 |
| 90 | background | system | 指代块或聊天环境背景非空 |
| 100 | group_log | system | 群聊记录模块在场且有记录 |
| 105 | extra | system ×N | **兼容通道**：``req.extra_blocks`` 非空（不是默认段） |
| 110 | history | 对话 | 非空 |
| 120 | user | user | 始终 |

``extra`` 是改造前就存在的模块额外块通道（``runtime.context_providers`` 收集），
保留为注册表之外的兼容段：位置固定在群聊记录之后、历史之前，行为不变；
新模块想加提示词请用 ``register_section``（见 ``docs/MODULE_DEV.md`` §4.7.4）。

## 失败语义（绝不发畸形 prompt）

- **注册表解析失败**（两个 ``complete`` 段抢整条 system 等）→ ``logger.error`` 留痕后
  重新抛出，整轮不请求 LLM；
- **单个段 builder 抛异常** → ``logger.error`` 留痕并**跳过该段**（不抛错）。理由：
  段的失败是局部的（例如记忆召回异常），跳过后 prompt 只是少了这一段信息，
  人格/历史/本轮仍在，模型仍能正常回答；若升级为整轮失败，一次可恢复的旁路
  故障就会让用户完全收不到回复。这与改造前"单块失败已跳过"的行为一致，
  只是日志从 warning 提升为 error 以便排查。

提示词方案（``prompt_preset``）改的是**文案不是段**：段的位置与顺序不变，正文换成
该方案的版本（见 ``assembly.presets``）；人格段永远不受影响。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.agent.assembly.builtin_sections import (
    EXTRA_SECTION,
    _extra_text_parts,
    assembly_profile,
    build_background,
    build_behavior,
    build_emoji_hint,
    build_extra_blocks,
    build_group_log,
    build_history,
    build_memory,
    build_message_meta,
    build_persona,
    build_proactive,
    build_schedule_instruction,
    build_skills,
    build_user,
    default_sections,
    message_meta_instruction,
    persona_begin_dialogs,
    prompt_preset,
    resolve_persona_preset,
)
from app.agent.assembly.sections import (
    SCOPE_AGENT,
    register_section,
    registry_of,
    unregister_section,
)
from app.core.logger import account_label, logger


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
    #: 模块贡献的额外 system 块 ``[(块名, 文本)]``（由 `runtime.context_providers` 收集）。
    #: 装配器只负责按顺序渲染，来源与内容都在模块侧 —— 这是「记忆 / 知识库 / 群记录
    #: 做成模块」时**不必接管整个装配器**的接入点（新代码优先用 section 注册表）。
    extra_blocks: list[tuple[str, str]] = field(default_factory=list)

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
    """（deprecated）旧的「命名块」接口：名称 + 生成函数 + 适用条件。

    新代码请用 ``sections.Section`` + ``register_section(runtime, ...)``；本类只为
    兼容既有外部引用（``fold_system_blocks`` 的测试替身、自定义块表）保留。
    它没有 ``order``，只在 ``PromptAssembler(blocks=...)`` 的旧构造路径里使用。
    """

    name: str
    build: Callable[[PromptRequest], list[dict]]
    applies: Callable[[PromptRequest], bool] = lambda req: True


#: 装配产物：``(段, 消息列表)``，段只用 ``.name`` 归因（``Block`` / ``Section`` 都满足）
BuiltBlocks = list[tuple[Any, list[dict]]]


# ==================== 解析与逐段生成 ====================


def resolve_blocks(req: PromptRequest) -> list[Any]:
    """解析本轮要渲染的段：注册表结果 + 兼容的 ``extra`` 通道，按 order 排好。

    ``extra`` 不是默认段（不参与 ``resolve()`` 的 12 段快照），但每次渲染都会插回
    ``group_log`` 与 ``history`` 之间（``EXTRA_ORDER=105``），保持既有输出不变；
    若插件已自行注册同名 ``extra`` 段，则以插件段为准（不重复插入）。
    """
    sections = list(registry_of(getattr(req, "runtime", None)).resolve(scope=SCOPE_AGENT))
    if not any(section.name == EXTRA_SECTION.name for section in sections):
        sections.append(EXTRA_SECTION)
    sections.sort(key=lambda section: (section.order, section.name))
    return sections


def build_all_blocks(req: PromptRequest, sections: list[Any] | None = None) -> BuiltBlocks:
    """按顺序逐段生成，返回 ``(段定义, 消息)`` 以便 describe/调试归因。

    - 段解析失败（``resolve()`` 抛错，如两个 ``complete`` 段）→ ``logger.error`` 后
      **重新抛出**：两条"整条 system"同时进 messages 是畸形 prompt，宁可本轮不请求；
    - 单个段 builder 抛异常 → ``logger.error`` 后**跳过该段**。段失败是局部的
      （记忆召回超时之类），跳过后 prompt 少一段信息但仍可正常回答；若整轮失败，
      一次旁路故障就会让用户完全收不到回复，代价远大于收益。该取舍与改造前
      「单块失败已跳过」一致，只把日志级别从 warning 提到 error。

    ``system_prompt_style == "single"`` 时把所有 system 段折成一条（见
    ``fold_system_blocks``）；默认 ``blocks`` 保持既有形态不变。
    """
    if sections is None:
        try:
            sections = resolve_blocks(req)
        except Exception as e:  # noqa: BLE001 —— 留痕后中止本轮，绝不发畸形 prompt
            logger.add_info(account_label(getattr(req.runtime, 'bot_id', '?'))).error(
                f"[Assembly] 提示词段解析失败，已中止本轮组装: {e}"
            )
            raise
    built: BuiltBlocks = []
    for section in sections:
        try:
            messages = section.builder(req) or []
        except Exception as e:  # noqa: BLE001 —— 单段失败不应炸掉整轮请求
            logger.add_info(account_label(getattr(req.runtime, 'bot_id', '?'))).error(
                f"[Assembly] 段 {section.name} 构建失败（已跳过该段）: {e}"
            )
            messages = []
        built.append((section, list(messages)))
    if system_style(req) == SYSTEM_STYLE_SINGLE:
        built = fold_system_blocks(built)
    return built


def _build_legacy_blocks(req: PromptRequest, blocks: tuple[Block, ...]) -> BuiltBlocks:
    """旧的自定义块表路径（``PromptAssembler(blocks=...)``）：行为与改造前一致。"""
    built: BuiltBlocks = []
    for block in blocks:
        try:
            if not block.applies(req):
                built.append((block, []))
                continue
            built.append((block, block.build(req) or []))
        except Exception as e:  # noqa: BLE001 —— 单块失败不应炸掉整轮请求
            logger.add_info(account_label(getattr(req.runtime, 'bot_id', '?'))).error(
                f"[Assembly] 块 {block.name} 构建失败（已跳过）: {e}"
            )
            built.append((block, []))
    if system_style(req) == SYSTEM_STYLE_SINGLE:
        built = fold_system_blocks(built)
    return built


#: 组装风格：各段各自一条 system（默认，保持既有形态）
SYSTEM_STYLE_BLOCKS = "blocks"
#: 组装风格：所有 system 段折成一条（对齐 AstrBot 「一整条 system prompt」的形态）
SYSTEM_STYLE_SINGLE = "single"


def system_style(req: PromptRequest) -> str:
    """读取本轮组装风格（未知值一律按默认 ``blocks`` 处理）。"""
    style = str(req.cfg("system_prompt_style", SYSTEM_STYLE_BLOCKS) or SYSTEM_STYLE_BLOCKS)
    return SYSTEM_STYLE_SINGLE if style.strip().lower() == SYSTEM_STYLE_SINGLE else SYSTEM_STYLE_BLOCKS


def fold_system_blocks(built: BuiltBlocks) -> BuiltBlocks:
    """把各段的 system 消息折成一条，挂在**第一个**含 system 的段上。

    - 只折叠 ``role == "system"`` 且 content 为字符串的消息，文本按段顺序用
      ``\\n\\n`` 连接（空内容丢弃）；其余角色（历史 / 本轮 user）与多模态块原样保留；
    - 在**段结果**上折叠而不是在摊平后的消息列表上，是为了让 ``describe()`` 的
      「段 ↔ 消息」对位继续成立：被折走消息的段显示为无消息，一眼能看出它去了哪。
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
        return built  # 没有任何 system 段 → 原样返回

    kept_blocks[target].insert(0, {"role": "system", "content": "\n\n".join(texts)})
    return [(block, kept_blocks[index]) for index, (block, _msgs) in enumerate(built)]


def default_blocks() -> tuple[Block, ...]:
    """旧 ``Block`` 形态的默认块表（12 段 + ``extra``），按 order 排好。

    deprecated：仅供既有测试/外部引用按名字查看顺序；装配走 ``SectionRegistry``。
    """
    pairs = [(section.order, Block(section.name, section.builder)) for section in default_sections()]
    pairs.append((EXTRA_SECTION.order, Block(EXTRA_SECTION.name, EXTRA_SECTION.builder)))
    pairs.sort(key=lambda item: (item[0], item[1].name))
    return tuple(block for _order, block in pairs)


#: 兼容常量（deprecated）：旧代码/测试用它看"默认块序列"；装配不再读它。
BLOCKS: tuple[Block, ...] = default_blocks()


class PromptAssembler:
    """把 PromptRequest 装配成 final messages。

    默认（``blocks=None``）走 section 注册表：``registry_of(req.runtime).resolve()``；
    传入 ``blocks`` 的旧构造路径保留一个版本，便于自定义块表逐字回退。
    """

    def __init__(self, blocks: tuple[Block, ...] | None = None) -> None:
        #: 旧接口：自定义块表（None = 走 section 注册表）
        self.blocks = blocks

    def _built(self, req: PromptRequest) -> BuiltBlocks:
        if self.blocks is None:
            return build_all_blocks(req)
        return _build_legacy_blocks(req, self.blocks)

    def build(self, req: PromptRequest) -> list[dict]:
        """装配 messages：解析段 → 逐段生成 → 归位 → 按模态清洗（唯一兜底）→ 贴发言协议。"""
        built = self._built(req)
        messages = flatten(built)
        messages = place_images(messages, req)
        return apply_speech_protocol(sanitize(messages, req), req)

    def describe(self, req: PromptRequest) -> list[dict]:
        """返回 ``[{block, role, chars, messages}]``：不打印全文也能看清本轮发了什么。

        统计的是**最终发出**的形态：图片归位后的 user 段按 ``user+media`` 记，
        被清洗掉的图片不会计入。段只生成一次（builder 允许有副作用，绝不能跑两遍）。
        """
        built = self._built(req)
        final = sanitize(place_images(flatten(built), req), req)
        return describe_blocks(built, final=final)

    # 便于外部按名字取段（测试/调试）
    def build_blocks(self, req: PromptRequest) -> list[dict]:
        return flatten(self._built(req))


def flatten(built: BuiltBlocks) -> list[dict]:
    """把 ``(段, 消息)`` 摊平成最终消息列表。"""
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
    built: BuiltBlocks,
    *,
    final: list[dict] | None = None,
) -> list[dict]:
    """把 (段, 消息) 压成可读摘要。

    每段一行：``{"block": 名字, "role": 角色, "chars": 文本长度, "messages": 条数}``；
    空段也保留一行（``role="-"``、``chars=0``），便于"一眼看出本轮缺哪块"。
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

    # 按最终消息对齐：段与消息顺序一致（段可产出 0/1/N 条）
    cursor = 0
    rows = []
    for block, msgs in built:
        if not msgs:
            rows.append({"block": block.name, "role": "-", "chars": 0, "messages": 0})
            continue
        for _ in range(len(msgs)):
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
    from app.domain.modalities import supports_image

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
    from app.domain.modalities import sanitize_contexts_by_modalities

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

    另外把 ``runtime`` 回填到 ``req.runtime``（调用方没填时）：段注册表按 runtime
    实例隔离，回填后 ``build_for(runtime, req)`` 与直接 ``PromptAssembler().build(req)``
    取到的是同一张表，插件在这个 runtime 上注册的段不会被漏掉。

    装配器缺失或不合格时回退默认装配器并留痕，避免一个坏装配器让整轮请求炸掉。
    """
    if getattr(req, "runtime", None) is None:
        try:
            req.runtime = runtime
        except Exception as e:  # noqa: BLE001 —— 只读请求替身上回填失败就按无 runtime 处理
            logger.debug(f"[Assembly] 回填 req.runtime 失败（按无运行时处理）: {e}")
    assembler = getattr(runtime, "assembler", None)
    if not callable(getattr(assembler, "build", None)):
        if assembler is not None:
            logger.add_info(account_label(getattr(runtime, 'bot_id', '?'))).warning(
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

    # 策略侧配置：`llm_assembly` 模块接管时把视图挂在 `runtime.assembly_options`，
    # 于是 token 预算与请求落盘这些开关读的是**模块配置**（未接管时退回 agent 配置）。
    policy_cfg = getattr(runtime, "assembly_options", None) or getattr(runtime, "config", None)

    _mt, _window = _request_limits(runtime)
    result = apply_request_policy(
        result,
        policy_cfg,
        max_tokens=_mt,
        context_window=_window,
        bot_id=getattr(runtime, "bot_id", None),
    )

    # Q7：messages.before_send —— 最终请求体**发出前**的最后一道改写（waterfall，
    # 见 `app.agent.loop.middleware`）。与 `post_assemble` 钩子的区别是语义：这里的 handler
    # 是"可读可替换的一步"，没有中间件时原样返回（零开销），拦截（abort/异常）即
    # 本轮不请求 LLM（与上面钩子的中止语义一致）。放在 token 预算之后，
    # 保证它看到的就是即将发出去的那份 messages。
    from app.agent.loop.middleware import apply_messages_before_send

    final_messages = await apply_messages_before_send(runtime, result, ctx=ctx)
    if final_messages is None:
        return None
    result = final_messages

    # 请求落盘（默认关闭）：把「最终发出去的东西」留成可比对的证据
    from app.agent.assembly.dump import record as _record_dump

    _record_dump(
        runtime,
        result,
        session_id=getattr(req, "session_id", "") or getattr(ctx, "session_id", ""),
        kind=getattr(req, "kind", ""),
    )
    return result


__all__ = [
    "BLOCKS",
    "SYSTEM_STYLE_BLOCKS",
    "SYSTEM_STYLE_SINGLE",
    "Block",
    "PromptAssembler",
    "PromptRequest",
    "apply_post_assemble",
    "apply_speech_protocol",
    "assembly_profile",
    "build_all_blocks",
    "build_background",
    "build_behavior",
    "build_emoji_hint",
    "build_extra_blocks",
    "build_for",
    "build_group_log",
    "build_history",
    "build_memory",
    "build_message_meta",
    "build_messages",
    "build_persona",
    "build_proactive",
    "build_schedule_instruction",
    "build_skills",
    "build_user",
    "default_blocks",
    "describe",
    "describe_blocks",
    "flatten",
    "fold_system_blocks",
    "message_meta_instruction",
    "persona_begin_dialogs",
    "place_images",
    "prompt_preset",
    "register_section",
    "resolve_blocks",
    "resolve_persona_preset",
    "sanitize",
    "system_style",
    "unregister_section",
]
