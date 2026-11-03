"""框架提示词统一入口（2026-11-03）。

## 唯一位置

框架所有**提示词注入**的文案与渲染入口都在本包：先注入 system 的内置段、
发言协议、工具结果回应要求、群记录头尾、历史省略提示、重试 nudge、
压缩/摘要等内部任务 prompt。

非提示词的东西不搬进来：人格数据、群记录渲染、历史渲染等"运行时内容"
仍由各自的组装模块生成（它们只是把数据填进这里的模板/段）。

## 目录

| 文件 | 内容 |
|---|---|
| `presets.py` | 两套提示词方案（chat / roleplay）的段落文案与渲染 |
| `sections.py` | 框架默认 system 段（persona/behavior/emoji/rules/…）及其文案 |
| `convention.py` | 消息格式约定段 |
| `participation.py` | 参与度/触发规则的提示词段 |
| `emoji.py` | 可用表情提示 |
| `onebot.py` | OneBot 敏感工具的消息凭证约束 |
| `identity.py` | 身份块文案模板（账号/拥有者/管理员/当前群聊） |
| `persona.py` | 默认人格兜底文案 |
| `speech.py` | 发言协议（贴在最后一条 user 消息尾部） |
| `tool_reply.py` | 工具结果回应要求（贴在工具结果尾部） |
| `group_log.py` | 群聊环境记录的头尾声明 |
| `history.py` | 历史省略提示 / 压缩摘要头 |
| `nudges.py` | 重试类 user 提醒 |
| `internal.py` | 后台内部 LLM 任务 prompt（历史压缩） |

模块自己的提示词（`llm_context` / `llm_trigger`）仍归模块所有，不搬进本包。
"""

from __future__ import annotations

from .convention import CONVENTION, convention_section
from .emoji import EMOJI_HINT_TEMPLATE, emoji_hint_line
from .group_log import FOOTER as GROUP_LOG_FOOTER
from .group_log import HEADER as GROUP_LOG_HEADER
from .history import HISTORY_OMITTED_HINT, SUMMARY_HEADER
from .identity import (
    ACCOUNT_LINE,
    GROUP_LINE_TEMPLATE,
    MANAGER_LINE,
    OWNER_LINE,
)
from .internal import COMPRESS_SYSTEM_PROMPT, DEFAULT_COMPRESS_PROMPT
from .nudges import REASONING_OVERFLOW_NUDGE
from .onebot import ONEBOT_AUTH_TEXT
from .participation import (
    LEGACY_TRIGGER_TEXT,
    LEVEL_PROMPT,
    SILENCE_RULE,
    participation_section,
    reply_rules_section,
)
from .persona import DEFAULT_PERSONA_PROMPT
from .speech import SPEECH_PROTOCOL
from .tool_reply import DEFAULT_REPLY_DIRECTIVE

__all__ = [
    "ACCOUNT_LINE",
    "COMPRESS_SYSTEM_PROMPT",
    "CONVENTION",
    "DEFAULT_COMPRESS_PROMPT",
    "DEFAULT_PERSONA_PROMPT",
    "DEFAULT_REPLY_DIRECTIVE",
    "EMOJI_HINT_TEMPLATE",
    "GROUP_LINE_TEMPLATE",
    "GROUP_LOG_FOOTER",
    "GROUP_LOG_HEADER",
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
