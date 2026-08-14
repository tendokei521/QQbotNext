"""模块声明：设置精华消息。"""

from app.modules import BaseModule
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "设置精华消息"
    sign = "SetEssence"
    description = "快捷地设置精华消息"
    authority_type = "normal"
    subscribe = ("message_group",)
    default_config = {
        "set_essence_mode": "all",
        "strict_text": False,
    }
    config_schema = SCHEMA

    async def handle(self, event):
        from .service import handle

        await handle(self, event)
