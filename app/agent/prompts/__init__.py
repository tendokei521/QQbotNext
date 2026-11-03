"""框架提示词统一入口（2026-11-03）。

## 唯一位置

框架所有**提示词注入**的文案与渲染入口都在本包：先注入 system 的内置段、
发言协议、工具结果回应要求、群记录头尾、历史省略提示、重试 nudge、
压缩/摘要等内部任务 prompt。

非提示词的东西不搬进来：人格数据、群记录渲染、历史渲染等"运行时内容"
仍由各自的组装模块生成（它们只是把数据填进这里的模板/段）。

## 目录（按实际组装顺序）

1. `system.py` — **按组装顺序排列的全部静态文案**：L1 各段 → 历史 → user 尾 →
   工具结果尾 → 重试提醒 → 后台内部任务；
2. `presets.py` — L1 order 20 behavior 的两套方案段落（chat / roleplay）；
3. `sections.py` — 按组装顺序排列的默认段 builder（persona → behavior → … → user）；
4. `convention.py` — L1 order 60 消息格式约定段；
5. `participation.py` — L1 order 54 参与度/触发规则段。

模块自己的提示词（`llm_context` / `llm_trigger`）仍归模块所有，不搬进本包。
"""

from __future__ import annotations

from .convention import convention_section
from .participation import participation_section, reply_rules_section
from .system import (
    ACCOUNT_LINE,
    COMPRESS_SYSTEM_PROMPT,
    CONVENTION,
    DEFAULT_COMPRESS_PROMPT,
    DEFAULT_PARTICIPATION_LEVEL,
    DEFAULT_PERSONA_PROMPT,
    DEFAULT_REPLY_DIRECTIVE,
    EMOJI_HINT_TEMPLATE,
    GROUP_LINE_TEMPLATE,
    HISTORY_OMITTED_HINT,
    LEGACY_TRIGGER_TEXT,
    LEVEL_PROMPT,
    MANAGER_LINE,
    ONEBOT_AUTH_TEXT,
    OWNER_LINE,
    REASONING_OVERFLOW_NUDGE,
    SILENCE_RULE,
    SPEECH_PROTOCOL,
    SUMMARY_HEADER,
    emoji_hint_line,
)

__all__ = [
    "ACCOUNT_LINE",
    "COMPRESS_SYSTEM_PROMPT",
    "CONVENTION",
    "DEFAULT_COMPRESS_PROMPT",
    "DEFAULT_PARTICIPATION_LEVEL",
    "DEFAULT_PERSONA_PROMPT",
    "DEFAULT_REPLY_DIRECTIVE",
    "EMOJI_HINT_TEMPLATE",
    "GROUP_LINE_TEMPLATE",
    "HISTORY_OMITTED_HINT",
    "LEGACY_TRIGGER_TEXT",
    "LEVEL_PROMPT",
    "MANAGER_LINE",
    "ONEBOT_AUTH_TEXT",
    "OWNER_LINE",
    "REASONING_OVERFLOW_NUDGE",
    "SILENCE_RULE",
    "SPEECH_PROTOCOL",
    "SUMMARY_HEADER",
    "convention_section",
    "emoji_hint_line",
    "participation_section",
    "reply_rules_section",
]
