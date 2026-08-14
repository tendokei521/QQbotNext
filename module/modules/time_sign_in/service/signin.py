"""打卡执行：批量打卡 + 每日定时任务。"""

import asyncio
from functools import partial

from app.core.logger import module_logger
from app.modules import register_daily_schedule
from app.modules.groups import resolve_group_ids


async def _execute_signin_for_groups(module, group_ids: list, batch: int = 4) -> tuple:
    """为每个群签到（分批并发，防串行拖慢 + 防限流），返回 (成功数, 失败数)。"""
    bot = module.ctx.bot

    async def _one(gid: str) -> bool:
        try:
            result = await bot.send_group_sign(group_id=int(gid))
            return bool(result and result.get("status") == "ok")
        except Exception:
            return False

    success = 0
    for i in range(0, len(group_ids), batch):
        results = await asyncio.gather(*(_one(g) for g in group_ids[i:i + batch]))
        success += sum(1 for ok in results if ok)
    return success, len(group_ids) - success


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
    await register_daily_schedule(
        module,
        key_suffix="daily",
        enable_key="enable_daily_auto_signin",
        time_key="daily_signin_time",
        handler_factory=lambda: partial(daily_sign_in, module, module.ctx.bot),
    )
