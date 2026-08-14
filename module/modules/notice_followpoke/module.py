"""模块声明：戳一戳跟随。"""

from app.modules import BaseModule
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "戳一戳跟随"
    sign = "FollowPoke"
    description = "跟随群友的戳一戳或反戳"
    authority_type = "normal"
    subscribe = ("notice_poke",)
    default_config = {
        "follow_poke": True,
        "follow_poke_prob": 0.5,
        "poke_back": True,
        "poke_prob": 1.0,
        "poke_text": "喵？",
        "poke_text_prob": 0.5,
    }
    config_schema = SCHEMA

    async def handle(self, event):
        from .service import handle

        await handle(self, event)
