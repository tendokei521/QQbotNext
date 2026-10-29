"""知识库模块（原 `app/agent/knowledge`，v3 搬进本模块）。"""

from __future__ import annotations

from .src.tool import build_knowledge_tools

__all__ = ["build_knowledge_tools"]
