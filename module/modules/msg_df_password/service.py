"""三角洲行动今日密码业务逻辑（消息指令 + 每日 05:00 定时推送）。"""

import json
import re
from datetime import date

import aiohttp

from app.core.logger import module_logger
from app.modules import resolve_enabled_ids

API_URL = "https://www.tmini.net/api/sjzmm?ckey=&type="
PASSWORD_ESSENCE_RE = re.compile(r"三角洲行动 今日密码 \((\d+月\d+日)\)")

_cached_date = None
_cached_passwords = []


async def handle(module, event):
    if event.event_type == "message_group":
        await message_main(module, event)


async def message_main(module, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(f"{module.name}")
    config = module.config
    strict_text = config.get("strict_text", True)
    enabled_groups = resolve_enabled_ids(config.get("group_list", {}), config.get("group_list_mode", "all"))
    group_id = str(event.group.group_id)
    if group_id not in enabled_groups:
        return

    message_text = ""
    for seg in event.message:
        if seg.type == "text":
            message_text = seg.data.get("text", "")
            break

    if not strict_text and "今日密码" not in message_text.strip():
        return
    if strict_text and message_text.strip() != "#今日密码":
        return

    push_enable = config.get("push_enable", False)
    await df_password_main(module, event.bot, int(event.group.group_id), push_enable, logger)


async def daily_push(module, bot):
    """定时任务：向启用的群推送今日密码。"""
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(f"{module.name}")
    config = module.config
    push_enable = config.get("push_enable", False)
    enabled_groups = resolve_enabled_ids(config.get("group_list", {}), config.get("group_list_mode", "all"))
    for group_id in enabled_groups:
        await df_password_main(module, bot, int(group_id), push_enable, logger)


async def df_password_main(module, bot, group_id: int, push_enable: bool, logger):
    passwords = await fetch_today_password()
    if not passwords:
        await bot.send_group_msg(group_id, "三角洲行动今日密码获取失败，请稍后再试")
        return

    date_str = passwords[0]
    msg = format_password_message(date_str, passwords[1])
    result = await bot.send_group_msg(group_id, msg)

    if push_enable:
        need_set = await manage_essence_messages(bot, group_id, date_str, logger)
        if not need_set:
            return

    if push_enable and result and result.get("status") == "ok":
        message_id = result.get("data", {}).get("message_id")
        if message_id:
            await bot.set_essence_msg(message_id)
            logger.info(f"群 {group_id} 密码消息已设为精华")


async def manage_essence_messages(bot, group_id: int, today_date: str, logger) -> bool:
    essence_resp = await bot.get_essence_msg_list(group_id)
    if not essence_resp or essence_resp.get("status") != "ok":
        logger.warning(f"群 {group_id} 获取精华列表失败，仍继续设置")
        return True

    essence_list = essence_resp.get("data", [])
    if not essence_list:
        return True

    today_exists = False
    old_message_ids = []
    for item in essence_list:
        content = _extract_essence_text(item)
        if not content:
            continue
        match = PASSWORD_ESSENCE_RE.search(content)
        if not match:
            continue
        essence_date = match.group(1)
        if essence_date == today_date:
            today_exists = True
            logger.info(f"群 {group_id} 今日密码 ({today_date}) 已是精华，跳过")
            break
        old_message_ids.append(item.get("message_id"))

    if today_exists:
        return False
    for mid in old_message_ids:
        await bot.delete_essence_msg(mid)
        logger.info(f"群 {group_id} 已移除旧精华消息 (message_id: {mid})")
    return True


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


async def fetch_today_password():
    global _cached_date, _cached_passwords

    today = date.today().strftime("%m月%d日")
    if _cached_date == today and _cached_passwords:
        return [_cached_date, _cached_passwords]

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    module_logger.error(f"密码API请求失败: HTTP {resp.status}")
                    return None
                text = await resp.text()
    except Exception as e:
        module_logger.error(f"密码API请求异常: {e}")
        return None

    parsed = parse_password_response(text)
    if parsed:
        _cached_date, _cached_passwords = parsed[0], parsed[1]
    return parsed


def parse_password_response(text: str):
    date_match = re.search(r"更新日期:\s*(\d+月\d+日)", text)
    if not date_match:
        module_logger.error("无法解析更新日期")
        return None
    date_str = date_match.group(1)

    blocks = re.split(r"-{30,}", text)
    passwords = []
    for block in blocks:
        name_match = re.search(r"地图名称:\s*(.+)", block)
        pwd_match = re.search(r"密码:\s*(\d+)", block)
        if name_match and pwd_match:
            passwords.append((name_match.group(1).strip(), pwd_match.group(1)))
    if not passwords:
        module_logger.error("未解析到任何密码信息")
        return None
    return [date_str, passwords]


def format_password_message(date_str: str, passwords: list) -> str:
    lines = [f"三角洲行动 今日密码 ({date_str})", ""]
    for name, pwd in passwords:
        lines.append(f"{name}: {pwd}")
    return "\n".join(lines)
