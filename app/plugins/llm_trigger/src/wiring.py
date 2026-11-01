"""本模块的提示词段装配（`main.py` 只做编排，注册细节放这里）。

两段都是 L1 系统提示词：定时任务协议（order 40）与主动性引导（order 50）。
模块禁用/卸载时成对注销 → 整段从 prompt 里消失，不会出现"提示词说有能力、
实际调度器已不在"的悬空说明。
"""

from __future__ import annotations

from typing import Any

#: 本模块注册的段名（注销时按名撤）
SECTION_NAMES: tuple[str, ...] = ("schedule", "proactive")


def register_prompt_sections(runtime: Any) -> None:
    from app.agent.assembly.layers import LAYER_SYSTEM_PROMPT
    from app.agent.assembly.sections import Section, register_section

    from .prompt import proactive_section, schedule_section

    register_section(
        runtime,
        Section(
            "schedule", 40, schedule_section,
            layer=LAYER_SYSTEM_PROMPT, source="llm_trigger",
        ),
    )
    register_section(
        runtime,
        Section(
            "proactive", 50, proactive_section,
            layer=LAYER_SYSTEM_PROMPT, source="llm_trigger",
        ),
    )


def unregister_prompt_sections(runtime: Any) -> None:
    from app.agent.assembly.sections import unregister_section

    for name in SECTION_NAMES:
        unregister_section(runtime, name)
