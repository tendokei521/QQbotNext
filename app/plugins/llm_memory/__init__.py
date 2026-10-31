"""长期记忆模块（llm_memory，v3）。

对外导出：
- `build_memory_tools(...)`：记忆类原生工具
- `handle_memory_command(...)`：`/memory` / `#llm memory` 指令入口
- `memory_manager(...)`：模块内的记忆管理器（工具/命令/钩子共用）
"""

from __future__ import annotations

from .src.commands import handle_memory_command
from .src.service import memory_manager
from .src.tool import build_memory_tools

__all__ = ["build_memory_tools", "handle_memory_command", "memory_manager"]
