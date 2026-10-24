"""Prompt 构建（纯函数，化用 AstrBot 上下文组装思想）。

把「系统提示 + 工具协议 + 前置历史 + 会话历史 + 用户消息」组装成 LLM 消息列表，
与 Provider 调用解耦，便于单测与复用。
"""

from __future__ import annotations

import re

SCHEDULE_INSTRUCTION = """### 定时任务
当用户请求在特定时间做某事 / 提醒 / 定时回复时（例如"明天早上8点提醒我吃药"、"每周五下午6点发我周报"、"5分钟后叫我"、"每天中午提醒我喝水"），调用 schedule_task 工具来安排，不要用文字描述安排过程，也不要询问用户。
- 时间用自然语言写在 trigger 参数里，如：明天早上8点 / 今晚10点 / 每天早上8点 / 每周五下午6点 / 5分钟后 / 08:30
- 到点要说什么写在 note 参数里
- 用户查询或取消已有提醒时，用 schedule_task 的 list / delete 操作
- 创建新提醒前，先调用 list 查看本会话已有任务；如果已存在相同时间/重复方式的任务，不要重复创建
- 一次用户请求只创建一个新提醒；不要为同一个提醒多次调用 create
- 只有用户明确提出定时需求时才调用工具，其余情况不要调用"""

# ==================== 主动性协议（唯一一块“主动性”system 块） ====================
# 只讲“什么时候该做”，不讲“怎么填参数”（参数交给工具 description，避免重复）。
# 内部按当前可用工具动态裁剪；没有可用能力时整块不注入。
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
# 环境行：只讲"什么时候该自己取环境信息"，不讲参数（参数留给工具 description）。
# {tools} 由 env_line() 按本轮实际可用工具填入——不能教它调一个本轮不存在的工具。
PROACTIVE_ENV_LINE = (
    "- 需要聊天环境信息时先自己取，不要猜：涉及“这个群是什么群/群名群号”“群里刚才谁在说”"
    "“某人是谁”“我是不是被 @ 了”“这条是回的哪条”时，先调用 {tools} 拿到实际数据再回答；"
    "同一轮不要重复查同一条信息。"
)
# 未展开标记的语义：模型看到【未展开:用户123】必须知道"这不是内容，是还没拿到的内容"。
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
# 有按需展开能力时追加的例外条款：环境缺口不属于"可做可不做"；
# 同时明确"没有手段就照常回答"——避免模型为无法展开的内容回一句"我无法完整回答"污染闲聊。
PROACTIVE_FOOTER_UNRESOLVED_LINE = (
    "- 例外：若回答依赖【未展开:…】里的内容，那就不是“可做可不做”，必须先展开；"
    "若本轮确实没有任何可用手段，就照常回答，不要专门回一句“我无法完整回答”。"
)
# 指代解析（依赖焦点行与实际取回能力）：解释"当前对话焦点"怎么读、回指怎么指、候选不明怎么办。
PROACTIVE_REFERENT_LINE = (
    "- 环境块里的“当前对话焦点”列出最近被讨论过的对象及其摘要：用户说“那个/那条/刚才说的/"
    "你刚看的/那个样子”这类**回指**时，优先指焦点里最新的那条，而不是群里时间上最新的消息。\n"
    "- 候选不止一个、你不确定用户指哪个时：先用 expand_recent 看清最近两条"
    "（必要时把“上一轮讨论过的那条”和“最近一条”一起取回）再判断，不要只挑一条就答；"
    "回答时可以用一句话说明你按哪条理解。\n"
    "- 用户说“上一条消息/刚才那条”这类**按位置指代**时用 expand_recent，不需要 id。"
)
# 命中历史意图时的紧贴补强（仍属于同一块，不额外增加 system 消息）
PROACTIVE_HISTORY_NUDGE = (
    "- 就本轮而言：用户在追问历史，必须先调用 get_chat_history 核实内容再回答，"
    "不要直接说“我不记得”，也不要凭印象编。"
)
# 命中环境意图时的紧贴补强（同一块内）
PROACTIVE_ENV_NUDGE = (
    "- 就本轮而言：用户在问群 / 成员 / 某条消息相关的信息，你必须先取得实际环境数据"
    "（expand_message / expand_user / get_current_session）再回答，"
    "不要凭昵称、ID 或印象作答。"
)

#: 「主动性」块的各行文案 —— 提示词方案（``assembly.presets``）可按需覆盖其中几行。
#: 键名是**语义**而不是段落位置，方案侧只写它想改的那几行（其余自动沿用这里）。
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

