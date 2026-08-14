"""打卡成功通知。"""

from app.modules import resolve_enabled_ids


async def _send_success_notify(module, success: int, fail: int):
    """向配置的通知目标群/好友发送打卡结果消息。"""
    config = module.config
    if not config.get("enable_success_notify", False):
        return
    bot = module.ctx.bot
    if bot is None:
        return
    msg = f"✅ 每日自动打卡完成 — 成功: {success}，失败: {fail}"
    for gid in resolve_enabled_ids(config.get("notify_groups", {}), "partial"):
        await bot.send_group_msg(group_id=int(gid), message=msg)
    for uid in resolve_enabled_ids(config.get("notify_friends", {}), "partial"):
        await bot.send_private_msg(user_id=int(uid), message=msg)
