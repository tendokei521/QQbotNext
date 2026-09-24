"""群聊环境块的装配入口：把记录面查出来的事件渲染成可直接放进 prompt 的文本。

这里是**唯一的取值口径**，四个装配路径（普通回复 / 流式 / 主动消息 / 定时任务）
都调它，避免"为什么主动消息没有群聊环境"这类问题只能从函数体里找答案。

三条纪律：

1. **无 store 就返回空串**（模块未装/未启用/未接入 LLM）→ 降级，不阻断主流程；
2. **去重交给会话历史**：本轮会话窗口里的 message_id 全部登记为"已覆盖"，
   正文只在会话历史里出现一次，但表情/撤回/我的动作仍会以影子行保留；
3. **丢数据必须留痕**：预算丢弃、无记录两种情况都写日志，不静默变短。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.core.logger import logger
from app.llm.group_log.events import make_scope
from app.llm.group_log.render import (
    DEFAULT_MAX_CHARS,
    DEFAULT_WINDOW_LIMIT,
    DEFAULT_WINDOW_MINUTES,
    RenderResult,
    render_environment,
)
from app.llm.group_log.store import store_of


def _cfg(config: Any, key: str, default: Any = None) -> Any:
    try:
        return config.get(key, default)
    except Exception:  # noqa: BLE001 - 配置对象异常按默认值处理
        return default


def _int_cfg(config: Any, key: str, default: int) -> int:
    try:
        return int(_cfg(config, key, default) or default)
    except (TypeError, ValueError):
        return default


def _history_message_ids(history: Iterable[Any]) -> set[str]:
    """本轮会话窗口里的 message_id（这些正文已由会话历史呈现，环境块不再重复）。"""
    ids: set[str] = set()
    for entry in history or []:
        data = entry if isinstance(entry, dict) else getattr(entry, "__dict__", {}) or {}
        base = data.get("base") if isinstance(data.get("base"), dict) else {}
        mid = str(data.get("message_id") or base.get("message_id") or "").strip()
        if mid:
            ids.add(mid)
    return ids


def scope_for_tool_ctx(tool_ctx: Any) -> str:
    """工具调用上下文 → 记录面分片键（表情闭环与渲染共用同一口径）。

    群聊取群号；私聊取**对方 QQ**（``self<b>_<bot_id>`` 这类只有自己知道的键按空处理）。
    """
    event = getattr(tool_ctx, "event", None)
    group_id = getattr(tool_ctx, "group_id", None) or getattr(event, "group_id", None)
    if not group_id:
        group = getattr(event, "group", None)
        group_id = getattr(group, "group_id", None) if group is not None else None
    if group_id:
        return make_scope(True, group_id=group_id)
    session_id = str(getattr(tool_ctx, "session_id", "") or "")
    if session_id.startswith("private_"):
        peer = session_id[len("private_"):]
        if peer and not peer.startswith("self"):
            return make_scope(False, user_id=peer)
    user_id = getattr(tool_ctx, "user_id", None)
    if user_id and not str(user_id).startswith("self"):
        return make_scope(False, user_id=user_id)
    return ""


def build_context_text(
    runtime: Any,
    session_id: str,
    *,
    history: Iterable[Any] | None = None,
    is_private: bool = False,
    group_id: Any = None,
    user_id: Any = None,
    tool_ctx: Any = None,
) -> str:
    """取当前会话的群聊环境文本；任何异常都降级为空串。"""
    config = getattr(runtime, "config", None)
    if not bool(_cfg(config, "group_log_enable", True)):
        return ""
    store = store_of(runtime)
    if store is None:
        return ""
    scope = make_scope(not is_private, group_id=group_id, user_id=user_id)
    if not scope:
        return ""

    minutes = _int_cfg(config, "group_log_window_minutes", DEFAULT_WINDOW_MINUTES)
    limit = _int_cfg(config, "group_log_window_limit", DEFAULT_WINDOW_LIMIT)
    max_chars = _int_cfg(config, "group_log_max_chars", DEFAULT_MAX_CHARS)

    try:
        events = store.recent(scope, minutes=minutes, limit=limit)
        if not events:
            return ""
        # 本轮触发消息已在会话历史里（prepare_prompt 先写入再排除自己），所以
        # 它的正文不会在环境块里重复；它上面的反应/我的动作仍会以影子行保留。
        covered = _history_message_ids(history)
        my_actions = {
            str(e.message_id): store.my_actions_on(scope, e.message_id)
            for e in events
            if e.message_id and e.by_me
        }
        result: RenderResult = render_environment(
            events,
            my_actions={k: v for k, v in my_actions.items() if v},
            covered_ids=covered,
            max_chars=max_chars,
            window_minutes=minutes,
            window_limit=limit,
        )
    except Exception as e:  # noqa: BLE001 - 环境块失败不能影响回复主流程
        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(
            f"[GroupLog] 渲染环境块失败（按空处理）: {e}"
        )
        return ""

    stats = result.stats
    if stats.dropped_by_budget or stats.dropped_covered:
        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(
            f"[GroupLog] {session_id} 环境块：{stats.messages} 条消息 / "
            f"{stats.events} 事件，预算丢弃 {stats.dropped_by_budget} 行、"
            f"去重 {stats.dropped_covered} 条，{stats.chars} 字符"
        )
    return result.text