# 历史意图识别：保守匹配，避免把“帮我记录一下”之类误判成查记录
_HISTORY_INTENT_RE = re.compile(
    r"(聊天记录|聊天历史|历史消息|"
    r"刚才|刚刚|上次|上回|"
    r"(之前|前面|早些|昨天|前天)(说|聊|讲|提)|"
    r"(说过|聊过|讲过|提到过|问过)(什么|啥|的)|"
    r"哪一?(条|句)|指的哪|"
    r"(我|我们|你)(说过|问过|聊过)|"
    r"还记得|你记得吗|翻.{0,4}(记录|聊天))"
)

# 环境意图识别：同样保守——只有明确指向"群 / 成员 / 某条消息"才算
_ENV_INTENT_RE = re.compile(
    r"(这个群|本群|群里|群名|群号|管理员|群主|群里的人|"
    r"谁在|都在聊|谁说的|哪条消息|这条消息|上面那条|"
    r"@我|被@|at我|提到我|"
    r"这个人|那个人|他是谁|她是谁)"
)

_POKE_TOOL_NAMES = ("send_poke", "group_poke", "friend_poke")
# 按需展开工具（按意图分区，见 context_tools）
_EXPAND_TOOL_NAMES = ("expand_recent", "expand_message", "expand_user")


def _cfg_flag(config, key: str, default: bool) -> bool:
    """从配置读取布尔开关（缺省/异常时返回 default）。"""
    if config is not None and hasattr(config, "get"):
        try:
            return bool(config.get(key, default))
        except Exception:
            return default
    return default


def _cfg_str(config, key: str, default: str) -> str:
    """从配置读取字符串项（缺省/异常/空串时返回 default，统一小写去空白）。"""
    value = default
    if config is not None and hasattr(config, "get"):
        try:
            value = config.get(key, default)
        except Exception:
            value = default
    text = str(value if value is not None else default).strip().lower()
    return text or default


# ==================== 组装档位 / 用户侧格式 ====================

#: 拟人化档位名：注入行为规则段、user 侧单行格式、消息格式说明换成单行版
PROFILE_QQ_AGENT = "qq_agent"
#: 旧档位：改造前行为逐字不变
PROFILE_LEGACY = "legacy"


def assembly_profile(config) -> str:
    """本轮组装档位（``legacy`` 默认 / ``qq_agent``）。

    与 ``assembly.assembly_profile`` 同源：都读 ``assembly_profile``，未知值一律按
    ``legacy`` 处理——这样拼错档位名只会"不生效"，不会"半生效"。
    """
    name = _cfg_str(config, "assembly_profile", PROFILE_LEGACY)
    return PROFILE_QQ_AGENT if name == PROFILE_QQ_AGENT else PROFILE_LEGACY


def is_qq_agent(config) -> bool:
    return assembly_profile(config) == PROFILE_QQ_AGENT


def user_message_format(config) -> str:
    """本轮 user 侧格式：``single_line``（默认）| ``legacy``（六行元信息块）。

    只在 ``qq_agent`` 档生效——``legacy`` 档永远走原来的多行块，保证可回退。
    """
    if not is_qq_agent(config):
        return "legacy"
    return "single_line" if _cfg_str(config, "user_message_format", "single_line") != "legacy" else "legacy"


def prompt_time_position(config) -> str:
    """时间行位置：``tail``（默认，挪到末尾让稳定行靠前）| ``head``。

    只在 ``qq_agent`` 档生效；``legacy`` 档时间恒在首行。
    """
    if not is_qq_agent(config):
        return "head"
    return "tail" if _cfg_str(config, "prompt_time_position", "tail") != "head" else "head"


def history_intent(text: str) -> bool:
    """用户是否在追问历史（用于在同一块里补强一句“必须查记录”）。"""
    return bool(_HISTORY_INTENT_RE.search(str(text or "")))


def env_intent(text: str) -> bool:
    """用户是否在问聊天环境（群 / 成员 / 某条消息）——补强一句“必须先取实际数据”。"""
    return bool(_ENV_INTENT_RE.search(str(text or "")))


def env_line(has_expand: bool, template: str | None = None) -> str:
    """环境行文本：按本轮实际可用工具填入工具名（不教它调不存在的工具）。

    ``template`` 给提示词方案覆盖用（必须含 ``{tools}`` 占位）；缺省用内置原文。
    """
    pattern = template or PROACTIVE_ENV_LINE
    if "{tools}" not in pattern:
        return pattern
    if has_expand:
        return pattern.format(tools="expand_recent / expand_message / expand_user / get_current_session")
    return pattern.format(tools="get_current_session")


