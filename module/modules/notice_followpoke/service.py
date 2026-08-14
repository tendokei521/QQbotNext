"""戳一戳跟随/反戳业务逻辑。"""

import random

from app.core.logger import module_logger


async def handle(module, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(f"{module.name}")
    cache = module.ctx.services.cache

    user_id = event.user_id
    self_id = event.self_id
    group_id = event.group_id
    target_id = event.target_id

    if user_id == target_id:
        user_id = self_id
    if user_id == self_id:
        return

    # 10s 内同一 (user, target) 只处理一次
    if cache.has(f"{user_id}_{target_id}"):
        return
    cache.set(f"{user_id}_{target_id}", 1, 10)

    if target_id == self_id:
        # 自己被戳：反戳 / 文本回复
        poke_back = module.config.get("poke_back", False)
        poke_prob = module.config.get("poke_prob", 0.5)
        poke_text = module.config.get("poke_text", "")
        poke_text_prob = module.config.get("poke_text_prob", 0.5)

        if poke_back and random.random() < poke_prob:
            await event.bot.send_poke(user_id=user_id, group_id=group_id, target_id=user_id)
            logger.info(f"戳一戳反击 {user_id}")
        if poke_text and group_id and random.random() < poke_text_prob:
            await event.bot.send_msg(message_type="group", message=poke_text, group_id=group_id)
            logger.info(f"戳一戳回复: {poke_text}")
    else:
        # 其他人被戳：跟随
        follow_poke = module.config.get("follow_poke", False)
        follow_poke_prob = module.config.get("follow_poke_prob", 0.5)
        if follow_poke and random.random() < follow_poke_prob:
            await event.bot.send_poke(user_id=user_id, group_id=group_id, target_id=target_id)
            logger.info(f"戳一戳跟随 {target_id}")
