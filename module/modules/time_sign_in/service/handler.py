"""指令分发：#打卡 / #全群打卡 / 自定义触发词；命中即接管，跳过 LLM。"""

from app.core.logger import module_logger
from app.modules.groups import check_group_enabled, resolve_group_ids

from .signin import _execute_signin_for_groups


async def handle(module, event):
    """指令入口：匹配 #打卡 / #全群打卡 / 自定义触发词；命中即接管，跳过 LLM。"""
    if event.event_type != "message_group":
        return
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(module.name)
    config = module.config
    text = event.text.strip()

    if text == "#打卡":
        if config.get("enable_signin_command", True):
            await _signin_current(module, event, logger)
            event.llm.stop()  # 打卡已执行（含静默模式）
        return

    if text == "#全群打卡":
        if config.get("enable_all_signin_command", True):
            await _signin_all(module, event, logger)
            event.llm.stop()
        return

    if config.get("enable_custom_commands", False):
        for cmd in config.get("custom_commands", []) or []:
            cmd = str(cmd).strip()
            if cmd and (text == f"#{cmd}" or text == f"/{cmd}"):
                await _signin_current(module, event, logger)
                event.llm.stop()
                return


async def _signin_current(module, event, logger):
    """#打卡 — 为当前群签到。"""
    config = module.config
    group_id = event.group.group_id
    if not group_id:
        return
    gid = str(group_id)
    if not config.get("enable_ignore_group_check", False) and not check_group_enabled(config, gid):
        await _maybe_reply(module, event, "该群不在可打卡列表内")
        return
    result = await event.bot.send_group_sign(group_id=group_id)
    ok = bool(result and result.get("status") == "ok")
    logger.info(f"群 {gid} 打卡{'成功' if ok else '失败'}")
    await _maybe_reply(module, event, "今日打卡已完成" if ok else "打卡失败，请稍后再试")


async def _signin_all(module, event, logger):
    """#全群打卡 — 为所有已启用群签到。"""
    groups = await resolve_group_ids(module)
    if not groups:
        await _maybe_reply(module, event, "没有需要打卡的群")
        return
    await _maybe_reply(module, event, f"正在为 {len(groups)} 个群聊打卡")
    success, fail = await _execute_signin_for_groups(module, groups)
    logger.info(f"全群打卡完成: 成功 {success} 失败 {fail}")
    await _maybe_reply(module, event, f"已完成全群打卡，成功{success} 失败{fail}")


async def _maybe_reply(module, event, text):
    """静默模式下不回复。"""
    if module.config.get("enable_silence_signin", True):
        return
    await event.reply(text)
