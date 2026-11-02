"""能力提示词段归插件后的契约测试。

锁三件事：

1. `llm_trigger` 注册的 `schedule` / `proactive` 是 **L1** 段，且卸载后消失；
2. `llm_trigger` 的主动消息段在没有相关工具时不出现；
3. `llm_tools_extra` 的表情提示只在贴表情工具本轮可用、且活跃度 > 0 时产出。

这是「框架只剩三套系统提示词、能力文案随模块生灭」的回归闸门：模块禁用时
提示词里不该再出现"你可以建定时任务 / 这就是格式"这类悬空说明。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.assembly.layers import LAYER_SYSTEM_PROMPT, layer_of
from app.agent.assembly.sections import resolve_sections


def _runtime(config: dict | None = None):
    return SimpleNamespace(config=config or {}, bot_id="1")


def test_trigger_registers_schedule_and_proactive_as_system_prompt():
    from app.plugins.llm_trigger.src.wiring import (
        register_prompt_sections,
        unregister_prompt_sections,
    )

    runtime = _runtime({"prompt_preset": "chat"})
    register_prompt_sections(runtime)
    sections = {s.name: s for s in resolve_sections(runtime)}
    assert {"schedule", "proactive"} <= set(sections)
    assert layer_of(sections["schedule"]) == LAYER_SYSTEM_PROMPT
    assert layer_of(sections["proactive"]) == LAYER_SYSTEM_PROMPT

    unregister_prompt_sections(runtime)
    names = {s.name for s in resolve_sections(runtime)}
    assert not {"schedule", "proactive"} & names, "卸载后能力段必须消失"


def test_proactive_section_disappears_when_capability_missing():
    from app.plugins.llm_trigger.src.prompt import proactive_section

    req = SimpleNamespace(
        config={"proactive_prompt_enable": True},
        intent_text="",
        available_tools=set(),  # 本轮没有任何相关工具
        schedule_enable=False,
    )
    assert proactive_section(req) == []


def test_emoji_hint_requires_tool():
    from app.plugins.llm_tools_extra.src.prompt import emoji_hint_section

    req_no_tool = SimpleNamespace(
        config={"emoji_react_level": 2}, available_tools={"send_message"}, runtime=None
    )
    assert emoji_hint_section(req_no_tool) == []

    # 活跃度开关已删除（2026-11-01 精简）：配置里残留旧键也不影响，工具在就有提示
    req_with_tool = SimpleNamespace(
        config={},
        available_tools={"set_msg_emoji_like"},
        runtime=None,
    )
    messages = emoji_hint_section(req_with_tool)
    assert len(messages) == 1 and "【可用表情】" in messages[0]["content"]
