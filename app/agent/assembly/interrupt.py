"""回复打断的配置桥：把「对话行为」页的开关落到运行时的中断标记上。

它只读**本体**配置（`interrupt_enable` / `interrupt_save_sent` / `interrupt_debug`
都在 Agent「对话行为」页），与业务模块无关，所以留在框架。
"""

from __future__ import annotations

from app.agent import logger


def _flag(ctx, key: str, default: bool) -> bool:
    """读本体配置（打断开关属本体，不走模块视图）。"""
    config = getattr(getattr(ctx, "runtime", None), "config", None)
    getter = getattr(config, "get", None)
    if not callable(getter):
        return default
    try:
        return bool(getter(key, default))
    except Exception:  # noqa: BLE001 —— 配置异常按默认值
        return default

async def interrupt_config_hook(ctx):
    ctx.runtime.interrupt_enabled = _flag(ctx, "interrupt_enable", False)
    ctx.runtime.interrupt_save_sent = _flag(ctx, "interrupt_save_sent", True)
    if _flag(ctx, "interrupt_debug", False):
        logger.add_info(f"#{ctx.runtime.bot_id}").info(
            f"[打断] {ctx.session_id} interrupt_enabled={ctx.runtime.interrupt_enabled}"
        )
