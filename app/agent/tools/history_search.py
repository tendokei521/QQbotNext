"""`search_history`：按关键词检索**本会话**更早的历史（Q2）。

## 为什么需要

`history_mode=recent` 之后，请求里只内联最近 N 条；更早的内容不能"凭记忆"——
必须有一个明确的取回入口，否则就是功能倒退。本工具就是那个入口：

- 只查**调用者自己的会话**（`ctx.session_id`），不跨会话、不跨群；
- 关键词按"连续片段包含"匹配（大小写不敏感），命中条数受 `limit` 限制；
- 返回带角色、时间与 `message_id` 的短列表，模型可以继续用 `expand_message` 展开。

数据源用内存历史（`SessionManager.get_history`），它已经是事件日志的投影，
与请求里看到的内容同源；没有索引的线性扫描对单会话几百条是够用的。
"""

from __future__ import annotations

import time
from typing import Any

from app.agent.tools.spec import ToolSpec

DEFAULT_LIMIT = 5
MAX_LIMIT = 20


def _session_manager(runtime: Any):
    from app.agent.session.manager import SessionManager

    manager = getattr(runtime, "session_mgr", None)
    if manager is not None:
        return manager
    return SessionManager(str(getattr(runtime, "bot_id", "?") or "?"))


def _stamp(raw: Any) -> str:
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return time.strftime("%m-%d %H:%M", time.localtime(value))


def search_history(
    runtime: Any,
    session_id: str,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> str:
    """检索本会话历史，返回给模型看的文本；找不到时明确说"没有"。"""
    text = str(query or "").strip()
    if not text:
        return "error: 缺少 query（要检索的关键词）"
    if not session_id:
        return "error: 当前没有会话上下文，无法检索历史"

    try:
        size = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    except (TypeError, ValueError):
        size = DEFAULT_LIMIT

    try:
        history = _session_manager(runtime).get_history(session_id, limit=1000)
    except Exception as e:  # noqa: BLE001 —— 读历史失败按"没有"处理并留痕
        return f"error: 读取会话历史失败: {e}"

    needle = text.lower()
    hits: list[dict] = []
    for entry in reversed(history or []):
        if not isinstance(entry, dict):
            continue
        content = entry.get("content")
        haystack = content if isinstance(content, str) else ""
        if needle not in haystack.lower():
            continue
        hits.append(entry)
        if len(hits) >= size:
            break

    if not hits:
        return f"（本会话历史里没有找到包含「{text}」的内容；可以换个关键词再试）"

    lines = [f"本会话历史中匹配「{text}」的最近 {len(hits)} 条："]
    for entry in reversed(hits):
        role = "我" if str(entry.get("role") or "") == "assistant" else str(
            entry.get("nickname") or "对方"
        )
        when = _stamp(entry.get("time"))
        mid = str(entry.get("message_id") or "")
        body = str(entry.get("content") or "")
        if len(body) > 300:
            body = body[:300] + "…"
        suffix = f"〔message_id={mid}〕" if mid else ""
        lines.append(f"- [{when}] {role}: {body}{suffix}")
    return "\n".join(lines)


def build_history_search_tool(runtime: Any, ctx: Any = None) -> ToolSpec:
    """构造 ``search_history``（系统工具，按需注入）。"""

    async def _handler(tool_ctx, args: dict) -> str:
        payload = args or {}
        return search_history(
            getattr(tool_ctx, "runtime", None) or runtime,
            str(getattr(tool_ctx, "session_id", "") or ""),
            str(payload.get("query") or ""),
            limit=payload.get("limit", DEFAULT_LIMIT) or DEFAULT_LIMIT,
        )

    return ToolSpec(
        name="search_history",
        description=(
            "在本会话较早的历史里按关键词检索（当前请求只内联最近若干条）。"
            "当用户提到很久以前说过的内容、而你看不到时用它；可配合 expand_message 展开细节。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要检索的关键词或短语"},
                "limit": {
                    "type": "integer",
                    "description": f"最多返回几条，默认 {DEFAULT_LIMIT}，上限 {MAX_LIMIT}",
                },
            },
            "required": ["query"],
        },
        handler=_handler,
        source="system",
        category="系统工具",
        scopes=("*",),
    )
