"""三角洲行动今日密码业务逻辑（指令查询 + 定时推送 + 双站点 fallback + 精华管理）。"""

from __future__ import annotations

import json
import re
from datetime import date

from app.core.logger import module_logger
from app.modules import resolve_enabled_ids
from .deltaforce_api import fetch_passwords_from_site

PASSWORD_ESSENCE_RE = re.compile(r"三角洲行动 今日密码 \((\d+月\d+日)\)")

# 每日结果缓存（同一天不重复请求）
_cached_date = None
_cached_passwords = None


# ==================== 旧配置迁移 ====================


def migrate_legacy_config(module) -> None:
    """旧配置迁移：group_list/group_list_mode → command_response_groups/cmd_group_mode（一次性）。"""
    config = module.config
    if config.get("command_response_groups"):
        return
    legacy = config.get("group_list", None)
    if not legacy:
        return
    mode = config.get("group_list_mode", "all")
    groups = {}
    for i, gid in enumerate(resolve_enabled_ids(legacy, mode)):
        groups[gid] = {"enabled": True, "index": i}
    config.set("command_response_groups", groups)
    if mode in ("partial", "none"):
        config.set("cmd_group_mode", mode)


# ==================== 指令处理 ====================


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
    if not _check_group_enabled(
        group_id, config.get("cmd_group_mode", "all"), config.get("command_response_groups", {}) or {}
    ):
        return

    result_text = await _fetch_and_format(module)
    await event.reply(result_text if result_text else "三角洲行动今日密码获取失败，请稍后再试")
    # 模块已接管「今日密码」话题 → 跳过 LLM 兜底
    event.llm.stop()


# ==================== 定时推送 ====================


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
        if not _check_group_enabled(gid, config.get("cron_group_mode", "all"), cron_send_groups):
            continue
        result = await bot.send_group_msg(int(gid), text)
        logger.info(f"定时密码已发送到群 {gid}")
        if config.get("push_enable", False) and result and result.get("status") == "ok":
            message_id = result.get("data", {}).get("message_id")
            if message_id:
                await _set_essence(bot, int(gid), text, message_id, logger)


# ==================== 获取与格式化 ====================


async def _fetch_and_format(module) -> str | None:
    """获取密码并格式化为文本（主站失败 → 备用源）。"""
    config = module.config
    site = config.get("default_site", "kkrb")
    passwords = await _fetch_today_password(site)
    if passwords:
        return _format(passwords)

    if config.get("enable_fallback", False):
        fb_site = config.get("fallback_site", "tmini")
        if fb_site != site:
            module_logger.info(f"[DeltaForce] {site} 失败，尝试备用源 {fb_site}")
            passwords = await _fetch_today_password(fb_site)
            if passwords:
                return _format(passwords)

    return None


async def _fetch_today_password(site: str):
    """带每日缓存的站点获取。返回 {_date, 地图名: 密码} 或 None。"""
    global _cached_date, _cached_passwords

    today = date.today().strftime("%m月%d日")
    if _cached_date == today and _cached_passwords:
        return _cached_passwords

    result = await fetch_passwords_from_site(site)
    if result:
        _cached_date, _cached_passwords = today, result
    return result


def _format(passwords: dict) -> str:
    """格式化密码字典为可读文本。"""
    date_str = passwords.get("_date", "")
    header = "三角洲行动 今日密码"
    if date_str:
        header += f" ({date_str})"
    lines = [header]
    for name, pwd in passwords.items():
        if name == "_date":
            continue
        lines.append(f"{name}: {pwd}")
    return "\n".join(lines)


# ==================== 精华消息管理 ====================


async def _set_essence(bot, group_id: int, text: str, message_id, logger) -> None:
    """把推送消息设为精华：清理同模块旧精华，避免堆积。"""
    date_match = PASSWORD_ESSENCE_RE.search(text)
    today_date = date_match.group(1) if date_match else ""

    essence_resp = await bot.get_essence_msg_list(group_id)
    if not essence_resp or essence_resp.get("status") != "ok":
        logger.warning(f"群 {group_id} 获取精华列表失败，仍继续设置")
        await bot.set_essence_msg(message_id)
        return

    essence_list = essence_resp.get("data", [])
    for item in essence_list:
        content = _extract_essence_text(item)
        if not content:
            continue
        match = PASSWORD_ESSENCE_RE.search(content)
        if not match:
            continue
        if match.group(1) == today_date:
            logger.info(f"群 {group_id} 今日密码 ({today_date}) 已是精华，跳过")
            return
        await bot.delete_essence_msg(item.get("message_id"))
        logger.info(f"群 {group_id} 已移除旧精华消息 (message_id: {item.get('message_id')})")

    await bot.set_essence_msg(message_id)


def _extract_essence_text(item: dict) -> str:
    content = item.get("message_content") or item.get("content")
    if not content:
        return ""
    if isinstance(content, str):
        try:
            segments = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return content
    elif isinstance(content, list):
        segments = content
    else:
        return str(content)

    parts = []
    for seg in segments:
        if isinstance(seg, dict) and seg.get("type") == "text":
            text = seg.get("data", {}).get("text", "")
            if text:
                parts.append(text)
    return "".join(parts)


# ==================== 群检查 ====================


def _check_group_enabled(group_id: str, mode: str, configs: dict) -> bool:
    """根据模式检查群是否启用。"""
    cfg = configs.get(group_id, {})
    if mode == "all":
        return cfg.get("enabled", True)
    if mode == "none":
        return False
    return cfg.get("enabled", False)


# ==================== 定时注册 ====================


async def register_schedule(module):
    """按配置的 cron_time 动态注册每日推送任务（on_load 调用）。"""
    scheduler = module.ctx.services.scheduler
    if scheduler is None or module.bot_id is None:
        return
    if not module.config.get("enable_cron", False):
        await scheduler.unload_module(module.module_name, module.bot_id)
        return
    time_str = module.config.get("cron_time", "08:00")
    key = f"{module.module_name}:{module.bot_id}:cron"
    await scheduler.register(key, time_str, lambda: daily_push(module, module.ctx.bot))
