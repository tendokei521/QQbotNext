"""模块声明：消息跟随。"""

from app.modules import BaseModule
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "消息跟随模块"
    sign = "Follow"
    description = "消息跟随模块，监听消息并跟随消息"
    authority_type = "normal"
    subscribe = ("message_group",)
    default_config = {
        "follow_msg_count": 3,
        "follow_msg_type": "text",
        "follow_msg_time": 600,
    }
    config_schema = SCHEMA

    async def handle(self, event):
        from .service import handle

        await handle(self, event)
