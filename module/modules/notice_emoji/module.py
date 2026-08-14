"""模块声明：Emoji 跟随。"""

from app.modules import BaseModule
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "Emoji跟随"
    sign = "FollowEmoji"
    description = "跟随群友的 Emoji 回复"
    authority_type = "normal"
    subscribe = ("notice_group_emoji",)
    default_config = {
        "follow_emoji": True,
        "follow_emoji_prob": 0.5,
    }
    config_schema = SCHEMA

    async def handle(self, event):
        from .service import handle

        await handle(self, event)
