"""#chat memory 命令（P1：list / search / forget / clear；P3 增 audit）。

沿用 ``#chat schedule`` 的命令风格。隔离规则：
- list / search：展示当前会话「可见」owner（私聊=本人；群聊=群公共+本人）；
- forget / clear：只作用于「本人」层（群公共/他人记忆不可由普通成员误删）；
- audit：管理员，见 P3。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.llm.memory.recall import rank
from app.llm.memory.store import owner_group_member, owner_private

_USAGE = (
    "用法：\n"
    "#chat memory list\n"
    "#chat memory search <词>\n"
    "#chat memory forget <id|词>\n"
    "#chat memory clear\n"
    "#chat memory audit [owner]（管理员）"
)


async def handle_memory_command(
    module: Any,
    session_id: str,
    user_id: Any,
    is_admin: bool,
    is_private: bool,
    action: str,
    send: Callable[[str], Awaitable[None]],
) -> bool:
    memory = getattr(module, "memory", None)
    if memory is None or not memory.enabled():
        await send("长期记忆未启用（配置 memory_enable=false）")
        return True
    response = await _dispatch(
        memory, session_id, user_id, is_admin, action
    )
    await send(response)
    return True


async def _dispatch(memory, session_id, user_id, is_admin, action: str) -> str:
    rest = action[len("memory"):].strip() if action.startswith("memory") else ""
    cmd = "list"
    arg = ""
    if rest:
        parts = rest.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

    store = memory.store
    owners = memory.scope_owners(session_id, user_id)

    if cmd == "list":
        rows = store.list_for_owners(owners, limit=30)
        if not rows:
            return "当前可见记忆：无（还没有记住任何内容）"
        lines = [f"当前可见记忆 {len(rows)} 条："]
        for r in rows:
            lines.append(f"  - [{r['id'][:6]}] {r['content']} (重要度{r['importance']:.1f})")
        return "\n".join(lines)

    if cmd == "search":
        if not arg:
            return "用法：#chat memory search <词>"
        hits = rank(store, owners=owners, query=arg, limit=10, max_chars=2000)
        if not hits:
            return f"未找到与「{arg}」相关的记忆"
        lines = [f"搜索「{arg}」命中 {len(hits)} 条："]
        for r in hits:
            lines.append(f"  - [{r['id'][:6]}] {r['content']}")
        return "\n".join(lines)

    if cmd == "forget":
        if not arg:
            return "用法：#chat memory forget <id|词>"
        deleted = _forget(store, session_id, user_id, arg)
        if deleted == 0:
            return "没有可删除的记忆（只允许删除你自己的记忆）"
        return f"已删除 {deleted} 条记忆"

    if cmd == "clear":
        own = _own_owner(session_id, user_id)
        count = store.clear(own)
        note = ""
        if not str(session_id or "").startswith("private_"):
            note = "（群公共记忆需管理员另行处理）"
        store.audit("clear", owner=own, user_id=str(user_id), summary=f"clear {own}", source="manual")
        return f"已清空 {count} 条个人记忆{note}"

    if cmd == "audit":
        if not is_admin:
            return "权限不足：audit 仅管理员可用"
        if arg in ("all", "*"):
            rows = store.recent_audit(limit=50)
        elif arg:
            rows = store.recent_audit(owner=arg, limit=50)
        else:
            merged = {}
            for ow in owners:
                for r in store.recent_audit(owner=ow, limit=50):
                    merged[r["id"]] = r
            rows = sorted(merged.values(), key=lambda r: r.get("ts", 0), reverse=True)[:50]
        if not rows:
            return "暂无记忆事件记录"
        lines = [f"记忆事件记录 {len(rows)} 条："]
        for r in rows:
            lines.append(
                f"  {_fmt_time(r.get('ts'))} [{r.get('action')}] {r.get('owner')} "
                f"{r.get('user_id') or ''} {r.get('summary') or ''}"
            )
        return "\n".join(lines)

    return _USAGE


def _fmt_time(ts) -> str:
    try:
        import time

        return time.strftime("%m-%d %H:%M:%S", time.localtime(int(ts)))
    except Exception:
        return str(ts)


def _forget(store, session_id: str, user_id: Any, target: str) -> int:
    """只删除「本人层」记忆：优先按 id，其次按词匹配。"""
    own = _own_owner(session_id, user_id)
    row = store.get_owned(target, own)
    if row:
        store.delete_fact(target, owner=own)
        store.audit("forget", owner=own, user_id=str(user_id), summary=row.get("content", ""), source="manual")
        return 1
    deleted = store.delete_by_query(own, target)
    if deleted:
        store.audit("forget", owner=own, user_id=str(user_id), summary=f"query={target}", source="manual")
    return deleted


def _own_owner(session_id: str, user_id: Any) -> str:
    if str(session_id or "").startswith("private_"):
        return owner_private(session_id[len("private_"):])
    if str(session_id or "").startswith("group_"):
        return owner_group_member(session_id[len("group_"):], user_id)
    return "global"
