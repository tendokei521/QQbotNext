"""系统级会话上下文工具（不进入 NapCat 前端清单）。

设计目标：
- 只负责“当前会话/系统视角”的基础信息，不跟 NapCat Tool 清单耦合；
- 所有工具自动从 ``ToolContext`` 推导当前会话、机器人、用户、群号，不需要模型先知道 id；
- 读取成本低：优先本地会话记录，本地不足时按需补拉 QQ 历史。

历史工具演进：原 ``get_session_history`` 只读本地记录，模型在“本地记录不足”时便无从
下手（只能换工具猜或直接说“我不记得”），且与 NapCat 的两个 history 工具形成三选一。
现统一为 **``get_chat_history``**：

- 默认不传参：目标从当前会话推导，模型不需要知道 id；
- ``scope=auto``（默认）先给本地记录，本地条数不足 ``history_auto_qq_min_local`` 时自动
  补拉 QQ 原始聊天记录；
- 可选跨会话：私聊里可用 ``group_id`` / ``group_name`` 查“发起人自己也是成员”的群
  （回答只回给发起人）；群聊里查别的群、以及查别人的私聊一律拒绝（见
  ``_resolve_history_target``）；
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


def _int_id(value: Any) -> str:
    """把模型传来的 id 归一化为纯数字字符串；非法返回空串。"""
    try:
        return str(int(str(value).strip()))
    except (TypeError, ValueError):
        return ""


async def _resolve_group_by_name(bot, name: str) -> tuple[str, str]:
    """群名 → 群号；返回 (group_id, error_text)。精确匹配优先，其次唯一子串匹配。"""
    if bot is None:
        return "", "当前没有可用连接，无法按群名解析群号，请改用 group_id。"
    try:
        resp = await bot.get_group_list()
    except Exception:
        return "", "获取群列表失败，请改用 group_id 指定群号。"
    groups = (resp or {}).get("data") if isinstance(resp, dict) else []
    exact: list[tuple[str, str]] = []
    fuzzy: list[tuple[str, str]] = []
    for item in groups if isinstance(groups, list) else []:
        if not isinstance(item, dict):
            continue
        gid = str(item.get("group_id") or "")
        gname = str(item.get("group_name") or "")
        if not gid:
            continue
        if gname == name:
            exact.append((gid, gname))
        elif name in gname:
            fuzzy.append((gid, gname))
    if len(exact) == 1:
        return exact[0][0], ""
    if not exact and len(fuzzy) == 1:
        return fuzzy[0][0], ""
    cands = exact or fuzzy
    if not cands:
        return "", f"没找到群名包含「{name}」的群（机器人可能不在该群），请改用 group_id。"
    shown = "、".join(f"{n}({g})" for g, n in cands[:5])
    return "", f"群名「{name}」匹配到多个群：{shown}。请用 group_id 指定其中一个。"


async def _is_group_member(bot, group_id: str, user_id: str) -> bool:
    """发起人是否为该群成员（跨会话查询的授权凭据）；查询失败一律视为无权限。"""
    if bot is None or not group_id or not user_id:
        return False
    try:
        resp = await bot.get_group_member_info(group_id=int(group_id), user_id=int(user_id))
    except Exception:
        return False
    if not isinstance(resp, dict) or resp.get("status") != "ok":
        return False
    data = resp.get("data") or {}
    if not isinstance(data, dict) or not data:
        return False
    echoed = str(data.get("user_id") or "")
    return not echoed or echoed == str(user_id)


async def _resolve_history_target(
    ctx, bot, args: dict, *, is_private: bool, current_group: str, current_peer: str
) -> tuple[str, str, str, str]:
    """解析查询目标；返回 (group_id, user_id, cross_note, error)。

    安全模型（简洁但显式）：
    - 默认当前会话，始终允许；
    - 跨会话**只允许私聊发起、且发起人是该群成员**（结果只回给发起人）；
    - 群聊里不允许查别的群（回答会发进本群，等于泄漏给不在该群的人）；
    - 任何情况下都不允许查别人的私聊记录。
    """
    raw_group = args.get("group_id")
    raw_user = args.get("user_id")
    name = str(args.get("group_name") or "").strip()
    group_id = _int_id(raw_group)
    user_id = _int_id(raw_user)

    if raw_group not in (None, "") and not group_id:
        return "", "", "", "error: group_id 必须是纯数字群号"
    if raw_user not in (None, "") and not user_id:
        return "", "", "", "error: user_id 必须是纯数字 QQ 号"
    if name and not group_id:
        group_id, err = await _resolve_group_by_name(bot, name)
        if err:
            return "", "", "", f"error: {err}"
    if group_id and user_id:
        return "", "", "", "error: 一次只能查一个目标（group_id / group_name 与 user_id 二选一）"

    if group_id and group_id != current_group:
        if not is_private:
            return "", "", "", (
                "error: 群聊里不支持查询其它群的记录（会把别的群内容发到本群）。"
                "请在私聊里让我查。"
            )
        if not bool(_cfg(ctx, "history_cross_query_enable", True)):
            return "", "", "", "error: 跨会话查询已关闭（history_cross_query_enable=false）"
        requester = str(getattr(ctx, "user_id", "") or "")
        if not await _is_group_member(bot, group_id, requester):
            return "", "", "", (
                f"error: 你不是群 {group_id} 的成员，不能查询该群的聊天记录"
            )
        return group_id, "", f"（跨会话查询：群 {group_id}）", ""

    if user_id and user_id != current_peer:
        return "", "", "", (
            "error: 只能查询当前会话这条私聊的记录，不能读取你与其他人/机器人的私聊"
        )

    if group_id:
        return group_id, "", "", ""
    if user_id:
        return "", user_id, "", ""
    return current_group, current_peer, "", ""


async def _handle_chat_history(ctx, args: dict) -> str:
    """当前会话的聊天记录：本地优先，不足时自动补拉 QQ 历史。

    默认零参数（当前会话）；可选传入 ``group_id`` / ``group_name`` / ``user_id``
    以查询跨会话目标（授权规则见 ``_resolve_history_target``）。
    """
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

    # 当前会话目标（默认值；显式传参可查跨会话目标，授权规则见 _resolve_history_target）
    fallback_target = session_id.split("_", 1)[1] if "_" in session_id else ""
    if is_private:
        current_group, current_peer = "", str(getattr(ctx, "user_id", None) or fallback_target or "")
    else:
        current_group, current_peer = str(getattr(ctx, "group_id", None) or fallback_target or ""), ""

    group_id, user_id, cross_note, error = await _resolve_history_target(
        ctx, bot, args, is_private=is_private,
        current_group=current_group, current_peer=current_peer,
    )
    if error:
        return error
    # 本地会话记录只覆盖当前会话；跨会话目标只能走 QQ
    is_cross = (group_id or user_id) != (current_group or current_peer)

    if is_cross and scope == "local":
        return (
            f"error: 本地会话记录只覆盖当前会话，跨会话查询请用 scope=qq（或 auto）{cross_note}"
        )

    local_lines: list[str] = []
    if scope in ("auto", "local") and not is_cross:
        local_lines = _render_local_lines(runtime, session_id, is_private, limit)

    try:
        min_local = int(_cfg(ctx, "history_auto_qq_min_local", DEFAULT_AUTO_QQ_MIN_LOCAL))
    except (TypeError, ValueError):
        min_local = DEFAULT_AUTO_QQ_MIN_LOCAL
    min_local = max(0, min(min_local, 50))

    need_qq = is_cross or scope == "qq" or (scope == "auto" and len(local_lines) < min_local)
    qq_text = ""
    if need_qq:
        qq_text = await _fetch_qq_history(bot, runtime, group_id, user_id, limit)

    head_bits = [f"会话 {session_id}（{'私聊' if is_private else '群聊'}）"]
    if cross_note:
        head_bits.append(cross_note.strip("（）"))
    if not is_cross:
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
    if is_cross:
        hints.append(
            "该目标没有取到聊天记录（可能是新群/无历史，或机器人不在该群、连接不可用）。"
            "不要编造该群的内容；可以换个群名或稍后重试。"
        )
    elif scope == "local":
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
                "获取聊天记录。不传参数＝当前会话（默认先给本地记录，本地条数不足时自动补拉 "
                "QQ 原始聊天记录，最近 30 条）；当用户问“刚才/之前聊了什么”“我说过什么”"
                "“你指的是哪条”，或需要更早的上下文来回答时调用；不要在记录不足时凭猜测回答"
                "或说“我不记得”。\n"
                "跨会话（仅私聊场景）：用户在私聊里问“群里/某群说了什么”时，用 group_name（群名，"
                "如“蛋挞空间站”）或 group_id 指定该群；只允许查询发起人自己也是成员的群，"
                "结果只发给发起人。不要用它去查别的私聊记录。\n"
                "scope：auto=本地优先、不足补 QQ（默认）；local=仅本地（仅当前会话）；qq=仅 QQ。"
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
                    "group_id": {
                        "type": "integer",
                        "description": "要查询的群号；只在私聊里、且你/发起人是该群成员时可用（跨会话查询）",
                    },
                    "group_name": {
                        "type": "string",
                        "description": "要查询的群名（用户通常只说群名）；与 group_id 二选一，匹配到多个群时会返回候选让用户确认",
                    },
                    "user_id": {
                        "type": "integer",
                        "description": "私聊对方的 QQ 号；只允许查当前会话这一条私聊，其它一律拒绝",
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
