"""打卡成功通知。"""

from app.core.logger import module_logger
from app.modules import resolve_enabled_ids


async def _send_success_notify(module, success: int, fail: int):
    """向配置的通知目标群/好友发送打卡结果消息（单目标失败不影响其他）。"""
    config = module.config
    if not config.get("enable_success_notify", False):
        return
    bot = module.ctx.bot
    if bot is None:
        return
    msg = f"✅ 每日自动打卡完成 — 成功: {success}，失败: {fail}"
    for gid in resolve_enabled_ids(config.get("notify_groups", {}), "partial"):
        try:
            await bot.send_group_msg(group_id=int(gid), message=msg)
        except Exception as e:
            module_logger.warning(f"通知群 {gid} 发送失败: {e}")
    for uid in resolve_enabled_ids(config.get("notify_friends", {}), "partial"):
        try:
            await bot.send_private_msg(user_id=int(uid), message=msg)
        except Exception as e:
            module_logger.warning(f"通知好友 {uid} 发送失败: {e}")
