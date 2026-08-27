"""知识库（Knowledge）模块：向量化文档存储 + 语义检索 + LLM 工具。

- 存储：SQLite + 向量 BLOB（余弦相似度在内存计算，避免额外向量数据库依赖）；
- 向量化：使用 Provider 预设中能力类型为 embedding 的模型实例；
- 工具：knowledge_add / knowledge_search / knowledge_delete；
- 隔离：每个 AgentRuntime 独立知识库文件。
"""

from __future__ import annotations

from app.llm.knowledge.manager import KnowledgeManager
from app.llm.knowledge.store import KnowledgeStore
from app.llm.knowledge.tool import build_knowledge_tools

__all__ = [
    "KnowledgeManager",
    "KnowledgeStore",
    "build_knowledge_tools",
]
