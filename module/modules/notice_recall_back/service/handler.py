"""防撤回主入口：消息缓存 + 撤回检测与转发（吸收旧版 msg_recallback 业务逻辑）。

- 消息事件 → 内存缓存（cache_time）+ 持久化库（db_enable，每群上限 db_max_per_group）；
- 撤回事件 → 监听开关过滤 → 内存/磁盘查缓存 → 构建合并转发 → 按开关转发（单目标失败不影响其他）。
"""

from app.core.logger import module_logger
from app.modules import resolve_enabled_ids

from .cache import _get_db, _serialize_event
from .forward import build_forward_msg_data


async def handle(module, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(module.name)
    cache = module.ctx.services.cache

    config = module.config
    cache_time = int(config.get("cache_time", 600) or 600)
    if cache_time <= 0:
        return

    if event.event_type in ("message_group", "message_private"):
        # 缓存普通消息，等待撤回事件使用（自身消息不缓存）
        if event.user_id == event.self_id:
            return
        if cache.has(f"{event.message_id}_msgobject"):
            return
        cache.set(f"{event.message_id}_msgobject", event, cache_time)
        # 磁盘持久层（重启兜底；按群上限淘汰）
        db = _get_db(module)
        if db is not None:
            await db.store(
                str(event.message_id),
                _serialize_event(event),
                max_per_group=int(config.get("db_max_per_group", 200) or 0),
            )
        return

    if event.event_type in ("notice_group_recall", "notice_private_recall"):
        # ── 监听开关过滤（旧版语义）──
        if event.event_type == "notice_group_recall" and not config.get("enable_group_listen", True):
            return
        if event.event_type == "notice_private_recall" and not config.get("enable_private_listen", True):
            return

        # ── 查找缓存：内存 → 磁盘 ──
        recalled = cache.get(f"{event.message_id}_msgobject")
        if recalled is not None:
            recalled = _serialize_event(recalled)
        else:
            db = _get_db(module)
            if db is not None:
                data = await db.get(str(event.message_id))
                if data:
                    recalled = data
                    logger.debug(f"消息 {event.message_id} 命中磁盘缓存")
        if not recalled:
            logger.warning(f"消息 {event.message_id} 未缓存")
            return
        logger.debug(f"消息 {event.message_id} 已缓存")

        # ── 构建转发消息 ──
        forward_msg_data = build_forward_msg_data(recalled, event)
        if not forward_msg_data:
            return

        # ── 转发到群（单目标失败不影响其他，旧版语义）──
        if config.get("enable_forward_to_group", True):
            for gid in resolve_enabled_ids(config.get("target_groups", {}), config.get("target_groups_mode", "all")):
                try:
                    await event.bot.send_forward_msg(group_id=int(gid), msgdata=forward_msg_data)
                    logger.info(f"转发到群 {gid}")
                except Exception as e:
                    logger.error(f"转发到群 {gid} 失败: {e}")

        # ── 转发到私聊 ──
        if config.get("enable_forward_to_private", True):
            for uid in resolve_enabled_ids(config.get("target_users", {}), config.get("target_users_mode", "all")):
                try:
                    await event.bot.send_forward_msg(user_id=int(uid), msgdata=forward_msg_data)
                    logger.info(f"转发到用户 {uid}")
                except Exception as e:
                    logger.error(f"转发到用户 {uid} 失败: {e}")
        return
