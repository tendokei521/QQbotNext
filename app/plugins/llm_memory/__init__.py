"""长期记忆模块（原 `app/agent/memory`，v3 搬进本模块）。

对外导出两个构造器，供 `service.py` 与 `#llm memory` 指令使用：
- `build_memory_tools(...)`：记忆类原生工具
- `handle_memory_command(...)`：`#llm memory ...` 指令入口
"""

from __future__ import annotations

from app.plugins.llm_memory.commands import handle_memory_command
from app.plugins.llm_memory.tool import build_memory_tools

__all__ = ["build_memory_tools", "handle_memory_command"]
