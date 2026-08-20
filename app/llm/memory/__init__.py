"""长期记忆（Memory）模块：SQLite 存储 + 召回 + 工具 + 隐式蒸馏。

阶段：P0~P5 已完成。
- 存储：``MemoryStore``（SQLite，按 bot 隔离，owner 路由，audit）；
- 召回注入：``MemoryManager.recall_block(_async)``（含群聊提及扩展）；
- 原生工具：``build_memory_tools``（memory_save / memory_recall / memory_delete）；
- 确定性兜底：``detect.autosave_clause / wants_autosave``；
- 隐式蒸馏：``extract`` + ``MemoryManager.maybe_consolidate``（回复后限频 + 归档 force）；
- 管理命令：``commands.handle_memory_command``（#chat memory ...）。
"""

from __future__ import annotations

from app.llm.memory.commands import handle_memory_command
from app.llm.memory.manager import MemoryManager, scope_owners
from app.llm.memory.recall import rank, render_block
from app.llm.memory.store import MemoryStore, session_owner
from app.llm.memory.tool import build_memory_tools

__all__ = [
    "MemoryStore",
    "MemoryManager",
    "scope_owners",
    "session_owner",
    "rank",
    "render_block",
    "build_memory_tools",
    "handle_memory_command",
]
