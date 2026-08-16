"""模块声明：戳一戳回复。

监听 notice_poke：
- 跟随戳一戳：别人戳别人时，Bot 概率跟随戳同一目标；
- 被戳反戳：Bot 被戳时概率反戳对方；
- 被戳概率回复：Bot 被戳时概率回复文本。
"""

import random
import time

from app.modules import BaseModule, module_hook
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "戳一戳回复"
    sign = "PokeReply"
    description = "跟随戳一戳、被戳反戳、被戳概率回复"
    permission = "everyone"
    subscribe = ("notice_poke",)
    category = "通知"
    default_config = {
        "enable_follow_poke": True,
        "follow_poke_probability": 0.3,
        "enable_poke_back": True,
        "poke_back_probability": 0.5,
        "enable_poke_reply": True,
        "poke_reply_text": "别戳了别戳了",
        "poke_reply_probability": 0.4,
        "poke_interval": 5,
    }
    config_schema = SCHEMA

    @module_hook("notice_poke", order=10)
    async def handle(self, event):
        operator_id = event.user_id or event.operator_id or 0
        target_id = event.target_id or 0
        group_id = event.group_id or 0

        if not operator_id or not target_id:
            return

        # 忽略 Bot 自己发出的戳一戳
        if operator_id in (event.self_id, getattr(event, "bot_id", None)):
            return

        if not self._check_interval(event):
            return

        # 1. 跟随戳一戳：戳的目标不是 Bot 自己
        if target_id != event.self_id:
            if self.config.get("enable_follow_poke", True) and self._hit("follow_poke_probability", 0.3):
                await self._poke(operator_id, target_id, group_id, event)

        # 2/3. 被戳：目标是 Bot
        if target_id == event.self_id:
            if self.config.get("enable_poke_back", True) and self._hit("poke_back_probability", 0.5):
                await self._poke(operator_id, operator_id, group_id, event)

            if self.config.get("enable_poke_reply", True) and self._hit("poke_reply_probability", 0.4):
                text = str(self.config.get("poke_reply_text", "") or "").strip()
                if text:
                    await event.reply(text)

    async def _poke(self, operator_id, target_id, group_id, event):
        try:
            await event.bot.send_poke(
                user_id=target_id,
                group_id=group_id or None,
            )
        except Exception as e:
            from app.core.logger import module_logger

            module_logger.add_info(f"#{self.bot_id}").error(f"[戳一戳] 发送失败: {e}")

    def _hit(self, key: str, default: float) -> bool:
        probability = float(self.config.get(key, default) or 0)
        return random.random() < probability

    def _check_interval(self, event) -> bool:
        interval = float(self.config.get("poke_interval", 5) or 0)
        if interval <= 0:
            return True
        key = f"poke_reply:{event.bot_id}:{event.group_id or 0}:{event.user_id or 0}"
        cache = self.ctx.services.cache
        if cache.has(key):
            return False
        cache.set(key, True, int(interval))
        return True
