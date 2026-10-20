"""精华消息管理：推送密码时设置精华并清理旧精华。"""

import json
import re

PASSWORD_ESSENCE_RE = re.compile(r"三角洲行动 今日密码 \((\d+月\d+日)\)")


async def _set_essence(bot, group_id: int, text: str, message_id, logger) -> None:
    """把推送消息设为精华：清理同模块旧精华，避免堆积。"""
    date_match = PASSWORD_ESSENCE_RE.search(text)
    today_date = date_match.group(1) if date_match else ""
    if not today_date:
        # 无法识别日期 → 不做去重/清理（避免误删旧精华），直接设置
        await bot.set_essence_msg(message_id)
        return

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
