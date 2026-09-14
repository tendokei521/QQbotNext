"""系统级会话上下文工具（不进入 NapCat 前端清单）。

设计目标：
- 只负责“当前会话/系统视角”的基础信息，不跟 NapCat Tool 清单耦合；
- 所有工具自动从 ``ToolContext`` 推导当前会话、机器人、用户、群号，不需要模型先知道 id；
- 读取成本低：优先本地会话记录，本地不足时按需补拉 QQ 历史。

历史工具演进：原 ``get_session_history`` 只读本地记录，模型在“本地记录不足”时便无从
下手（只能换工具猜或直接说“我不记得”），且与 NapCat 的两个 history 工具形成三选一。
现统一为 **``get_chat_history``（零参数单入口）**：

- 目标（群号/对方 QQ）一律从当前会话推导，模型无法借此读取其他会话的记录；
- ``scope=auto``（默认）先给本地记录，本地条数不足 ``history_auto_qq_min_local`` 时自动
  补拉 QQ 原始聊天记录；
- 取不到记录时给出明确路标（告诉模型下一步可以查 QQ），而不是一句“暂无记录”。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.llm.group_context import (
    fetch_group_name,
    fetch_group_online_history,
    fetch_private_online_history,
    format_history_for_llm,
)
from app.llm.session import SessionManager
from app.llm.tool import ToolSpec

DEFAULT_LIMIT = 30
DEFAULT_AUTO_QQ_MIN_LOCAL = 4
# scope 取值：auto=本地优先、不足补 QQ；local=仅本地；qq=强制 QQ
_SCOPES = ("auto", "local", "qq")


def _is_private(session_id: str | None) -> bool:
    return str(session_id or "").startswith("private_")


def _parse_limit(args: dict, default: int = 20, maximum: int = 100) -> int:
    try:
        limit = int(args.get("limit") or default)
    except (TypeError, ValueError):
        limit = default
    return max(1, min(limit, maximum))


def _cfg(ctx, key: str, default: Any):
    cfg = getattr(getattr(ctx, "runtime", None), "config", None)
    if cfg is not None and hasattr(cfg, "get"):
        try:
            return cfg.get(key, default)
        except Exception:
            pass
    return default


def _session_manager(runtime):
    """优先复用 runtime 上的会话管理器；否则按 bot_id 取单例（同一对象）。"""
    manager = getattr(runtime, "session_mgr", None)
    if manager is not None:
        return manager
    return SessionManager(str(getattr(runtime, "bot_id", "?") or "?"))


def _render_local_lines(runtime, session_id: str, is_private: bool, limit: int) -> list[str]:
    """本地会话记录 → 单行文本列表（不含头部）。"""
    try:
        history = _session_manager(runtime).get_history(session_id, limit=limit)
    except Exception:
        return []
    if not history:
        return []
    rendered = format_history_for_llm(
        history,
        is_private=is_private,
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
    return lines


def _bot_self_ids(bot, runtime) -> set[str]:
    ids = {
        str(getattr(bot, "bot_id", "") or ""),
        str(getattr(runtime, "bot_id", "") or ""),
        str(getattr(bot, "self_id", "") or ""),
    }
    ids.discard("")
    return ids


async def _fetch_qq_history(bot, runtime, group_id: str, user_id: str, limit: int) -> str:
    """补拉当前会话对象的 QQ 原始聊天记录；失败返回空串。"""
    if bot is None:
        return ""
    self_ids = _bot_self_ids(bot, runtime)
    try:
        if group_id:
            return await fetch_group_online_history(
                bot, group_id, count=limit, self_ids=self_ids, mask_nickname=True
            )
        if user_id:
            return await fetch_private_online_history(
                bot, user_id, count=limit, self_ids=self_ids
            )
    except Exception:
        return ""
    return ""


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


async def _handle_chat_history(ctx, args: dict) -> str:
    """当前会话的聊天记录：本地优先，不足时自动补拉 QQ 历史（零参数）。"""
    if ctx is None:
        return "error: 当前无会话上下文"

    runtime = getattr(ctx, "runtime", None)
    bot = getattr(ctx, "bot", None)
    session_id = str(getattr(ctx, "session_id", "") or "")
    if not session_id:
        return "error: 当前无会话 ID"

    args = args or {}
    scope = str(args.get("scope") or "auto").strip().lower()
    if scope not in _SCOPES:
        scope = "auto"
    limit = _parse_limit(args, default=DEFAULT_LIMIT, maximum=100)
    is_private = _is_private(session_id)

    # 目标一律从当前会话推导（不接受模型传参），避免越权读取其他会话
    fallback_target = session_id.split("_", 1)[1] if "_" in session_id else ""
    if is_private:
        group_id, user_id = "", str(getattr(ctx, "user_id", None) or fallback_target or "")
    else:
        group_id = str(getattr(ctx, "group_id", None) or fallback_target or "")
        user_id = ""

    local_lines: list[str] = []
    if scope in ("auto", "local"):
        local_lines = _render_local_lines(runtime, session_id, is_private, limit)

    try:
        min_local = int(_cfg(ctx, "history_auto_qq_min_local", DEFAULT_AUTO_QQ_MIN_LOCAL))
    except (TypeError, ValueError):
        min_local = DEFAULT_AUTO_QQ_MIN_LOCAL
    min_local = max(0, min(min_local, 50))

    need_qq = scope == "qq" or (scope == "auto" and len(local_lines) < min_local)
    qq_text = ""
    if need_qq:
        qq_text = await _fetch_qq_history(bot, runtime, group_id, user_id, limit)

    head_bits = [f"会话 {session_id}（{'私聊' if is_private else '群聊'}）"]
    head_bits.append(f"本地记录 {len(local_lines)} 条")
    if need_qq:
        head_bits.append("QQ 记录：已补拉" if qq_text else "QQ 记录：未取到")
    elif not local_lines:
        head_bits.append("QQ 记录：未查询")
    head = "；".join(head_bits)

    blocks: list[str] = []
    if local_lines:
        blocks.append("【本地会话记录】\n" + "\n".join(local_lines))
    if qq_text:
        blocks.append("【QQ 聊天记录】\n" + qq_text)

    if blocks:
        result = head + "\n\n" + "\n\n".join(blocks)
        if local_lines and len(local_lines) < min_local and not qq_text:
            result += "\n\n（本地记录较少，QQ 历史本次未取到；如需更多上下文可稍后重试或直接问用户。）"
        return result

    # 完全取不到：给明确路标，避免模型改用臆测/说“我不记得”
    hints = [f"{head}。"]
    if scope == "local":
        hints.append("本地没有可读记录；如需 QQ 原始聊天记录，请调用本工具并指定 scope=qq。")
    else:
        hints.append(
            "本地与 QQ 都没有取到聊天记录（可能是新会话，或连接/权限不可用）。"
            "可以稍后重试，或直接向用户询问；不要假装记得之前的对话。"
        )
    return "\n".join(hints)


def build_session_tools(runtime: Any, ctx: Any) -> list[ToolSpec]:
    """构造绑定当前 ToolContext 的系统级会话工具。"""

    async def _current_session(_ctx, _args: dict) -> str:
        return await _handle_current_session(ctx, _args)

    async def _chat_history(_ctx, _args: dict) -> str:
        return await _handle_chat_history(ctx, _args)

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
            source="system",
            category="会话",
        ),
        ToolSpec(
            name="get_chat_history",
            description=(
                "获取当前会话的聊天记录，无需任何参数（会话对象自动取当前群/当前对方）。"
                "默认先给本地会话记录，本地条数不足时自动补拉 QQ 原始聊天记录（最近 30 条）。"
                "当用户问“刚才/之前聊了什么”“我说过什么”“你指的是哪条”，"
                "或需要更早的上下文来回答时调用；不要在记录不足时凭猜测回答或说“我不记得”。"
                "scope=local 只看本地；scope=qq 强制查 QQ 聊天记录。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["auto", "local", "qq"],
                        "description": "auto=本地优先、不足自动补 QQ（默认）；local=仅本地；qq=仅 QQ",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回条数，1~100，默认 30。",
                    },
                },
            },
            handler=_chat_history,
            permission="member",
            scopes=("*",),
            source="system",
            category="会话",
        ),
    ]
