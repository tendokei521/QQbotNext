"""Emoji 跟随业务逻辑。"""

import random

from app.core.logger import module_logger


async def handle(module, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(f"{module.name}")
    cache = module.ctx.services.cache

    message_id = event.message_id
    follow_emoji = module.config.get("follow_emoji", False)
    follow_emoji_prob = module.config.get("follow_emoji_prob", 0.5)

    if not follow_emoji:
        return
    if not event.emoji_is_add:
        return
    for emoji in event.emoji_likes:
        emoji_id = emoji.get("emoji_id", "")
        if cache.has(f"{message_id}_emoji_{emoji_id}"):
            logger.debug(f"已处理过表情 {emoji_id}")
            continue
        if random.random() < follow_emoji_prob:
            cache.set(f"{message_id}_emoji_{emoji_id}", emoji.get("count", "0"), 60)
            logger.debug(f"跟随表情 {emoji_id}")
            await event.bot.set_msg_emoji_like(message_id=message_id, emoji_id=emoji_id)
