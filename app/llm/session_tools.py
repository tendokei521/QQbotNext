"""系统级会话上下文工具（不进入 NapCat 前端清单）。

设计目标：
- 只负责“当前会话/系统视角”的基础信息，不跟 NapCat Tool 清单耦合；
- 所有工具自动从 ``ToolContext`` 推导当前会话、机器人、用户、群号，不需要模型先知道 id；
- 读取成本低：优先使用本地会话/事件上下文，不额外请求远端接口。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.llm.group_context import fetch_group_name, format_history_for_llm
from app.llm.session import SessionManager
from app.llm.tool import ToolSpec


def _is_private(session_id: str | None) -> bool:
    return str(session_id or "").startswith("private_")


def _parse_limit(args: dict, default: int = 20, maximum: int = 100) -> int:
    try:
        limit = int(args.get("limit") or default)
    except (TypeError, ValueError):
        limit = default
    return max(1, min(limit, maximum))


async def _handle_current_session(ctx, args: dict) -> str:
    """返回当前会话的基础身份信息。"""
    if ctx is None:
        return "error: 当前无会话上下文"

    runtime = getattr(ctx, "runtime", None)
    bot = getattr(ctx, "bot", None)
    event = getattr(ctx, "event", None)
    session_id = str(getattr(ctx, "session_id", "") or "")
    if not session_id:
        return "error: 当前无会话 ID"

    is_private = _is_private(session_id)
    lines: list[str] = []
    lines.append(f"会话类型：{'私聊' if is_private else '群聊'}")
    lines.append(f"会话ID：{session_id}")

    if runtime is not None:
        lines.append(f"Bot ID：{getattr(runtime, 'bot_id', '?')}")

    # 当前用户 / 触发者
    user = getattr(event, "user", None) if event is not None else None
    user_id = (
        getattr(ctx, "user_id", None)
        or (getattr(event, "user_id", None) if event is not None else None)
        or (getattr(user, "user_id", None) if user is not None else None)
    )
    if user_id not in (None, ""):
        lines.append(f"当前用户 QQ：{user_id}")
    nickname = getattr(user, "nickname", "") or ""
    card = getattr(user, "card", "") or ""
    if card:
        lines.append(f"当前用户群名片：{card}")
    elif nickname:
        lines.append(f"当前用户昵称：{nickname}")

    role = (
        getattr(event, "permission_role", None)
        or getattr(event, "role", None)
    )
    if role:
        lines.append(f"当前用户角色：{role}")

    # 会话目标（群号 / 对方 QQ）
    target = str(session_id[len("private_"):]) if is_private else str(session_id[len("group_"):])
    if is_private:
        if target:
            lines.append(f"对方 QQ：{target}")
    else:
        group = getattr(event, "group", None) if event is not None else None
        group_id = (
            getattr(ctx, "group_id", None)
            or (getattr(group, "group_id", None) if group is not None else None)
            or target
        )
        group_name = ""
        if group is not None:
            group_name = getattr(group, "group_name", "") or ""
        if not group_name and group_id and bot is not None:
            group_name = await fetch_group_name(bot, group_id)
        if group_id not in (None, ""):
            lines.append(f"群号：{group_id}")
        if group_name:
            lines.append(f"群名：{group_name}")

    lines.append(f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return "\n".join(lines)


async def _handle_session_history(ctx, args: dict) -> str:
    """返回当前会话的本地聊天记录（含发送者信息）。"""
    if ctx is None:
        return "error: 当前无会话上下文"

    runtime = getattr(ctx, "runtime", None)
    session_id = str(getattr(ctx, "session_id", "") or "")
    if not session_id:
        return "error: 当前无会话 ID"

    bot_id = str(getattr(runtime, "bot_id", "?") or "?")
    limit = _parse_limit(args, default=20, maximum=100)
    manager = SessionManager(bot_id)
    history = manager.get_history(session_id, limit=limit)
    if not history:
        return "当前会话暂无本地聊天记录。"

    rendered = format_history_for_llm(
        history,
        is_private=_is_private(session_id),
        normalize_enhanced=False,
        mask_nickname=True,
    )
    lines: list[str] = []
    for item in rendered:
        role = str(item.get("role", "user"))
        content = str(item.get("content", "") or "")
        if role == "assistant":
            lines.append(content)
        else:
            lines.append(f"[{role}] {content}")
    return "最近会话记录：\n" + "\n".join(lines)


def build_session_tools(runtime: Any, ctx: Any) -> list[ToolSpec]:
    """构造绑定当前 ToolContext 的系统级会话工具。"""

    async def _current_session(_ctx, _args: dict) -> str:
        return await _handle_current_session(ctx, _args)

    async def _session_history(_ctx, _args: dict) -> str:
        return await _handle_session_history(ctx, _args)

    return [
        ToolSpec(
            name="get_current_session",
            description=(
                "获取当前会话的基础信息：会话类型（群聊/私聊）、会话 ID、Bot ID、"
                "当前用户 QQ/群名片/角色、群号/群名（群聊）、对方 QQ（私聊）、当前时间。"
                "当模型需要确认“我现在在哪个会话、群号是多少、对方是谁”时调用。"
            ),
            parameters={
                "type": "object",
                "properties": {},
            },
            handler=_current_session,
            permission="member",
            scopes=("*",),
        ),
        ToolSpec(
            name="get_session_history",
            description=(
                "获取当前会话最近的本地聊天记录，包含发送者昵称/QQ 与时间。"
                "当用户问“刚才我们聊了什么”“之前我说过什么”“把最近对话整理一下”时调用。"
                "limit 控制返回条数（1~100，默认 20）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "返回最近消息条数，1~100，默认 20。",
                    },
                },
            },
            handler=_session_history,
            permission="member",
            scopes=("*",),
        ),
    ]
