"""触发与调度模块自带的系统提示词段（L1）。

## 为什么文案在这里

"定时任务协议"和"主动性引导"都是**本模块能力**的说明书：模块禁用/卸载时，这两段
提示词必须跟着消失，否则模型会被告知去调用一个不存在的调度器/工具。
所以它们由本模块注册成 L1 段（`register_section(layer="system_prompt")`），
框架不再持有这两段文案（`app/agent/assembly/prompt.py` 里已删）。

## 措辞口径

1. **只写本轮真实可用的能力**：`available_tools` 决定哪些行出现；
2. **与行为段不重复**：通用用法（翻记录/戳/引用）归行为段，这里只留
   "本轮意图补强 / 指代 / 未展开标记 / 怎么取环境"这类每轮变化的引导（`dedupe=True`）；
3. 行的顺序固定，保证同一输入逐字节一致（前缀缓存）。
"""

from __future__ import annotations

import re
from typing import Any

# ==================== 定时任务协议 ====================

SCHEDULE_INSTRUCTION = """### 定时任务
当用户请求在特定时间做某事 / 提醒 / 定时回复时（例如"明天早上8点提醒我吃药"、"每周五下午6点发我周报"、"5分钟后叫我"、"每天中午提醒我喝水"），调用 schedule_task 工具来安排，不要用文字描述安排过程，也不要询问用户。
- 时间用自然语言写在 trigger 参数里，如：明天早上8点 / 今晚10点 / 每天早上8点 / 每周五下午6点 / 5分钟后 / 08:30
- 到点要说什么写在 note 参数里
- 用户查询或取消已有提醒时，用 schedule_task 的 list / delete 操作
- 创建新提醒前，先调用 list 查看本会话已有任务；如果已存在相同时间/重复方式的任务，不要重复创建
- 一次用户请求只创建一个新提醒；不要为同一个提醒多次调用 create
- 只有用户明确提出定时需求时才调用工具，其余情况不要调用"""

# ==================== 主动性协议 ====================

PROACTIVE_HISTORY_LINE = (
    "- 上下文不足时先查记录：用户问“刚才/之前聊了什么”“我说过什么”“你指的是哪条”，"
    "或当前对话记录不足以回答时，调用 get_chat_history（无需参数；本地记录不足会自动补拉 "
    "QQ 聊天记录）。先查到内容再回答，不要凭猜测，也不要说“我不记得”。"
    "若用户在私聊里问“某个群说了什么”，带上 group_name 查那个群（只能查你自己也在的群）。"
)
PROACTIVE_POKE_LINE = (
    "- 想引起注意或表达态度时：可以调用 send_poke 戳一戳（打招呼、催、调侃、卖萌、叫人）。"
    "群聊中带上当前群号，私聊只需对方 QQ。同一会话不要连着戳，也不要每条消息都戳。"
)
PROACTIVE_ENV_LINE = (
    "- 需要聊天环境信息时先自己取，不要猜：涉及“这个群是什么群/群名群号”“群里刚才谁在说”"
    "“某人是谁”“我是不是被 @ 了”“这条是回的哪条”时，先调用 {tools} 拿到实际数据再回答；"
    "同一轮不要重复查同一条信息。"
)
PROACTIVE_UNRESOLVED_LINE = (
    "- 记录里出现【未展开:用户123】/【未展开:引用456】/【未展开:合并转发456】这类标记，"
    "表示该处内容你还没拿到：按类型调用 expand_user / expand_message（可一次传多个 id）再回答；"
    "标记成【已展开:… → 摘要】的表示本会话已经取过，不要再重复取。"
    "确实取不到就直接说这项拿不到，不要按标记里的数字猜内容，也不要凭印象编。"
)
PROACTIVE_QUOTE_LINE = (
    "- 回复的是更早的、或多人交错容易混淆的消息时：在回复最前面写 [reply] 表示引用对方刚发的那条消息"
    "（引用是社交可见的动作，只在必要时用，一条回复最多引用一次）。"
    "需要点名某人时写 [@QQ号]，例如 [@10001]；这些标记会自动变成真正的引用/@，正文里不要保留标记。"
)
PROACTIVE_FOOTER_LINE = (
    "- 这些动作都是可选的：拿不准就不用。机械地每次都戳、每条都引用，比不做更糟。"
)
PROACTIVE_FOOTER_UNRESOLVED_LINE = (
    "- 例外：若回答依赖【未展开:…】里的内容，那就不是“可做可不做”，必须先展开；"
    "若本轮确实没有任何可用手段，就照常回答，不要专门回一句“我无法完整回答”。"
)
PROACTIVE_REFERENT_LINE = (
    "- 环境块里的“当前对话焦点”列出最近被讨论过的对象及其摘要：用户说“那个/那条/刚才说的/"
    "你刚看的/那个样子”这类**回指**时，优先指焦点里最新的那条，而不是群里时间上最新的消息。\n"
    "- 候选不止一个、你不确定用户指哪个时：先用 expand_recent 看清最近两条"
    "（必要时把“上一轮讨论过的那条”和“最近一条”一起取回）再判断，不要只挑一条就答；"
    "回答时可以用一句话说明你按哪条理解。\n"
    "- 用户说“上一条消息/刚才那条”这类**按位置指代**时用 expand_recent，不需要 id。"
)
PROACTIVE_HISTORY_NUDGE = (
    "- 就本轮而言：用户在追问历史，必须先调用 get_chat_history 核实内容再回答，"
    "不要直接说“我不记得”，也不要凭印象编。"
)
PROACTIVE_ENV_NUDGE = (
    "- 就本轮而言：用户在问群 / 成员 / 某条消息相关的信息，你必须先取得实际环境数据"
    "（expand_message / expand_user / get_current_session）再回答，"
    "不要凭昵称、ID 或印象作答。"
)

