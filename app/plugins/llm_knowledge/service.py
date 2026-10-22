"""llm_knowledge 的服务层：知识库作为**业务**接到基础接口上。

模块不接管任何东西：
- `tool_sources` —— 注册 `knowledge_*` 检索/写入工具；
- 检索结果由工具回传给模型（知识库不需要每轮注入上下文块）。
"""

from __future__ import annotations

from typing import Any


def _manager(module: Any):
    mgr = getattr(module, "_manager", None)
    if mgr is None:
        from app.plugins.llm_knowledge.manager import KnowledgeManager

        runtime = module._runtime()
        mgr = KnowledgeManager(runtime) if runtime is not None else None
        module._manager = mgr
    return mgr


class KnowledgeToolSource:
    """工具源：知识库检索/写入工具。"""

    def __init__(self, module: Any) -> None:
        self.module = module
        self.name = "knowledge_tools"
        self.order = 35
        self.owner = module

    async def collect(self, scope: Any) -> list:
        mgr = _manager(self.module)
        if mgr is None or not mgr.enabled():
            return []
        from app.plugins.llm_knowledge.tool import build_knowledge_tools

        runtime = getattr(scope, "runtime", None)
        return list(build_knowledge_tools(runtime) or [])
