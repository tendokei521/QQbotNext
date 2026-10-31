"""``/memory`` 命令（v3）：list / search / profile / remember / forget / clear / audit / export。

三种前缀（``/memory``、``／memory``、``#llm memory``）由 main.py 的
``@command_group("memory", prefixes=("/", "／", "#llm "))`` 分发；本模块只做纯函数式
解析（``handle_memory_command``），便于测试与直调。契约见 §5.8。

隔离规则：
- list / search / profile / export：当前会话「可见」owner（私聊=本人；群聊=群公共+本人）；
- remember / forget / clear：只作用于「本人」层（群公共/他人记忆不可由普通成员误删）；
- audit：管理员。

回显统一 ``await send(text)``（框架会加 ``# `` 前缀）。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logger import logger
from app.plugin.commands import render_group_help as _render_group_help

#: 子命令说明：main.py 的命令 ``description`` 直接复用（也用于无子命令时的帮助清单）
COMMAND_HELP: dict[str, str] = {
    "list": "列出当前可见记忆（--all 含已下架/被替换）",
    "status": "查看记忆状态（条数/向量/蒸馏）",
    "search": "按关键词检索：search <词>",
    "profile": "查看当前会话的用户画像",
    "remember": "记住一条（写本人层）：remember <内容>",
    "forget": "删除本人记忆：forget <id|词>",
    "clear": "清空本人记忆",
    "audit": "查看记忆事件记录（管理员）：audit [owner]",
    "export": "导出可见记忆为 JSON（最多 100 条）",
}

#: 帮助清单的展示顺序
_USAGE_ORDER = (
    "list", "status", "search", "profile", "remember", "forget", "clear", "audit", "export",
)

#: 导出条数 / 文本上限（QQ 单条消息装不下太多）
_EXPORT_LIMIT = 100
_EXPORT_MAX_CHARS = 3500


def render_group_help(
    entries: Any, group: str = "memory", prefix: str = "/"
) -> str:
    """复用框架的命令组帮助渲染（供 main.py 的命令组入口使用）。"""
    return _render_group_help(entries, group, prefix=prefix)


def usage_text(prefix: str = "/") -> str:
    """渲染子命令帮助清单（无子命令 / 未知子命令时回显）。"""
    lines = ["【memory】长期记忆"]
    for name in _USAGE_ORDER:
        lines.append(f"{prefix}memory {name} — {COMMAND_HELP[name]}")
    return "\n".join(lines)


# ── 管理器 / 调用兼容 ─────────────────────────────────────


def _manager_of(module: Any) -> Any:
    """从模块取记忆管理器：``memory_manager()`` → 直接传 manager 的替身 → ``module.memory``。"""
    getter = getattr(module, "memory_manager", None)
    if callable(getter):
        try:
            manager = getter()
        except Exception as e:  # noqa: BLE001 —— 取管理器失败按「不可用」处理，但要留痕
            logger.warning(f"[记忆] 获取记忆管理器失败: {e}")
            manager = None
        if manager is not None:
            return manager
    # 测试/直调：第一个实参本身就是 manager
    if hasattr(module, "store") and hasattr(module, "scope_owners"):
        return module
    return getattr(module, "memory", None)


async def _invoke(method: Any, *args: Any, **kwargs: Any) -> Any:
    """调 manager/store 方法：协程函数直接 await，同步函数丢线程池（避免阻塞事件循环）。"""
    if inspect.iscoroutinefunction(method):
        return await method(*args, **kwargs)
    result = await asyncio.to_thread(method, *args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _audit(
    store: Any,
    action: str,
    owner: str,
    user_id: Any,
    *,
    summary: str = "",
    source: str = "manual",
) -> None:
    """写一条审计事件；审计失败只留痕，不影响命令结果。"""
    try:
        await asyncio.to_thread(
            store.audit,
            action,
            owner=owner,
            user_id=str(user_id or ""),
            summary=summary,
            source=source,
        )
    except Exception as e:  # noqa: BLE001 —— 审计是旁路，坏了不该让命令失败
        logger.debug(f"[记忆] 写审计事件失败（已忽略）: {e}")


# ── 入口 ──────────────────────────────────────────────────


async def handle_memory_command(
    module: Any,
    session_id: str,
    user_id: Any,
    is_private: bool,
    is_admin: bool,
    action: str,
    send: Callable[[str], Awaitable[None]],
) -> bool:
    """处理一条 ``memory`` 命令；返回 True 表示已处理（兼容旧测试/直调）。

    ``action`` 是命令词之后的文本（如 ``"list --all"``、``"search 美式"``），
    **不含** ``memory`` 前缀；空串表示只发了 ``/memory``。
    """
    try:
        manager = _manager_of(module)
        if manager is None:
            await send("长期记忆未启用（记忆服务不可用）")
            return True
        checker = getattr(manager, "enabled", None)
        if callable(checker) and not checker():
            await send("长期记忆未启用（配置 memory_enable=false）")
            return True
        text = await _dispatch(
            manager, session_id, str(user_id or ""), bool(is_private), bool(is_admin), action
        )
    except Exception as e:  # noqa: BLE001 —— 命令失败不该把异常抛进消息处理链
        logger.warning(f"[记忆] 命令处理失败: {e}")
        text = f"记忆命令处理失败：{e}"
    await send(text)
    return True


async def _status_text(manager: Any, store: Any) -> str:
    """记忆状态：条数 / 语料 / 向量 / 上次蒸馏（运维排查入口）。"""
    stats: dict = {}
    if store is not None:
        try:
            stats = await _invoke(store.stats) or {}
        except Exception as e:  # noqa: BLE001 —— 状态查询失败时仍返回可读文本
            logger.debug(f"[记忆] 读取统计失败（已忽略）: {e}")
    embedding: dict = {}
    getter = getattr(manager, "embedding_status", None)
    if callable(getter):
        try:
            embedding = await _invoke(getter) or {}
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[记忆] 读取向量状态失败（已忽略）: {e}")
    last_run = {}
    consolidator = getattr(manager, "consolidator", None)
    status = getattr(consolidator, "status", None)
    if callable(status):
        try:
            last_run = (status() or {}).get("last_run") or {}
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[记忆] 读取蒸馏状态失败（已忽略）: {e}")

    vector_state = "可用" if embedding.get("enabled") else "未启用（纯词法召回）"
    lines = [
        "【memory】长期记忆状态",
        f"记忆：{int(stats.get('memories') or 0)} 条 / {int(stats.get('owners') or 0)} 个 owner"
        f" / 画像 {int(stats.get('profiles') or 0)} 条",
        f"语料：{int(stats.get('episodes') or 0)} 条（未巩固的由后台蒸馏处理）",
        f"向量：{vector_state}  model={embedding.get('model') or '—'}",
    ]
    if isinstance(last_run, dict) and last_run:
        latest = max(float(value) for value in last_run.values())
        lines.append(f"上次蒸馏：{time.strftime('%m-%d %H:%M', time.localtime(latest))}")
    else:
        lines.append("上次蒸馏：尚无记录")
    return "\n".join(lines)


async def _dispatch(
    manager: Any,
    session_id: str,
    user_id: str,
    is_private: bool,
    is_admin: bool,
    action: str,
) -> str:
    cmd, arg = _parse_action(action)
    arg = arg.strip()
    store = getattr(manager, "store", None)

    if cmd == "list":
        if store is None:
            return "记忆存储不可用（管理器未初始化）"
        include_all = arg.casefold() in ("--all", "all", "-a")
        rows = await _invoke(
            store.list_for_owners,
            manager.scope_owners(session_id, user_id),
            limit=30,
            include_all=include_all,
        )
        return _format_rows(rows or [], include_all=include_all)

    if cmd == "status":
        return await _status_text(manager, store)

    if cmd == "search":
        if not arg:
            return "用法：#llm memory search <词>"
        text = await _invoke(manager.search_async, session_id, user_id, arg, limit=10)
        text = str(text or "").strip()
        return text or f"未找到与「{arg}」相关的记忆"

    if cmd == "profile":
        text = str(await _invoke(manager.profile_block, session_id, user_id) or "").strip()
        return text or "还没有关于你的画像记忆"

    if cmd == "remember":
        if not arg:
            return "用法：#llm memory remember <内容>"
        own = manager.own_owner(session_id, user_id)
        mid = await _invoke(
            manager.save_fact,
            arg,
            own,
            importance=0.6,
            source="manual",
            source_user=user_id,
            confidence=0.8,
        )
        if mid is None:
            return "保存失败（长期记忆未启用或内容为空）"
        return f"已记住：{arg}"

    if cmd == "forget":
        if not arg:
            return "用法：#llm memory forget <id|词>"
        if store is None:
            return "记忆存储不可用（管理器未初始化）"
        own = manager.own_owner(session_id, user_id)
        row = await _invoke(store.get_owned, arg, own)
        if row:
            await _invoke(store.delete_fact, arg, owner=own)
            await _audit(store, "forget", own, user_id, summary=str(row.get("content") or ""))
            return "已删除 1 条记忆"
        deleted = int(await _invoke(store.delete_by_query, own, arg) or 0)
        if deleted:
            await _audit(store, "forget", own, user_id, summary=f"query={arg}")
            return f"已删除 {deleted} 条记忆"
        return "没有可删除的记忆（只允许删除你自己的记忆）"

    if cmd == "clear":
        if store is None:
            return "记忆存储不可用（管理器未初始化）"
        own = manager.own_owner(session_id, user_id)
        count = int(await _invoke(store.clear, own) or 0)
        await _audit(store, "clear", own, user_id, summary=f"clear {own}")
        note = "" if is_private else "（群公共记忆需管理员另行处理）"
        return f"已清空 {count} 条个人记忆{note}"

    if cmd == "audit":
        if not is_admin:
            return "权限不足：audit 仅管理员可用"
        if store is None:
            return "记忆存储不可用（管理器未初始化）"
        rows = await _audit_rows(manager, store, session_id, user_id, arg)
        if not rows:
            return "暂无记忆事件记录"
        lines = [f"记忆事件记录 {len(rows)} 条："]
        for row in rows:
            lines.append(
                f"- {_fmt_time(row.get('ts'))} [{row.get('action')}] {row.get('owner') or ''} "
                f"{row.get('user_id') or ''} {row.get('summary') or ''}".rstrip()
            )
        return "\n".join(lines)

    if cmd == "export":
        if store is None:
            return "记忆存储不可用（管理器未初始化）"
        rows = await _invoke(
            store.list_for_owners,
            manager.scope_owners(session_id, user_id),
            limit=_EXPORT_LIMIT,
        )
        return _export_json(rows or [])

    return usage_text()


# ── 细节 ──────────────────────────────────────────────────


def _parse_action(action: str) -> tuple[str, str]:
    """把命令文本解析成 ``(子命令, 剩余参数)``；容忍旧式 ``memory`` 前缀。"""
    text = str(action or "").strip()
    lowered = text.casefold()
    if lowered.startswith("#llm "):
        text = text[len("#llm "):].strip()
        lowered = text.casefold()
    if lowered == "memory":
        text = ""
    elif lowered.startswith("memory "):
        text = text[len("memory "):].strip()
    if not text:
        return "", ""
    parts = text.split(maxsplit=1)
    return parts[0].casefold(), (parts[1] if len(parts) > 1 else "")


def _format_rows(rows: list[dict], *, include_all: bool) -> str:
    """渲染 ``list`` 结果：``[id 前 8 位][状态] 内容 (置信 0.xx)``。"""
    if not rows:
        return "当前可见记忆：无（还没有记住任何内容）"
    suffix = "（含已下架/被替换）" if include_all else ""
    lines = [f"当前可见记忆 {len(rows)} 条：{suffix}"]
    for row in rows:
        status = str(row.get("status") or "active")
        content = str(row.get("content") or "").strip()
        mid = str(row.get("id") or "")[:8]
        try:
            confidence = float(row.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        lines.append(f"- [{mid}][{status}] {content} (置信 {confidence:.2f})")
    return "\n".join(lines)


async def _audit_rows(
    manager: Any, store: Any, session_id: str, user_id: str, arg: str
) -> list[dict]:
    """取审计行：``all``=全部，``<owner>``=指定 owner，空=当前可见 owner 合并。"""
    if arg.casefold() in ("all", "*"):
        rows = await _invoke(store.recent_audit, limit=50)
        return list(rows or [])
    if arg:
        rows = await _invoke(store.recent_audit, owner=arg, limit=50)
        return list(rows or [])
    merged: dict[Any, dict] = {}
    for owner in manager.scope_owners(session_id, user_id):
        for row in await _invoke(store.recent_audit, owner=owner, limit=50) or []:
            merged[row.get("id")] = row
    return sorted(merged.values(), key=lambda row: int(row.get("ts") or 0), reverse=True)[:50]


def _export_json(rows: list[dict]) -> str:
    """把可见记忆导成 JSON 文本（≤100 条，内容截断到 200 字，总长受控）。"""
    payload: list[dict] = []
    for row in rows:
        try:
            confidence = round(float(row.get("confidence") or 0.0), 3)
        except (TypeError, ValueError):
            confidence = 0.0
        payload.append(
            {
                "id": row.get("id"),
                "owner": row.get("owner"),
                "kind": row.get("kind"),
                "content": str(row.get("content") or "")[:200],
                "confidence": confidence,
                "status": row.get("status"),
                "source": row.get("source"),
                "updated_at": row.get("updated_at"),
            }
        )
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) > _EXPORT_MAX_CHARS:
        text = text[:_EXPORT_MAX_CHARS] + "\n…（已截断）"
    return f"记忆导出（{len(payload)} 条）：\n{text}"


def _fmt_time(ts: Any) -> str:
    """epoch 秒 → ``MM-DD HH:MM:SS``；坏值原样返回。"""
    try:
        return time.strftime("%m-%d %H:%M:%S", time.localtime(int(ts)))
    except (TypeError, ValueError, OSError):
        return str(ts)