PROACTIVE_LINES: dict[str, str] = {
    "intro": "你可以像人一样主动使用能力，而不是只被动回答。",
    "history": PROACTIVE_HISTORY_LINE,
    "poke": PROACTIVE_POKE_LINE,
    "env": PROACTIVE_ENV_LINE,
    "unresolved": PROACTIVE_UNRESOLVED_LINE,
    "quote": PROACTIVE_QUOTE_LINE,
    "footer": PROACTIVE_FOOTER_LINE,
    "footer_unresolved": PROACTIVE_FOOTER_UNRESOLVED_LINE,
    "referent": PROACTIVE_REFERENT_LINE,
    "history_nudge": PROACTIVE_HISTORY_NUDGE,
    "env_nudge": PROACTIVE_ENV_NUDGE,
}

_HISTORY_INTENT_RE = re.compile(
    r"(聊天记录|聊天历史|历史消息|"
    r"刚才|刚刚|上次|上回|"
    r"(之前|前面|早些|昨天|前天)(说|聊|讲|提)|"
    r"(说过|聊过|讲过|提到过|问过)(什么|啥|的)|"
    r"哪一?(条|句)|指的哪|"
    r"(我|我们|你)(说过|问过|聊过)|"
    r"还记得|你记得吗|翻.{0,4}(记录|聊天))"
)
_ENV_INTENT_RE = re.compile(
    r"(这个群|本群|群里|群名|群号|管理员|群主|群里的人|"
    r"谁在|都在聊|谁说的|哪条消息|这条消息|上面那条|"
    r"@我|被@|at我|提到我|"
    r"这个人|那个人|他是谁|她是谁)"
)
_POKE_TOOL_NAMES = ("send_poke", "group_poke", "friend_poke")
_EXPAND_TOOL_NAMES = ("expand_recent", "expand_message", "expand_user")


def _cfg_flag(config: Any, key: str, default: bool) -> bool:
    if config is not None and hasattr(config, "get"):
        try:
            return bool(config.get(key, default))
        except Exception:  # noqa: BLE001
            return default
    return default