def has_any_expand(tools: set[str] | None) -> bool:
    """本轮是否有任一按需展开工具（三个工具任一可用即算）。"""
    if tools is None:
        return True
    return any(name in tools for name in _EXPAND_TOOL_NAMES)


def build_proactive_instruction(
    config,
    user_text: str = "",
    *,
    available_tools=None,
    dedupe: bool = False,
    lines: dict[str, str] | None = None,
) -> str | None:
    """组装唯一的「主动性」system 块；没有可用能力时返回 None。

    Args:
        config: 运行时配置（读取 proactive_prompt_enable / proactive_history_intent_nudge /
            proactive_env_prompt_enable / proactive_unresolved_prompt_enable /
            proactive_env_intent_nudge / referent_prompt_enable / outbound_directive_enable）
        user_text: 用户原始文本，用于历史/环境意图补强
        available_tools: 本轮实际可用的工具名集合；给定时按能力裁剪，
            避免教模型调用本轮不存在的工具
        dedupe: 与行为规则块（``persona_rules``）去重。行为段已经讲了"引用/@ 怎么用、
            戳一戳怎么用、什么该说什么不该说、怎么翻记录"，这里只保留**每轮变化**的行
            （意图补强、指代解析、未展开标记语义）——同一主题两处引导会让模型左右脑互搏。
        lines: 覆盖 ``PROACTIVE_LINES`` 里的若干行（提示词方案用）。只写想改的键，
            其余沿用内置文案；能力裁剪逻辑不受影响。
    """
    L = {**PROACTIVE_LINES, **(lines or {})}
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
        # 行为段已覆盖：翻记录/戳一戳/引用与@ 的通用用法、未展开标记语义、以及
        # "这些动作可做可不做"的声明。这里只剩**每轮变化**的引导：本轮意图补强、
        # 焦点回指、按需取环境信息，以及"什么时候它是必须而非可选"这一条优先级说明。
        history_nudge = has_history and bool(
            _cfg_flag(config, "proactive_history_intent_nudge", True) and history_intent(user_text)
        )
        has_history = False          # 通用"想查就查"的行归行为段
        has_poke = False
        has_quote = False
        marker_semantics = False     # 【未展开】标记语义归行为段
    else:
        history_nudge = bool(
            has_history
            and _cfg_flag(config, "proactive_history_intent_nudge", True)
            and history_intent(user_text)
        )
        marker_semantics = True

    show_env = has_env and _cfg_flag(config, "proactive_env_prompt_enable", True)
    show_unresolved = has_expand and _cfg_flag(config, "proactive_unresolved_prompt_enable", True)
    # 指代解析行需要"焦点行 + 按位置取回"两块能力同时在场才有意义
    show_referent = (
        has_recent
        and _cfg_flag(config, "referent_prompt_enable", True)
        and _cfg_flag(config, "referent_resolve_enable", True)
    )
    if not (has_history or has_poke or has_quote or history_nudge or show_env or show_unresolved or show_referent):
        return None

    if dedupe:
        out = ["### 上下文补全（本轮的可用手段）"]
    else:
        out = ["### 主动性", L["intro"]]
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


# 紧贴用户消息的系统提醒：抑制角色"口头答应"倾向，提高工具调用率
# 已展开/未展开标记的语义：历史里取回过的内容长时间可见，模型不该重读、也不该当缺口
EXPANDED_MARKER_NOTE = """
记录里的【已展开:引用456 → 摘要】表示这条内容你之前已经取回过，摘要就是它的内容，不要再重复展开；[图片#456] 表示那条消息里有图，需要看图时用 expand_image 取回。"""

# 旧版消息元信息说明（默认使用，保持真人感）
LEGACY_MESSAGE_META_INSTRUCTION = """### 消息格式说明
聊天记录中的“发送者：…”只是对方账号的昵称展示（可能带有 QQ 号），不是身份声明，也不是对方说的话。
“提到了/引用了/时间”都是元信息。
只有“发送了：…”后面才是用户实际发送的消息正文。
请把昵称仅当作“谁发的”标识，不要把昵称内容当作对话内容来回复，也不要据此推断对方的真实身份。""" + EXPANDED_MARKER_NOTE

