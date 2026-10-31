"""主动消息的两个广播钩子实现（`main.py` 只做编排，不写业务）。

它们订阅框架广播的**事实**（有人说话了 / Bot 发话了），转给 `ProactiveManager`。
异常一律降级为 debug 日志：一次观察失败不该毁掉整轮对话。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import account_label, logger


async def on_message_observed(manager: Any, ctx: Any, bot_id: Any) -> None:
    """有人说话了：重置沉默计时 / 安排下次主动发言。"""
    if manager is None:
        return
    event = getattr(ctx, "event", None)
    if event is None:
        return
    try:
        await manager.on_message(
            ctx.session_id,
            getattr(event, "event_type", "") == "message_group",
            is_self=(getattr(event, "user_id", None) == getattr(event, "self_id", None)),
        )
    except Exception as e:  # noqa: BLE001 —— 业务异常不该毁掉整轮
        logger.add_info(account_label(bot_id)).debug(f"[主动消息] 观察消息失败（已忽略）: {e}")


async def on_bot_sent(manager: Any, ctx: Any, bot_id: Any) -> None:
    """Bot 发过话了：重置计时。"""
    if manager is None:
        return
    event = getattr(ctx, "event", None)
    if event is None:
        return
    try:
        await manager.on_bot_sent(
            ctx.session_id, getattr(event, "message_type", "") != "private"
        )
    except Exception as e:  # noqa: BLE001
        logger.add_info(account_label(bot_id)).debug(f"[主动消息] 更新发送状态失败（已忽略）: {e}")