def history_intent(text: str) -> bool:
    return bool(_HISTORY_INTENT_RE.search(str(text or "")))


def env_intent(text: str) -> bool:
    return bool(_ENV_INTENT_RE.search(str(text or "")))


def has_any_expand(tools: set[str] | None) -> bool:
    if tools is None:
        return True
    return any(name in tools for name in _EXPAND_TOOL_NAMES)


def env_line(has_expand: bool, template: str | None = None) -> str:
    pattern = template or PROACTIVE_ENV_LINE
    if "{tools}" not in pattern:
        return pattern
    if has_expand:
        return pattern.format(tools="expand_recent / expand_message / expand_user / get_current_session")
    return pattern.format(tools="get_current_session")


def build_proactive_instruction(
    config: Any,
    user_text: str = "",
    *,
    available_tools: set[str] | None = None,
    dedupe: bool = False,
) -> str | None:
    """组装唯一的「主动性」system 块；没有可用能力时返回 None。"""
    L = PROACTIVE_LINES
    if not _cfg_flag(config, "proactive_prompt_enable", True):
        return None

    tools = set(available_tools) if available_tools is not None else None
    has_history = tools is None or "get_chat_history" in tools
    has_poke = tools is None or any(name in tools for name in _POKE_TOOL_NAMES)
    has_quote = _cfg_flag(config, "outbound_directive_enable", True)
    has_env = tools is None or "get_current_session" in tools
    has_expand = has_any_expand(tools)
    has_recent = tools is None or "expand_recent" in tools

    if dedupe:
        history_nudge = has_history and bool(
            _cfg_flag(config, "proactive_history_intent_nudge", True) and history_intent(user_text)
        )
        has_history = False
        has_poke = False
        has_quote = False
        marker_semantics = False
    else:
        history_nudge = bool(
            has_history
            and _cfg_flag(config, "proactive_history_intent_nudge", True)
            and history_intent(user_text)
        )
        marker_semantics = True

    show_env = has_env and _cfg_flag(config, "proactive_env_prompt_enable", True)
    show_unresolved = has_expand and _cfg_flag(config, "proactive_unresolved_prompt_enable", True)
    show_referent = (
        has_recent
        and _cfg_flag(config, "referent_prompt_enable", True)
        and _cfg_flag(config, "referent_resolve_enable", True)
    )
    if not (has_history or has_poke or has_quote or history_nudge or show_env or show_unresolved or show_referent):
        return None

    out = ["### 上下文补全（本轮的可用手段）"] if dedupe else ["### 主动性", L["intro"]]
    if has_history:
        out.append(L["history"])
    if history_nudge:
        out.append(L["history_nudge"])
    if show_referent:
        out.append(L["referent"])
    if show_env:
        out.append(env_line(has_expand, L["env"]))
    if show_unresolved and marker_semantics:
        out.append(L["unresolved"])
    if show_env and _cfg_flag(config, "proactive_env_intent_nudge", True) and env_intent(user_text):
        out.append(L["env_nudge"])
    if has_poke:
        out.append(L["poke"])
    if has_quote:
        out.append(L["quote"])
    if not dedupe:
        out.append(L["footer"])
    if show_unresolved:
        out.append(L["footer_unresolved"])
    return "\n".join(out)


# ==================== 注册用的段构建器 ====================


def schedule_section(req: Any) -> list[dict]:
    """定时任务协议段：模块被禁用时整段消失（本模块注册的 section 随卸载注销）。"""
    if not getattr(req, "schedule_enable", False):
        return []
    return [{"role": "system", "content": SCHEDULE_INSTRUCTION}]


def proactive_section(req: Any) -> list[dict]:
    """主动性引导段：按本轮真实工具裁剪；行为段已覆盖的通用用法不重复。"""
    text = build_proactive_instruction(
        req.config,
        getattr(req, "intent_text", "") or "",
        available_tools=getattr(req, "available_tools", None),
        dedupe=True,
    )
    return [{"role": "system", "content": text}] if text else []
