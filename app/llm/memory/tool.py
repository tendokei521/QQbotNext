"""记忆原生工具（P2）：memory_save / memory_recall / memory_delete。

注册方式与 ``scheduler.build_schedule_tool`` 一致——在 ``chat._collect_llm_ext``
按会话追加，复用既有 function-calling + 工具循环。
"""

from __future__ import annotations

from typing import Any

from app.llm.memory.store import owner_group, owner_private
from app.llm.tool import ToolSpec


def _group_id(session_id: str) -> str:
    if str(session_id or "").startswith("group_"):
        return str(session_id)[len("group_"):]
    return ""


async def _handle_save(runtime, session_id, user_id, args) -> str:
    memory = getattr(runtime, "memory", None)
    if memory is None:
        return "error: 记忆服务不可用"
    content = str(args.get("content") or "").strip()
    if not content:
        return "error: content 不能为空"
    scope = str(args.get("scope") or "session").strip().lower()
    try:
        importance = float(args.get("importance") or 0.5)
        importance = max(0.0, min(1.0, importance))
    except (TypeError, ValueError):
        importance = 0.5

    if scope == "group_public":
        gid = _group_id(session_id)
        if not gid:
            return "error: 群公共记忆仅群聊可用"
        owner = owner_group(gid)
    elif scope == "user":
        if not bool(memory._get("memory_user_cross_group", False)):
            return "error: 跨群记忆未开启（memory_user_cross_group=false）"
        owner = owner_private(user_id)
    else:
        owner = memory.own_owner(session_id, user_id)

    mid = memory.save_fact(
        content, owner, importance=importance,
        source="tool", source_user=str(user_id or ""),
    )
    if mid is None:
        return "error: 保存失败（记忆可能未启用）"
    return f"success: 已记住「{content}」"


async def _handle_recall(runtime, session_id, user_id, bot, args) -> str:
    memory = getattr(runtime, "memory", None)
    if memory is None:
        return "error: 记忆服务不可用"
    query = str(args.get("query") or "").strip()
    try:
        limit = max(1, min(20, int(args.get("limit") or 8)))
    except (TypeError, ValueError):
        limit = 8
    if hasattr(memory, "visible_recall_async"):
        block = await memory.visible_recall_async(
            session_id, user_id, query, bot=bot, limit=limit, audit=True
        )
    else:
        block = memory.visible_recall(session_id, user_id, query, limit=limit, audit=True)
    return block or "未找到相关记忆"


async def _handle_delete(runtime, session_id, user_id, args) -> str:
    memory = getattr(runtime, "memory", None)
    if memory is None:
        return "error: 记忆服务不可用"
    target = str(args.get("id") or args.get("query") or "").strip()
    if not target:
        return "error: 需要提供 id 或 query"
    n = memory.delete_own(session_id, user_id, target)
    if n == 0:
        return "error: 未找到可删除的记忆（只允许删除当前对话本人的记忆）"
    return f"success: 已删除 {n} 条记忆"


def build_memory_tools(
    runtime: Any, session_id: str, user_id: Any, is_private: bool
) -> list[ToolSpec]:
    """构造绑定到当前会话的三个记忆工具。"""

    async def _save(_ctx, args: dict) -> str:
        return await _handle_save(runtime, session_id, user_id, args)

    async def _recall(_ctx, args: dict) -> str:
        bot = getattr(_ctx, "bot", None) if _ctx is not None else None
        return await _handle_recall(runtime, session_id, user_id, bot, args)

    async def _delete(_ctx, args: dict) -> str:
        return await _handle_delete(runtime, session_id, user_id, args)

    return [
        ToolSpec(
            name="memory_save",
            description=(
                "把用户明确想让长期记住的信息保存下来（如个人偏好、名字、习惯、"
                "约定、重要事实）。当用户说类似“记住……”“我叫……”“我喜欢……”"
                "“我喜欢喝美式”“我住在……”时调用。scope: session=当前会话本人/群内本人，"
                "group_public=群公共约定，user=跨群用户画像（需配置开启）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "要记住的一句话事实（简洁完整）。",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["session", "group_public", "user"],
                        "description": "记忆归属范围（默认 session）。",
                    },
                    "importance": {
                        "type": "number",
                        "description": "重要度 0~1（可选，默认 0.5）。",
                    },
                },
                "required": ["content"],
            },
            handler=_save,
        ),
        ToolSpec(
            name="memory_recall",
            description=(
                "查询长期记忆，回答“我以前说过/你记得……吗/我/某人喜欢什么/什么时候……”"
                "等需要翻旧账的问题时调用。返回记忆命中列表。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要查询的关键词/问题（成员名/主题词）。",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回条数上限 1~20（默认 8）。",
                    },
                },
                "required": ["query"],
            },
            handler=_recall,
        ),
        ToolSpec(
            name="memory_delete",
            description=(
                "删除用户想忘掉的长期记忆。用户说“忘掉/删掉/别再记得……”时调用。"
                "只允许删除当前对话本人的记忆。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "记忆 id（来自 list/recall 结果）。"},
                    "query": {"type": "string", "description": "按关键词删除，如“美式”。"},
                },
            },
            handler=_delete,
        ),
    ]
