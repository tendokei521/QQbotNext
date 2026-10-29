"""四条装配路径共用同一张块表：只钉「关键块在场/不在场」的行为不变量。

历史：主动/定时路径曾经缺「消息格式说明」块、焦点行靠手工拼接、背景不合并。
这里保留两个回归断言（关键块出现 + 背景合并），不再逐块比对完整序列快照。
"""

from __future__ import annotations

from app.agent.assembly.assembler import PromptRequest, describe


def _rows(req: PromptRequest) -> list[dict]:
    return {row["block"]: row for row in describe(req)}


def test_proactive_prompt_has_message_meta_and_no_schedule_instruction():
    """主动消息必须带格式说明块（历史上缺过），且定时协议按开关关闭。"""
    req = PromptRequest(
        config={"system_prompt": "人设", "meta_sender_style": "legacy"},
        session_id="group_1",
        kind="proactive",
        user_text="（主动开口）",
        raw_user_text="（主动开口）",
        history=[{"role": "assistant", "content": "上次说了晚安"}],
        referent_text="### 当前对话焦点",
        skill_blocks=["技能块"],
        memory_text="记忆块",
        available_tools={"get_chat_history"},
        schedule_enable=False,
    )

    rows = _rows(req)
    assert rows["message_meta"]["messages"] == 1, "主动消息必须带消息格式说明块"
    assert rows["schedule"]["messages"] == 0, "schedule_enable=False 时不应注入定时协议"
    assert rows["user"]["messages"] == 1


def test_schedule_prompt_uses_task_text_and_merges_background():
    """定时任务的本轮 user 是任务模板内容；焦点与环境合并进同一块背景。"""
    from app.agent.assembly.assembler import PromptRequest

    req = PromptRequest(
        config={"system_prompt": "人设", "meta_sender_style": "legacy"},
        session_id="group_2",
        kind="schedule",
        user_text="【定时任务】到点提醒: 吃药",
        raw_user_text="【定时任务】到点提醒: 吃药",
        history=[],
        referent_text="### 当前对话焦点",
        pre_history_text="### 当前聊天环境",
        skill_blocks=[],
        memory_text="",
        available_tools=None,
        schedule_enable=False,
    )

    rows = _rows(req)
    assert rows["user"]["chars"] == len("【定时任务】到点提醒: 吃药")
    assert rows["background"]["messages"] == 1, "焦点行与环境行应合并成一块背景"
