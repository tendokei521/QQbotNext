"""框架级 LLM Agent 层（对齐 AstrBot 的 agent/tool/scheduler 架构）。

原 llm_chat_v2 的能力上提至此：Agent 循环 + Tool 注册表 + 会话/历史 +
定时任务(CronManager) + 主动消息 + 状态标签协议。

## 本文件为什么这么薄（重要，别把 `from .chat import handle` 加回顶层）

这里曾经在末尾急切 import 并重导出 `chat` / `context` / `hooks` / `skills` / `tool`。
后果：全库 **121 处** `from app.llm import logger / llm_data_dir / safe_bot_id`
（共 73 个文件）全部被拖去加载 `chat.py`，实测把**每个入口**的可达闭包放大到
152 文件 / 46,243 行——「取一个日志器」需要加载整个 Agent 主循环。

现在按代价分成两类：

- **廉价工具**（`logger` 与路径函数）：直接 import；
- **廉价的公开 API**（`ToolContext` / `tool` / `skill` / `ToolCallContext` /
  `LlmContext` / `LlmJob`）：仍然急切重导出。它们分别只依赖 `hooks` / `telemetry` /
  `tool_loop` / `domain`，合计约 1,150 行且无重依赖——必须急切，因为
  `from app.llm import tool` 是插件作者的标准写法，而 `tool` 与子模块
  `app.llm.tool` **同名**，若走 `__getattr__` 会被子模块属性抢先命中，静默拿到模块对象。

## `handle` 为什么不再从包里重导出

`handle` 是唯一的重符号（`app/llm/chat.py`，2,192 行 + 整条依赖链）。曾尝试用
PEP 562 `__getattr__` 惰性提供，但那是**假解决**：`__getattr__` 里的
`from .chat import handle` 在 import 图上仍是一条 `app.llm → app.llm.chat` 的边，
而 `chat.py` 里有 `from app.llm import logger`——两跳成环。于是所有 import
`app.llm` 的模块又通过 `chat` 被兜回环内（实测 27 → 63 个模块）。

正确做法是**让包 `__init__` 不成为通往主循环的后门**：需要 `handle` 的地方
直接 `from app.llm.chat import handle`（具名、可 grep、依赖图上诚实）。
路径工具已下沉到 `app.infrastructure.fs.paths`（见 `docs/ARCHITECTURE.md §3.4`），
这里只是为兼容保留重导出。
"""

from __future__ import annotations

from app.core.logger import get_logger
from app.infrastructure.fs.paths import (
    bot_data_dir,
    llm_data_dir,
    migrate_legacy_data,
    safe_bot_id,
)

# 廉价的公开 API：必须急切，否则 `tool` 会被同名的 `app.llm.tool` 子模块遮蔽
from .context import LlmContext, LlmJob  # noqa: E402
from .hooks import ToolCallContext  # noqa: E402
from .skills import skill  # noqa: E402
from .tool import ToolContext, tool  # noqa: E402

logger = get_logger("LLM")

__all__ = [
    "LlmContext",
    "LlmJob",
    "ToolContext",
    "ToolCallContext",
    "tool",
    "skill",
    "logger",
    "llm_data_dir",
    "safe_bot_id",
    "bot_data_dir",
    "migrate_legacy_data",
]
