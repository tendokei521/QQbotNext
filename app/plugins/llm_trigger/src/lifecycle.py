"""生命周期小工具：停计时器这类"失败也不该阻断卸载"的动作。"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger


def stop_quietly(obj: Any, label: str) -> None:
    """停掉一个计时器/调度器；异常只记 debug（卸载路径不能抛）。"""
    if obj is None:
        return
    stop = getattr(obj, "stop", None)
    if not callable(stop):
        return
    try:
        stop()
    except Exception as e:  # noqa: BLE001 —— 停止失败不该阻断卸载
        logger.debug(f"[{label}] 停止计时器失败（已忽略）: {e}")
