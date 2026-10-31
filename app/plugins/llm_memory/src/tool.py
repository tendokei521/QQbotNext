"""记忆原生工具（v3）：memory_save / memory_recall / memory_delete / memory_correct / memory_deny。

注册方式不变：由模块的 tool source 在每轮按会话调用 :func:`build_memory_tools` 追加，
复用框架既有的 function-calling + 工具循环。契约见 ``docs/plan/memory-v3-rewrite.md`` §5.7：

- 5 个工具名固定（``app/agent/tools/policy.py`` 已登记，勿改名）；
- 高危/越权只回 ``error: ...`` 文本，绝不抛异常；
- manager 的同步方法（save_fact / delete_own / correct_own / deny_own）用
  ``asyncio.to_thread`` 调用，避免同步 SQLite 阻塞事件循环；异步方法（search_async）直接 await。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.agent.tools.spec import ToolSpec
from app.core.logger import logger

from .store import owner_group, owner_private

#: 工具结果自控上限（框架默认 2000 也会截，但先自己控住更干净）
_RESULT_MAX = 2000


def _resolve_memory(runtime: Any) -> Any:
    """取记忆管理器：优先 ``runtime.memory_service``（v3），回退 ``runtime.memory``（测试替身）。"""
    if runtime is None:
        return None
    service = getattr(runtime, "memory_service", None)
    if service is not None:
        return service
    return getattr(runtime, "memory", None)


def _config_value(memory: Any, runtime: Any, key: str, default: Any = None) -> Any:
    """读记忆策略配置：优先 manager 的 ``_get``，其次 ``runtime.memory_options``。"""
    getter = getattr(memory, "_get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except Exception as e:  # noqa: BLE001 —— 配置读失败用默认值即可，但要留痕
            logger.debug(f"[记忆] 读取配置 {key} 失败（使用默认值）: {e}")
    options = getattr(runtime, "memory_options", None) if runtime is not None else None
    if options is not None:
        try:
            value = options.get(key, default)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[记忆] 读取 memory_options.{key} 失败（使用默认值）: {e}")
        else:
            if value is not None:
                return value
    return default


def _enabled(memory: Any) -> bool:
    """记忆总开关（没有 ``enabled()`` 的测试替身按启用处理）。"""
    checker = getattr(memory, "enabled", None)
    if not callable(checker):
        return True
    try:
        return bool(checker())
    except Exception as e:  # noqa: BLE001 —— 读开关失败不阻断工具调用，但要留痕
        logger.debug(f"[记忆] 读取 enabled() 失败（按启用处理）: {e}")
        return True


def _scene_enabled(memory: Any, session_id: str) -> bool:
    """当前会话场景是否启用记忆（没有该方法的替身按启用处理）。"""
    checker = getattr(memory, "scene_enabled", None)
    if not callable(checker):
        return True
    try:
        return bool(checker(session_id))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[记忆] 读取 scene_enabled() 失败（按启用处理）: {e}")
        return True


def _group_id(session_id: str) -> str:
    """从 ``group_<gid>`` 会话 id 里取群号；非群聊返回空串。"""
    text = str(session_id or "")
    if text.startswith("group_"):
        return text[len("group_"):]
    return ""


def _importance(raw: Any) -> float:
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.5


def _clip(text: str, limit: int = _RESULT_MAX) -> str:
    """把结果控制在上限内（超长时保留主语并加省略号）。"""
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


async def _handle_save(
    runtime: Any,
    session_id: str,
    user_id: Any,
    is_private: bool,
    ctx: Any,
    args: dict,
) -> str:
    """写入一条记忆（session=本人层 / group_public=群公共 / user=跨群画像）。"""
    memory = _resolve_memory(runtime)
    if memory is None:
        return "error: 记忆服务不可用"
    content = str((args or {}).get("content") or "").strip()
    if not content:
        return "error: content 不能为空"
    if not _enabled(memory):
        return "error: 长期记忆未启用（memory_enable=false）"
    if not _scene_enabled(memory, session_id):
        return "error: 当前场景未开启长期记忆"

    scope = str((args or {}).get("scope") or "session").strip().lower()
    if scope not in ("session", "group_public", "user"):
        return "error: scope 仅支持 session / group_public / user"
    importance = _importance((args or {}).get("importance"))

    if scope == "group_public":
        group_id = _group_id(session_id)
        if not group_id:
            return "error: 群公共记忆仅群聊可用"
        event = getattr(ctx, "event", None) if ctx is not None else None
        # 主动消息/定时任务路径没有事件主语 → 放行；有事件时必须群管理员
        if event is not None and not bool(getattr(event, "is_admin", False)):
            return "error: 仅群管理员可写群公共记忆"
        owner = owner_group(group_id)
    elif scope == "user":
        if not bool(_config_value(memory, runtime, "memory_user_cross_group", False)):
            return "error: 跨群记忆未开启（memory_user_cross_group=false）"
        if not str(user_id or "").strip():
            return "error: 无法确定用户身份"
        owner = owner_private(user_id)
    else:
        owner = memory.own_owner(session_id, user_id)

    mid = await asyncio.to_thread(
        memory.save_fact,
        content,
        owner,
        importance=importance,
        source="tool",
        source_user=str(user_id or ""),
    )
    if mid is None:
        return "error: 保存失败（记忆可能未启用）"
    return _clip(f"success: 已记住「{content}」")


async def _handle_recall(
    runtime: Any,
    session_id: str,
    user_id: Any,
    is_private: bool,
    args: dict,
) -> str:
    """检索长期记忆：交给 manager.search_async（内部已做提及扩展与渲染）。"""
    memory = _resolve_memory(runtime)
    if memory is None:
        return "error: 记忆服务不可用"
    query = str((args or {}).get("query") or "").strip()
    if not query:
        return "error: query 不能为空"
    try:
        limit = max(1, min(20, int((args or {}).get("limit") or 8)))
    except (TypeError, ValueError):
        limit = 8
    block = await memory.search_async(session_id, user_id, query, limit=limit)
    text = str(block or "").strip()
    if not text or text.startswith("未找到"):
        return "未找到相关记忆"
    return _clip(text)


async def _handle_delete(
    runtime: Any,
    session_id: str,
    user_id: Any,
    is_private: bool,
    args: dict,
) -> str:
    """删除本人层记忆（id 或关键词二选一）。"""
    memory = _resolve_memory(runtime)
    if memory is None:
        return "error: 记忆服务不可用"
    target = str((args or {}).get("id") or (args or {}).get("query") or "").strip()
    if not target:
        return "error: 需要提供 id 或 query"
    count = await asyncio.to_thread(memory.delete_own, session_id, user_id, target)
    count = int(count or 0)
    if count <= 0:
        return "error: 未找到可删除的记忆（只允许删除当前对话本人的记忆）"
    return f"success: 已删除 {count} 条记忆"


async def _handle_correct(
    runtime: Any,
    session_id: str,
    user_id: Any,
    is_private: bool,
    args: dict,
) -> str:
    """纠正一条记忆：旧说法下架 + 新说法写入（仅本人层）。"""
    memory = _resolve_memory(runtime)
    if memory is None:
        return "error: 记忆服务不可用"
    old = str((args or {}).get("old") or "").strip()
    new_content = str((args or {}).get("content") or (args or {}).get("new") or "").strip()
    if not old or not new_content:
        return "error: 需要 old（旧说法）与 content（新说法）"
    mid = await asyncio.to_thread(
        memory.correct_own, session_id, user_id, old, new_content
    )
    if mid:
        return "success: 已纠正——旧说法下架，新说法已记住"
    return "error: 纠正失败（新内容为空，或没有匹配的旧记忆）"


async def _handle_deny(
    runtime: Any,
    session_id: str,
    user_id: Any,
    is_private: bool,
    args: dict,
) -> str:
    """否认一条记忆：下架（negative，不删除，可恢复），仅本人层。"""
    memory = _resolve_memory(runtime)
    if memory is None:
        return "error: 记忆服务不可用"
    target = str((args or {}).get("id") or (args or {}).get("query") or "").strip()
    if not target:
        return "error: 需要提供 id 或 query"
    count = await asyncio.to_thread(memory.deny_own, session_id, user_id, target)
    count = int(count or 0)
    if count <= 0:
        return "error: 未找到可下架的记忆（只允许下架当前对话本人的记忆）"
    return f"success: 已下架 {count} 条相关记忆（之后不再引用）"


def build_memory_tools(
    runtime: Any, session_id: str, user_id: Any, is_private: bool
) -> list[ToolSpec]:
    """构造绑定到当前会话的 5 个记忆工具。"""
    uid = str(user_id or "")

    async def _save(ctx: Any, args: dict) -> str:
        return await _handle_save(runtime, session_id, uid, is_private, ctx, args)

    async def _recall(ctx: Any, args: dict) -> str:
        return await _handle_recall(runtime, session_id, uid, is_private, args)

    async def _delete(ctx: Any, args: dict) -> str:
        return await _handle_delete(runtime, session_id, uid, is_private, args)

    async def _correct(ctx: Any, args: dict) -> str:
        return await _handle_correct(runtime, session_id, uid, is_private, args)

    async def _deny(ctx: Any, args: dict) -> str:
        return await _handle_deny(runtime, session_id, uid, is_private, args)

    return [
        ToolSpec(
            name="memory_save",
            description=(
                "把用户明确想让长期记住的信息保存下来（个人偏好、名字、习惯、约定、重要事实）。"
                "当用户说“记住……”“我叫……”“我喜欢……”时调用。scope: session=当前会话本人，"
                "group_public=群公共约定（仅群管理员），user=跨群用户画像（需配置开启）。"
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
            source="system",
            category="长期记忆",
            concurrency="exclusive",
        ),
        ToolSpec(
            name="memory_recall",
            description=(
                "查询长期记忆。回答“我以前说过/你记得……吗/某人喜欢什么”等需要翻旧账的问题时调用。"
                "返回记忆命中列表。"
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
            source="system",
            category="长期记忆",
            concurrency="exclusive",
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
            source="system",
            category="长期记忆",
            concurrency="exclusive",
        ),
        ToolSpec(
            name="memory_correct",
            description=(
                "纠正一条记忆：用户说“你记错了/其实是……/不是那样，是……”时调用。"
                "旧说法下架（不再引用），新说法写入。只作用于当前对话本人的记忆。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "old": {"type": "string", "description": "被纠正的旧说法关键词/原内容片段。"},
                    "content": {"type": "string", "description": "纠正后的新说法（一句话）。"},
                },
                "required": ["old", "content"],
            },
            handler=_correct,
            source="system",
            category="长期记忆",
            concurrency="exclusive",
        ),
        ToolSpec(
            name="memory_deny",
            description=(
                "用户否认某条记忆（“我没说过/不是真的/以后别提这个”）时调用："
                "把相关记忆下架（不再注入）。只作用于当前对话本人的记忆，可恢复。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "记忆 id。"},
                    "query": {"type": "string", "description": "按关键词下架，如“美式”。"},
                },
            },
            handler=_deny,
            source="system",
            category="长期记忆",
            concurrency="exclusive",
        ),
    ]
