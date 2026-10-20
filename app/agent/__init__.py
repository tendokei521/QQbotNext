"""Agent 层：一次对话从「收到消息」到「发出回复」的全部内部实现。

## 本包负责

把「模型该看到什么、该被允许调用什么、回复怎么送出去」这套逻辑收在一个地方，
与 `app/modules/`（插件运行时）、`app/webui/`（HTTP 适配）、`app/core/`（基础设施）
解耦。对外只有本文件与各子包的公开符号，内部实现不承诺稳定。

## 子包职责（一眼看懂先看这张表）

| 子包 | 负责 | 不负责 |
|---|---|---|
| `loop/` | 一轮对话的编排：拿锁 → 装配 → 调模型 → 跑工具 → 发出去 | 具体怎么装配（那是 `assembly/`） |
| `assembly/` | 把原料（历史、记忆、群语境、图片）拼成 `messages` | 调模型、发消息 |
| `persona/` | 人格、技能、标签、表情词表（纯规则与数据） | 任何 IO |
| `context/` | 群语境、指代消解、焦点、昵称、显示名 | 拼 prompt（那是 `assembly/`） |
| `session/` | 会话与历史的存储/裁剪/富化 | 决定说什么 |
| `tools/` | 工具协议（`tool` 装饰器、`ToolContext`）与内置工具集 | 何时调工具（那是 `loop/`） |
| `delivery/` | 回复切分、限速、语音、发送池、指令解析 | 生成回复内容 |
| `media/` | 图片下载/存储/多模态编码/动图拆帧 | 判断该发哪张图 |
| `proactivity/` | 主动消息、定时任务、时间解析、触发判定 | 主对话循环 |
| `providers/` | 各家 LLM 协议适配与故障转移 | 业务语义 |
| `memory/` `knowledge/` `group_log/` | 三套独立数据能力（记忆 / 知识库 / 群聊记录） | 彼此不知道对方存在 |
| **本文件** | 稳定公开 API 与日志器 | 任何业务逻辑 |

## 为什么本文件这么薄（重要，别把 `from .loop.chat import handle` 加回来）

这里曾在末尾急切 import 并重导出 `chat` / `context` / `hooks` / `skills` / `tool`。
后果：全库 **121 处** `from app.llm import logger / llm_data_dir / safe_bot_id`
（共 73 个文件）全部被拖去加载 `chat.py`，实测把**每个入口**的可达闭包放大到
152 文件 / 46,243 行——「取一个日志器」需要加载整个 Agent 主循环。

现在按代价分成两类：

- **廉价工具**（`logger` 与路径函数）：直接 import；
- **廉价的公开 API**（`ToolContext` / `tool` / `skill` / `ToolCallContext` /
  `LlmContext` / `LlmJob`）：仍然急切重导出。它们合计约 1,150 行且无重依赖。
  必须急切，因为 `from app.agent import tool` 是插件作者的标准写法，而 `tool`
  与子模块 `app.agent.tools.spec` **同名**，若走 `__getattr__` 会被子模块属性
  抢先命中，静默拿到模块对象。

## `handle` 为什么不从包里重导出

`handle` 是唯一的重符号（`app/agent/loop/chat.py`，2,192 行 + 整条依赖链）。
曾尝试用 PEP 562 `__getattr__` 惰性提供，但那是**假解决**：`__getattr__` 里的
`from .loop.chat import handle` 在 import 图上仍是一条边，而 `chat.py` 里又有
`from app.agent import logger`——两跳成环，所有 import `app.agent` 的模块又被
兜回环内（实测 27 → 63 个模块）。

正确做法是**让包 `__init__` 不成为通往主循环的后门**：需要 `handle` 的地方直接
`from app.agent.loop.chat import handle`（具名、可 grep、依赖图上诚实）。

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

# 先定义 logger：下面急切 import 的模块里有 `from app.agent import logger`，
# 必须在它们加载前就绪，否则会拿到未定义属性。
logger = get_logger("LLM")

# 廉价的公开 API：必须急切（见上方说明）
from app.agent.hooks import ToolCallContext  # noqa: E402
from app.agent.loop.context import LlmContext, LlmJob  # noqa: E402
from app.agent.persona.skills import skill  # noqa: E402
from app.agent.tools.spec import ToolContext, tool  # noqa: E402

__all__ = [
    "LlmContext",
    "LlmJob",
    "ToolCallContext",
    "ToolContext",
    "bot_data_dir",
    "llm_data_dir",
    "logger",
    "migrate_legacy_data",
    "safe_bot_id",
    "skill",
    "tool",
]
