"""OneBot 工具（框架内置）：清单 → ToolSpec → 执行。

原「工具扩展」插件的 OneBot 部分（2026-11-03 并入框架）：

- ``manifest.json``：175 个 OneBot API 的目录数据（name/description/schema/权限）；
- ``manifest.py``：加载 JSON 并派生 ``sensitivity``（高危名单写在代码里）；
- ``security.py``：只读总开关 + 清单默认权限/作用域；
- ``executor.py``：构建 Toolspec、执行 OneBot action、表情/戳一戳的特殊处理；
- ``source.py``：注册到 ``runtime.tool_sources`` 的工具源。
"""

from __future__ import annotations

from .executor import build_onebot_tools, resolve_action
from .manifest import ONEBOT_TOOLS
from .security import DEFAULT_TOOLS_ENABLED, resolve_tool_policy, tools_enabled
from .source import OneBotToolSource

__all__ = [
    "DEFAULT_TOOLS_ENABLED",
    "ONEBOT_TOOLS",
    "OneBotToolSource",
    "build_onebot_tools",
    "resolve_action",
    "resolve_tool_policy",
    "tools_enabled",
]
