"""群打卡业务逻辑：指令（#打卡/#全群打卡/自定义）+ 每日定时自动打卡 + 成功通知。

权限：框架按 authority_type=normal 放行群成员，模块内按 permission_scope 配置
用 event.authority_level 二次校验（everyone≥2 / 群主管理员≥3 / 仅拥有者≥4）。
"""

from __future__ import annotations

from app.core.logger import module_logger
from app.modules import resolve_enabled_ids


# ==================== 旧配置迁移 ====================


def migrate_legacy_config(module) -> None:
    """旧配置迁移：priority_groups/priority_groups_mode → group_configs（一次性，保留拖拽顺序）。"""
    config = module.config
    if config.get("group_configs"):
        return
    legacy = config.get("priority_groups", None)
    if not legacy:
        return
    mode = config.get("priority_groups_mode", "all")
    groups = {}
    for i, gid in enumerate(resolve_enabled_ids(legacy, mode)):
        groups[gid] = {"enabled": True, "index": i}
    config.set("group_configs", groups)
    if mode in ("partial", "none"):
        config.set("group_mode", mode)


async def handle(module, event):
    """指令入口：匹配 #打卡 / #全群打卡 / 自定义触发词。"""
    if event.event_type != "message_group":
        return
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(module.name)
    config = module.config
    text = event.text.strip()

    if not _check_permission(module, event):
        return

    if text == "#打卡":
        if config.get("enable_signin_command", True):
            await _signin_current(module, event, logger)
        return

    if text == "#全群打卡":
        if config.get("enable_all_signin_command", True):
            await _signin_all(module, event, logger)
        return

    if config.get("enable_custom_commands", False):
        for cmd in config.get("custom_commands", []) or []:
            cmd = str(cmd).strip()
            if cmd and (text == f"#{cmd}" or text == f"/{cmd}"):
                await _signin_current(module, event, logger)
                return


# ==================== 指令处理 ====================


async def _signin_current(module, event, logger):
    """#打卡 — 为当前群签到。"""
    config = module.config
    group_id = event.group.group_id
    if not group_id:
        return
    gid = str(group_id)
    if not config.get("enable_ignore_group_check", False) and not _is_group_enabled(config, gid):
        await _maybe_reply(module, event, "该群不在可打卡列表内")
        return
    result = await event.bot.send_group_sign(group_id=group_id)
    ok = bool(result and result.get("status") == "ok")
    logger.info(f"群 {gid} 打卡{'成功' if ok else '失败'}")
    await _maybe_reply(module, event, "今日打卡已完成" if ok else "打卡失败，请稍后再试")


async def _signin_all(module, event, logger):
    """#全群打卡 — 为所有已启用群签到。"""
    groups = await _get_enabled_group_ids(module)
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


# ==================== 权限 ====================


def _check_permission(module, event) -> bool:
    """按 permission_scope 配置检查事件权限（level 由框架计算）。"""
    scope = module.config.get("permission_scope", "bot_owner_only")
    level = event.authority_level
    if level is None:
        return False
    if scope == "everyone":
        return level >= 2
    if scope == "bot_owner_and_group_admin":
        return level >= 3
    return level >= 4  # bot_owner_only


# ==================== 群列表 ====================


async def _get_enabled_group_ids(module) -> list:
    """获取排序后的已启用群 ID 列表（新群自动注册进 group_configs）。"""
    bot = module.ctx.bot
    if bot is None:
        return []
    resp = await bot.get_group_list()
    if not resp or resp.get("status") != "ok":
        return []
    data = resp.get("data", []) or []

    config = module.config
    group_configs = dict(config.get("group_configs", {}) or {})

    # 自动注册新群
    dirty = False
    for g in data:
        gid = str(g.get("group_id", ""))
        if not gid:
            continue
        if gid not in group_configs:
            group_configs[gid] = {"enabled": True, "index": len(group_configs)}
            dirty = True
    if dirty:
        config.set("group_configs", group_configs)

    mode = config.get("group_mode", "all")
    if mode == "all":
        enabled = [gid for gid, cfg in group_configs.items() if cfg.get("enabled", True)]
    elif mode == "none":
        enabled = []
    else:
        enabled = [gid for gid, cfg in group_configs.items() if cfg.get("enabled", False)]

    enabled.sort(key=lambda gid: group_configs.get(gid, {}).get("index", 9999))
    return enabled


def _is_group_enabled(config, group_id_str: str) -> bool:
    """判断单个群是否在启用列表中。"""
    mode = config.get("group_mode", "all")
    group_configs = config.get("group_configs", {}) or {}
    if mode == "all":
        return group_configs.get(group_id_str, {}).get("enabled", True)
    if mode == "none":
        return False
    return group_configs.get(group_id_str, {}).get("enabled", False)


# ==================== 批量执行 ====================


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


# ==================== 成功通知 ====================


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


# ==================== 定时任务 ====================


async def daily_sign_in(module, bot):
    """定时任务：全群打卡 + 成功通知。"""
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(module.name)
    config = module.config
    if not config.get("enable_daily_auto_signin", True):
        return

    groups = await _get_enabled_group_ids(module)
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
