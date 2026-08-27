"""知识库 LLM 工具：knowledge_add / knowledge_search / knowledge_delete。"""

from __future__ import annotations

from typing import Any

from app.llm.tool import ToolSpec


def _kb(runtime: Any):
    return getattr(runtime, "knowledge", None)


async def _handle_add(runtime, args: dict) -> str:
    kb = _kb(runtime)
    if kb is None:
        return "error: 知识库服务不可用"
    if not kb.enabled():
        return "error: 知识库未启用（knowledge_enable=false）"
    content = str(args.get("content") or "").strip()
    title = str(args.get("title") or "").strip()
    if not content:
        return "error: content 不能为空"
    cid, msg = await kb.add_text(content, title=title, source="tool")
    if not cid:
        return f"error: {msg}"
    return "success: 已写入知识库" + (f" title={title}" if title else "")


async def _handle_search(runtime, args: dict) -> str:
    kb = _kb(runtime)
    if kb is None:
        return "error: 知识库服务不可用"
    if not kb.enabled():
        return "error: 知识库未启用（knowledge_enable=false）"
    query = str(args.get("query") or "").strip()
    try:
        limit = max(1, min(20, int(args.get("limit") or 5)))
    except (TypeError, ValueError):
        limit = 5
    if not query:
        return "error: query 不能为空"
    hits = await kb.search(query, limit=limit)
    if not hits:
        return "error: 未检索到相关内容（可能未配置 embedding 模型或知识库为空）"
    lines = []
    for hit in hits:
        score = float(hit.get("_score") or 0)
        title = hit.get("title") or ""
        content = str(hit.get("content") or "").strip()
        lines.append(f"[{title}] (score={score:.3f})\n{content}")
    return "\n\n".join(lines)


async def _handle_delete(runtime, args: dict) -> str:
    kb = _kb(runtime)
    if kb is None:
        return "error: 知识库服务不可用"
    cid = str(args.get("id") or "").strip()
    if not cid:
        return "error: id 不能为空"
    ok = kb.delete(cid)
    return "success: 已删除" if ok else "error: 未找到该知识片段"


def build_knowledge_tools(runtime: Any) -> list[ToolSpec]:
    """构造知识库工具。"""

    async def _add(_ctx, args: dict) -> str:
        return await _handle_add(runtime, args)

    async def _search(_ctx, args: dict) -> str:
        return await _handle_search(runtime, args)

    async def _delete(_ctx, args: dict) -> str:
        return await _handle_delete(runtime, args)

    return [
        ToolSpec(
            name="knowledge_add",
            description=(
                "把一段长期可复用的知识/资料/文档写入知识库，供以后检索。"
                "适合用户要求整理资料、你收到可复用信息、需要沉淀业务知识时调用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "要写入的完整文本内容。"},
                    "title": {"type": "string", "description": "标题/分类（可选）。"},
                },
                "required": ["content"],
            },
            handler=_add,
        ),
        ToolSpec(
            name="knowledge_search",
            description=(
                "在知识库中语义检索与问题相关的片段，适合回答已有资料、规范、文档、"
                "历史知识等问题。优先于泛泛而谈。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索问题或关键词。"},
                    "limit": {"type": "integer", "description": "返回条数上限 1~20（默认 5）。"},
                },
                "required": ["query"],
            },
            handler=_search,
        ),
        ToolSpec(
            name="knowledge_delete",
            description=(
                "按 id 删除一条知识库片段。id 来自 knowledge_search / knowledge_list 结果。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "知识片段 id。"},
                },
                "required": ["id"],
            },
            handler=_delete,
        ),
    ]
