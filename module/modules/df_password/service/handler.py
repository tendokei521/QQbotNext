"""指令与定时推送处理：#今日密码 查询 + 每日定时推送（含精华设置）。"""

from functools import partial

from app.core.logger import module_logger
from app.modules import register_daily_schedule
from app.modules.groups import check_group_enabled

from .essence import _set_essence
from .fetch import _fetch_and_format


async def handle(module, event):
    """#今日密码 指令 → 获取密码并回复；命中即接管，跳过 LLM。"""
    if event.event_type != "message_group":
        return
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(module.name)
    config = module.config

    if not config.get("enable_command", True):
        return

    text = event.text.strip()
    strict_text = config.get("strict_text", True)
    if strict_text and text != "#今日密码":
        return
    if not strict_text and "今日密码" not in text:
        return

    group_id = str(event.group.group_id)
    if not check_group_enabled(
        config, group_id, key="command_response_groups", mode_key="cmd_group_mode"
    ):
        return

    result_text = await _fetch_and_format(module)
    await event.reply(result_text if result_text else "三角洲行动今日密码获取失败，请稍后再试")
    # 模块已接管「今日密码」话题 → 跳过 LLM 兜底
    event.llm.stop()


async def daily_push(module, bot):
    """定时任务：向启用的群推送今日密码（可选设置精华）。"""
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(module.name)
    config = module.config

    if not config.get("enable_cron", False):
        return

    text = await _fetch_and_format(module)
    if not text:
        logger.warning("定时获取密码失败，跳过本次发送")
        return

    cron_send_groups = config.get("cron_send_groups", {}) or {}
    for gid, cfg in cron_send_groups.items():
        if not check_group_enabled(config, gid, key="cron_send_groups", mode_key="cron_group_mode"):
            continue
        try:
            result = await bot.send_group_msg(int(gid), text)
        except Exception as e:
            logger.error(f"定时密码发送到群 {gid} 失败: {e}")
            continue
        logger.info(f"定时密码已发送到群 {gid}")
        if config.get("push_enable", False) and result and result.get("status") == "ok":
            message_id = result.get("data", {}).get("message_id")
            if message_id:
                try:
                    await _set_essence(bot, int(gid), text, message_id, logger)
                except Exception as e:
                    logger.error(f"群 {gid} 设置精华失败: {e}")


async def register_schedule(module):
    """按配置的 cron_time 动态注册每日推送任务（on_load 调用）。"""
    await register_daily_schedule(
        module,
        key_suffix="cron",
        enable_key="enable_cron",
        time_key="cron_time",
        handler_factory=lambda: partial(daily_push, module, module.ctx.bot),
    )
