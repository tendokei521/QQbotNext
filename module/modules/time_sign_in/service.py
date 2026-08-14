"""群打卡业务逻辑：每日 00:00 全群打卡 + 群内 #打卡 指令。"""

from app.core.logger import module_logger
from app.modules import resolve_enabled_ids


async def handle(module, event):
    if event.event_type == "message_group":
        await handle_message_event(module, event)


async def daily_sign_in(module, bot):
    """定时任务：每日 00:00 全群打卡（优先群按拖拽顺序先打）。"""
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(f"{module.name}")
    logger.info("开始执行每日群打卡")

    priority_cfg = module.config.get("priority_groups", {}) or {}
    priority_groups = resolve_enabled_ids(priority_cfg, module.config.get("priority_groups_mode", "all"))
    # 按拖拽顺序（index）排序优先群
    priority_groups.sort(key=lambda gid: priority_cfg.get(gid, {}).get("index", 0)
                         if isinstance(priority_cfg.get(gid), dict) else 0)
    group_list_response = await bot.get_group_list()
    if group_list_response.get("status") != "ok":
        logger.error(f"获取群列表失败: {group_list_response}")
        return

    groups = group_list_response.get("data", [])
    if not groups:
        logger.warning("未获取到任何群")
        return

    group_ids = [str(g.get("group_id", "")) for g in groups if g.get("group_id")]
    ordered_groups = []
    for gid in priority_groups:
        if gid in group_ids:
            ordered_groups.append(gid)
            group_ids.remove(gid)
    ordered_groups.extend(group_ids)

    logger.info(f"共 {len(ordered_groups)} 个群需要打卡")
    success_count = fail_count = 0
    for group_id in ordered_groups:
        try:
            result = await bot.send_group_sign(group_id=int(group_id))
            if result and result.get("status") == "ok":
                logger.debug(f"群 {group_id} 打卡成功")
                success_count += 1
            else:
                logger.warning(f"群 {group_id} 打卡失败: {result}")
                fail_count += 1
        except Exception as e:
            logger.error(f"群 {group_id} 打卡异常: {e}")
            fail_count += 1
    logger.info(f"群打卡完成: 成功 {success_count} 个, 失败 {fail_count} 个")


async def handle_message_event(module, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(f"{module.name}")
    msgtext = None
    for seg in event.message:
        if seg.type == "text":
            msgtext = seg.data.get("text", "")
            break
    if msgtext != "#打卡":
        return
    group_id = event.group.group_id
    logger.info(f"执行群 {group_id} 打卡")
    result = await event.bot.send_group_sign(group_id=int(group_id))
    if result and result.get("status") == "ok":
        logger.debug(f"群 {group_id} 打卡成功")
    else:
        logger.warning(f"群 {group_id} 打卡失败: {result}")
