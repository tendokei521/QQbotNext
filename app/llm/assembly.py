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
  想回答"这次请求发了什么"不必再打印全文。

## 块顺序（与重构前的既有顺序保持一致）

| # | 块 | 角色 | 来源 | 条件 |
|---|---|---|---|---|
| 1 | persona | system | ``config.system_prompt`` | 始终 |
| 2 | schedule | system | ``prompt.SCHEDULE_INSTRUCTION`` | ``schedule_enable`` |
| 3 | proactive | system | ``prompt.build_proactive_instruction`` | 有可用能力时 |
| 4 | message_meta | system | ``LEGACY_/MESSAGE_META_INSTRUCTION`` | 消歧说明开启时 |
| 5 | skills | system ×N | ``runtime.skills.prompt_blocks()`` | 有技能时 |
| 6 | memory | system | ``memory.recall_block_async`` | 记忆有召回 |
| 7 | background | system | 指代块 + 聊天环境背景（合并为一块） | 任一非空 |
| 8 | group_log | system | 群聊环境记录（消息 + 互动 + 我的动作） | 群聊记录模块在场且有记录 |
| 9 | history | 对话 | 会话历史（已渲染打标） | 非空 |
| 10 | user | user | 本轮消息（可含图片块） | 始终 |

注意：块 6 在块 7 之前是历史既有行为；块 7 内部"指代在前、环境在后"用 ``\n\n`` 连接。
块 8 与块 7 是两个**不同来源**（持续记录 vs 按需拉取），正文按 message_id 去重。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.logger import logger
from app.llm.prompt import (
    LEGACY_MESSAGE_META_INSTRUCTION,
    MESSAGE_META_INSTRUCTION,
    SCHEDULE_INSTRUCTION,
    build_proactive_instruction,
)


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
    #: 群聊环境记录块（群聊记录模块产出，见 ``app.llm.group_log.context``）。
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


def build_persona(req: PromptRequest) -> list[dict]:
    """基础人设。"""
    system_prompt = req.cfg("system_prompt", "你是一个友好的助手。")
    return [{"role": "system", "content": system_prompt}]


def build_schedule_instruction(req: PromptRequest) -> list[dict]:
    """定时任务协议（主动消息/定时任务自身不需要再建任务，故关闭）。"""
    if not req.schedule_enable:
        return []
    return [{"role": "system", "content": SCHEDULE_INSTRUCTION}]


def build_proactive(req: PromptRequest) -> list[dict]:
    """「主动性」协议块：按本轮实际可用工具裁剪，没有可用能力时整块不注入。

    意图判定用**原始正文**（``req.intent_text``），避免元信息前缀干扰。
    """
    text = build_proactive_instruction(
        req.config,
        req.intent_text,
        available_tools=req.available_tools,
    )
    return [{"role": "system", "content": text}] if text else []


def message_meta_instruction(req: PromptRequest) -> str | None:
    """消息格式说明：让模型知道"发送者：…"是元信息、只有正文才是用户说的话。

    模式（``meta_instruction_mode`` 优先，兼容 ``meta_sender_style``）：
    ``off`` 不注入；``new`` 用单行脱敏版；其余用默认真人感版。
    此外，仅当本轮**真的注入了元信息**（``ctx.state["message_meta_injected"]``）时才注入说明——
    否则说明本身就是噪音，会诱导模型去找并不存在的"发送者："前缀。
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
    """会话历史（调用方已渲染打标并压缩）。"""
    return list(req.history or [])


def build_group_log(req: PromptRequest) -> list[dict]:
    """群聊环境记录块（消息 + 互动 + 我的动作）。

    独立成块（而不是并进 ``background``）是为了让"这次请求为什么带了/没带群聊记录"
    能从块表一眼看出：它是**另一个来源**，有自己的开关、窗口与预算。
    """
    text = str(req.group_log_text or "").strip()
    return [{"role": "system", "content": text}] if text else []


def build_user(req: PromptRequest) -> list[dict]:
    """本轮消息。图片块由 ``place_images`` 在清洗前归位。"""
    return [{"role": "user", "content": req.user_text}]


def build_all_blocks(req: PromptRequest) -> list[tuple[Block, list[dict]]]:
    """按固定顺序生成所有块，返回 (块定义, 消息) 以便 describe/调试归因。"""
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
    return built


#: 块顺序 —— 想调整顺序/插块，只改这张表
BLOCKS: tuple[Block, ...] = (
    Block("persona", build_persona),
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
        """装配 messages：生成块 → 归位 → 按模态清洗（唯一兜底）。"""
        built = build_all_blocks(req)
        messages = flatten(built)
        messages = place_images(messages, req)
        return sanitize(messages, req)

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
    from app.llm.providers.modalities import supports_image

    if not supports_image(req.modalities):
        return messages
    if not req.user_images:
        return messages
    from app.llm.image import build_user_content

    content = build_user_content(req.user_images, text=req.user_text)
    if not content:
        return messages
    for msg in reversed(messages):
        if msg.get("role") == "user":
            msg["content"] = content
            break
    return messages


def _images_enabled(req: PromptRequest) -> bool:
    return bool(req.cfg("image_understanding_enable", True))


def sanitize(messages: list[dict], req: PromptRequest) -> list[dict]:
    """按模型模态清洗（最后一道兜底：即使某条路径漏了门控，不支持的能力也会被替换）。"""
    from app.llm.providers.modalities import sanitize_contexts_by_modalities

    return sanitize_contexts_by_modalities(messages, req.modalities)


# ==================== 便捷入口 ====================


def build_messages(req: PromptRequest) -> list[dict]:
    """模块级快捷方式（等价于 ``PromptAssembler().build(req)``）。"""
    return PromptAssembler().build(req)


def describe(req: PromptRequest) -> list[dict]:
    """模块级快捷方式（等价于 ``PromptAssembler().describe(req)``）。"""
    return PromptAssembler().describe(req)
