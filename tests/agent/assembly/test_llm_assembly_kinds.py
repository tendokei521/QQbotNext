"""四条装配路径共用同一张块表：只钉「关键块在场/不在场」的行为不变量。

历史：主动/定时路径曾经缺「消息格式说明」块、焦点行靠手工拼接、背景不合并。
这里保留两个回归断言（关键块出现 + 背景合并），不再逐块比对完整序列快照。
"""

from __future__ import annotations

from app.agent.assembly.assembler import PromptAssembler, PromptRequest, describe


def _rows(req: PromptRequest) -> list[dict]:
    return {row["block"]: row for row in describe(req)}


def test_framework_does_not_own_capability_prompt_text():
    """能力说明（消息格式/定时/主动性/表情）已归各插件，框架裸装配不再产出这些文案。

    以前这些段是 `default_sections()` 的一部分，模块禁用后文案还在（"提示词说有、
    能力却不在"）。现在它们由插件 `register_section`，卸载即消失。
    """
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
    messages = PromptAssembler().build(req)
    joined = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
    for capability_marker in ("### 定时任务", "### 消息格式说明", "### 记录格式", "【可用表情】"):
        assert capability_marker not in joined, f"框架不该再内置: {capability_marker}"
    rows = _rows(req)
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
    assert rows["user"]["messages"] == 1
    messages = PromptAssembler().build(req)
    user_content = [m.get("content") for m in messages if m.get("role") == "user"][-1]
    if isinstance(user_content, list):
        user_text = "\n".join(
            str(part.get("text") or "") if isinstance(part, dict) else str(part)
            for part in user_content
        )
    else:
        user_text = str(user_content or "")
    assert "### 当前聊天环境" in user_text
    assert rows["background"]["chars"] == len("### 当前对话焦点")
