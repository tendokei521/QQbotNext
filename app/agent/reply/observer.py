"""参与度模式的全量观察：把群消息**先**写进会话历史，再谈要不要请求。

与旧实现的区别（见 `docs/plan/participation-reply-mode.md` P0-3/P0-8）：

1. 只在 `participation` 模式、且只在群聊生效——私聊永远走 legacy 触发，不进这里；
2. 不看冷却、不看触发、不看预算：门只回答"要不要花一次请求"，不回答"要不要记住"；
3. 失败必须**返回原因**，调用方只有拿到 `True` 才能置 `user_recorded`——
   旧实现在观察失败时也置 True，于是这条消息永久丢历史；
4. 写历史时绕开会话的 `can_reply()` 冷却（`get_or_create_session(observe=True)`），
   否则"bot 刚回完 5 秒内"的普通群消息会被判定成"本轮不触发"而不落库。
"""

from __future__ import annotations

from typing import Any

from app.agent.loop.turn_input import (
    TurnInput,
    get_or_create_session,
    record_user_turn,
)
from app.agent.session.manager import SessionManager
from app.core.logger import account_label, logger


def group_session_id(event: Any) -> str:
    """群聊会话 id（与 `LlmContext.session_id` 同口径）。"""
    group = getattr(event, "group", None)
    return f"group_{getattr(group, 'group_id', '')}"


def _nickname_of(event: Any) -> str:
    user = getattr(event, "user", None)
    if user is None:
        return ""
    return str(getattr(user, "card", "") or getattr(user, "nickname", "") or "")


async def observe_group_message(runtime: Any, event: Any, config: Any) -> tuple[bool, str]:
    """把一条群消息写进会话历史。

    Returns:
        ``(是否真的写入, 原因)``；``False`` 时调用方**不得**置 `user_recorded`。
    """
    raw = str(getattr(event, "text", "") or "").strip()
    segments = getattr(event, "message", None) or []
    if not raw and not segments:
        return False, "empty"

    turn = TurnInput(
        is_private=False,
        session_id=group_session_id(event),
        user_id=str(getattr(event, "user_id", "") or ""),
        group_id=str(getattr(getattr(event, "group", None), "group_id", "") or ""),
        user_text=raw,
        raw_user_text=raw,
        segment_text=raw,
        include_pre_history=False,
    )
    bot_id = str(getattr(runtime, "bot_id", ""))
    try:
        mgr = SessionManager(bot_id)
        session = await get_or_create_session(mgr, turn, config, None, observe=True)
        if session is None:
            # 观察口径下不该发生：observe=True 已绕开冷却。留痕便于发现回归。
            logger.add_info(account_label(bot_id)).warning(
                f"[参与度] 观察未拿到会话（{turn.session_id}），该条消息不会进历史"
            )
            return False, "no_session"
        await record_user_turn(
            runtime, event, turn, config, mgr, nickname=_nickname_of(event)
        )
        return True, "ok"
    except Exception as e:  # noqa: BLE001 —— 观察失败不阻断本轮，但必须可见
        logger.add_info(account_label(bot_id)).warning(
            f"[参与度] 全量写入失败（{turn.session_id}）: {e}"
        )
        return False, f"error:{e}"


__all__ = ["group_session_id", "observe_group_message"]
