"""打卡执行：批量打卡 + 每日定时任务 + 动态定时注册。"""

from app.core.logger import module_logger
from app.modules.groups import resolve_group_ids


async def _execute_signin_for_groups(module, group_ids: list) -> tuple:
    """按顺序为每个群签到，返回 (成功数, 失败数)。"""
    bot = module.ctx.bot
    success = fail = 0
    for gid in group_ids:
        try:
            result = await bot.send_group_sign(group_id=int(gid))
            if result and result.get("status") == "ok":
                success += 1
            else:
                fail += 1
        except Exception:
            fail += 1
    return success, fail


async def daily_sign_in(module, bot):
    """定时任务：全群打卡 + 成功通知。"""
    from .notify import _send_success_notify

    logger = module_logger.add_info(f"#{module.bot_id}").add_info(module.name)
    config = module.config
    if not config.get("enable_daily_auto_signin", True):
        return

    groups = await resolve_group_ids(module)
    if not groups:
        return
    logger.info(f"自动打卡 {len(groups)} 个群")
    success, fail = await _execute_signin_for_groups(module, groups)
    logger.info(f"自动打卡完成: 成功 {success} 失败 {fail}")
    await _send_success_notify(module, success, fail)


async def register_schedule(module):
    """按配置的 daily_signin_time 动态注册每日定时任务（on_load 调用）。"""
    scheduler = module.ctx.services.scheduler
    if scheduler is None or module.bot_id is None:
        return
    if not module.config.get("enable_daily_auto_signin", True):
        await scheduler.unload_module(module.module_name, module.bot_id)
        return
    time_str = module.config.get("daily_signin_time", "00:00")
    key = f"{module.module_name}:{module.bot_id}:daily"
    await scheduler.register(key, time_str, lambda: daily_sign_in(module, module.ctx.bot))
