"""Prompt 构建（纯函数，化用 AstrBot 上下文组装思想）。

把「系统提示 + 工具协议 + 前置历史 + 会话历史 + 用户消息」组装成 LLM 消息列表，
与 Provider 调用解耦，便于单测与复用。
"""

from __future__ import annotations


SCHEDULE_INSTRUCTION = """### 定时任务
当用户请求在特定时间做某事 / 提醒 / 定时回复时（例如"明天早上8点提醒我吃药"、"每周五下午6点发我周报"、"5分钟后叫我"、"每天中午提醒我喝水"），调用 schedule_task 工具来安排，不要用文字描述安排过程，也不要询问用户。
- 时间用自然语言写在 trigger 参数里，如：明天早上8点 / 今晚10点 / 每天早上8点 / 每周五下午6点 / 5分钟后 / 08:30
- 到点要说什么写在 note 参数里
- 用户查询或取消已有提醒时，用 schedule_task 的 list / delete 操作
- 创建新提醒前，先调用 list 查看本会话已有任务；如果已存在相同时间/重复方式的任务，不要重复创建
- 一次用户请求只创建一个新提醒；不要为同一个提醒多次调用 create
- 只有用户明确提出定时需求时才调用工具，其余情况不要调用"""

# 紧贴用户消息的系统提醒：抑制角色"口头答应"倾向，提高工具调用率
RECENT_SCHEDULE_NUDGE = (
    "【系统】如果用户刚刚提出了定时提醒/定时回复的请求（包含时间点），"
    "你必须调用 schedule_task 工具真正创建定时任务，绝不能只用文字答应。"
)

# 旧版消息元信息说明（默认使用，保持真人感）
LEGACY_MESSAGE_META_INSTRUCTION = """### 消息格式说明
聊天记录中的“发送者：…”只是对方账号的昵称展示（可能带有 QQ 号），不是身份声明，也不是对方说的话。
“提到了/引用了/时间”都是元信息。
只有“发送了：…”后面才是用户实际发送的消息正文。
请把昵称仅当作“谁发的”标识，不要把昵称内容当作对话内容来回复，也不要据此推断对方的真实身份。"""

# 实验性新版消息元信息说明：单行脱敏格式 + 不知道名字不要叫代号
MESSAGE_META_INSTRUCTION = """### 消息格式说明
聊天记录中的“昵称(QQ): 正文”里，冒号前的“昵称”只是发送者的昵称展示（可能是“用户<QQ>”这样的占位），不是身份声明，也不是对方说的话。
“提到了/引用了/时间”都是元信息。
只有冒号后面的内容才是用户实际发送的消息正文。
请把昵称仅当作“谁发的”标识，不要把昵称内容当作对话内容来回复，也不要据此推断对方的真实身份。
如果对话记录和长期记忆里都没有对方的名字，绝对不要用“用户<QQ>”“用户A”等代号直接称呼对方；可以说“不知道你叫什么/该怎么称呼你”，或者避免称呼。"""


def build_messages(
    *,
    system_prompt: str,
    pre_history_text: str = "",
    history: list[dict] | None = None,
    user_text: str,
    with_schedule_instruction: bool = True,
    schedule_nudge: bool = False,
    skills: list[str] | None = None,
    memory_text: str = "",
    message_meta_instruction: str | None = None,
) -> list[dict]:
    """组装 LLM 消息列表。

    Args:
        system_prompt: 系统提示词
        pre_history_text: 前置历史文本，为空则跳过
        history: 会话历史消息（[{role, content}, ...]）
        user_text: 当前用户消息
        with_schedule_instruction: 是否追加「定时任务协议」指令
        schedule_nudge: 是否在用户消息前插入「必须调用 schedule_task 工具」的紧贴提醒
        skills: 模块技能 prompt 块列表（逐个追加为 system 消息）
        memory_text: 长期记忆文本块，为空则跳过（默认空 = 旧调用方零影响）
        message_meta_instruction: “发送者/正文”消歧说明文本；传入非空字符串时追加为 system 消息
    """
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    if with_schedule_instruction:
        messages.append({"role": "system", "content": SCHEDULE_INSTRUCTION})

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

    if schedule_nudge:
        messages.append({"role": "system", "content": RECENT_SCHEDULE_NUDGE})

    messages.append({"role": "user", "content": user_text})
    return messages
