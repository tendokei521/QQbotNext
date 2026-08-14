"""设置 / 移除精华消息业务逻辑。"""

import re

from app.core.logger import module_logger

ESSENCE_PATTERNS = [
    re.compile(r"设置精华消息", re.IGNORECASE),
    re.compile(r"设精", re.IGNORECASE),
    re.compile(r"删除精华消息", re.IGNORECASE),
    re.compile(r"移除精华消息", re.IGNORECASE),
    re.compile(r"寸止", re.IGNORECASE),
]


async def handle(module, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(f"{module.name}")

    message = event.message
    config = module.config
    set_essence_mode = config.get("set_essence_mode", "all")
    strict_text = config.get("strict_text", True)

    if set_essence_mode == "nobody":
        return
    if set_essence_mode == "admin":
        if not (event.authority_level or 0) > 2:
            return

    reply_id = 0
    target_text = ""
    for seg in message:
        if seg.type == "text":
            target_text += seg.data.get("text", "")
        elif seg.type == "reply":
            reply_id = seg.data.get("id")
    if not reply_id or not target_text:
        return

    text_type = get_text_type(target_text, strict_text)
    if not text_type:
        return
    if text_type == "set":
        logger.info(f"设置精华消息: {reply_id}")
        await event.bot.set_essence_msg(message_id=reply_id)
    elif text_type == "delete":
        logger.info(f"删除精华消息: {reply_id}")
        await event.bot.delete_essence_msg(message_id=reply_id)


def get_text_type(message: str, strict_text: bool = True) -> str:
    if strict_text:
        if re.search(r"删除精华消息|移除精华消息", message, re.IGNORECASE):
            return "delete"
        if re.search(r"设置精华消息", message, re.IGNORECASE):
            return "set"
    else:
        if re.search(r"删除精华消息|移除精华消息|寸止", message, re.IGNORECASE):
            return "delete"
        if re.search(r"设置精华消息|设精", message, re.IGNORECASE):
            return "set"
    return ""
