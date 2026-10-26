"""消息快照序列化与持久化库访问、启动/周期清理。"""

import asyncio
import os

from app.core.logger import module_logger
from app.plugin import get_data_path


def _serialize_event(event) -> dict:
    """把消息事件序列化为可落盘的快照 dict。"""
    return {
        "message_id": event.message_id,
        "group_id": event.group.group_id,
        "user_id": event.user_id,
        "user_card": event.user.card,
        "user_nickname": event.user.nickname,
        "self_id": event.self_id,
        "message": [seg.to_dict() for seg in event.message],
        "forward_msg": event.forward_msg or [],
    }


def _db_path(module) -> str:
    """持久化库文件路径（按 bot 实例隔离）。"""
    name = f"message_db_{module.bot_id}.json" if module.bot_id is not None else "message_db_global.json"
    return os.path.join(get_data_path(module.module_name), name)


def _get_db(module):
    """懒加载模块实例的持久化库；未启用返回 None。"""
    if not module.config.get("db_enable", True):
        return None
    db = getattr(module, "_recall_db", None)
    if db is None:
        from ..recall_db import RecallDB

        db = RecallDB(_db_path(module))
        module._recall_db = db
    return db


async def on_load(module) -> None:
    """装配：只做启动清理，**不武装计时器**（那属于 on_online）。"""
    db = _get_db(module)
    if db is None:
        return
    await _cleanup_once(module, db)


def _task_owner(module) -> str:
    return f"module:{module.module_name}:{module.bot_id}"


async def on_online(module) -> None:
    """账号连上/模块启用：武装周期清理任务。"""
    db = _get_db(module)
    if db is None or module.bot_id is None:
        return
    interval = int(module.config.get("db_clean_interval_minutes", 60) or 60)
    task_manager = module.ctx.services.task_manager
    if task_manager is None:
        return
    # 同 owner 的旧任务先取消，避免重复武装
    task_manager.cancel_owner(_task_owner(module))
    task_manager.create_task(
        _cleanup_loop(module, db, interval),
        name="recall_cleanup",
        owner=_task_owner(module),
    )


async def on_offline(module) -> None:
    """账号断开/模块禁用：停掉周期清理（否则会对着已释放的实例继续跑）。"""
    task_manager = getattr(getattr(module, "ctx", None), "services", None)
    task_manager = getattr(task_manager, "task_manager", None)
    if task_manager is not None and module.bot_id is not None:
        task_manager.cancel_owner(_task_owner(module))


async def _cleanup_once(module, db) -> None:
    max_total = int(module.config.get("db_max_messages", 5000) or 0)
    max_age = int(module.config.get("db_retention_minutes", 60) or 0)
    await db.cleanup(max_total=max_total, max_age_minutes=max_age)


async def _cleanup_loop(module, db, interval_minutes: int) -> None:
    """周期清理循环：按配置间隔淘汰超量/过期消息。"""
    while True:
        await asyncio.sleep(interval_minutes * 60)
        try:
            await _cleanup_once(module, db)
        except Exception as e:
            module_logger.error(f"[RecallBack] 周期清理异常: {e}")
