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
- 只有用户明确提出定时需求时才调用工具，其余情况不要调用"""

# 紧贴用户消息的系统提醒：抑制角色"口头答应"倾向，提高工具调用率
RECENT_SCHEDULE_NUDGE = (
    "【系统】如果用户刚刚提出了定时提醒/定时回复的请求（包含时间点），"
    "你必须调用 schedule_task 工具真正创建定时任务，绝不能只用文字答应。"
)


def build_messages(
    *,
    system_prompt: str,
    pre_history_text: str = "",
    history: list[dict] | None = None,
    user_text: str,
    with_schedule_instruction: bool = True,
    schedule_nudge: bool = False,
    skills: list[str] | None = None,
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
    """
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    if with_schedule_instruction:
        messages.append({"role": "system", "content": SCHEDULE_INSTRUCTION})

    for block in skills or []:
        messages.append({"role": "system", "content": block})

    if pre_history_text:
        messages.append({"role": "system", "content": pre_history_text})

    if history:
        messages.extend(history)

    if schedule_nudge:
        messages.append({"role": "system", "content": RECENT_SCHEDULE_NUDGE})

    messages.append({"role": "user", "content": user_text})
    return messages