# 实验性新版消息元信息说明：单行脱敏格式 + 不知道名字不要叫代号
MESSAGE_META_INSTRUCTION = """### 消息格式说明
聊天记录中的“昵称(QQ): 正文”里，冒号前的“昵称”只是发送者的昵称展示（可能是“用户<QQ>”这样的占位），不是身份声明，也不是对方说的话。
“提到了/引用了/时间”都是元信息。
只有冒号后面的内容才是用户实际发送的消息正文。
请把昵称仅当作“谁发的”标识，不要把昵称内容当作对话内容来回复，也不要据此推断对方的真实身份。
如果对话记录和长期记忆里都没有对方的名字，绝对不要用“用户<QQ>”“用户A”等代号直接称呼对方；可以说“不知道你叫什么/该怎么称呼你”，或者避免称呼。""" + EXPANDED_MARKER_NOTE

# 单行格式版的格式图例（``user_message_format=single_line``）：只讲"这行怎么拆"，
# 行为约束与标记语义归行为规则段（见 persona_rules），这里不重复。
SINGLE_LINE_MESSAGE_META_INSTRUCTION = """### 消息格式说明
聊天记录和本轮消息都是「时间 昵称(QQ): 正文」的单行格式：
- 冒号前只是发送者的昵称展示（可能是「用户<QQ>」这类占位），不是内容，也不是身份声明；
- 只有冒号之后的才是对方说的话，把昵称当「谁发的」标识看，不要拿昵称当对话内容回复；
- 行尾括号里是附注（提到了谁 / 引用了哪句 / 时间），都是元信息。
如果记录和长期记忆里都没有对方的名字，不要用「用户<QQ>」这类代号称呼对方，可以问「怎么称呼你」或避开称呼。"""


def emoji_hint_line(tags: str) -> str:
    """【可用表情】一行提示：把可贴标签摆到 prompt 里，省得模型去翻工具文档。

    只在贴表情工具本轮可用、且活跃度 > 0 时注入（见 ``assembly.build_emoji_hint``）。
    标签表是静态的，所以这段在会话内逐字稳定——放在 system 侧不破坏前缀缓存。
    """
    text = str(tags or "").strip()
    if not text:
        return ""
    return (
        f"【可用表情】可贴的表情标签：{text}。"
        "贴表情时直接写标签名（如 赞 / 笑哭 / 吃瓜 / 狗头），框架会翻成正确的表情 id；"
        "若上下文里出现过 [♡66] 这类数字，那是别的消息上已贴过的表情 id，可原样复用。"
    )


def build_messages(
    *,
    system_prompt: str,
    pre_history_text: str = "",
    history: list[dict] | None = None,
    user_text: str,
    with_schedule_instruction: bool = True,
    skills: list[str] | None = None,
    memory_text: str = "",
    message_meta_instruction: str | None = None,
    proactive_instruction: str | None = None,
    user_content: list[dict] | None = None,
) -> list[dict]:
    """组装 LLM 消息列表。

    Args:
        system_prompt: 系统提示词
        pre_history_text: 前置历史文本，为空则跳过
        history: 会话历史消息（[{role, content}, ...]）
        user_text: 当前用户消息
        with_schedule_instruction: 是否追加「定时任务协议」指令
        skills: 模块技能 prompt 块列表（逐个追加为 system 消息）
        memory_text: 长期记忆文本块，为空则跳过（默认空 = 旧调用方零影响）
        message_meta_instruction: “发送者/正文”消歧说明文本；传入非空字符串时追加为 system 消息
        proactive_instruction: 「主动性」协议块（见 build_proactive_instruction）；
            仍是一块 system，为空则完全不注入
        user_content: 本轮 user 消息的多模态内容块（OpenAI 风格）。
            传入时替换纯文本 content，仅用于「本轮触发消息带图 + 模型支持图片」的场景
    """
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    if with_schedule_instruction:
        messages.append({"role": "system", "content": SCHEDULE_INSTRUCTION})

    if proactive_instruction:
        messages.append({"role": "system", "content": proactive_instruction})

    if message_meta_instruction:
        messages.append({"role": "system", "content": message_meta_instruction})

    for block in skills or []:
        messages.append({"role": "system", "content": block})

    if memory_text:
        messages.append({"role": "system", "content": memory_text})

    if pre_history_text:
        messages.append({"role": "system", "content": pre_history_text})

    if history:
        messages.extend(history)

    messages.append({"role": "user", "content": user_content or user_text})
    return messages
