"""模块声明：自回复模块。"""

from app.modules import BaseModule
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "自回复模块"
    sign = "KeywordReply"
    description = "提取群消息关键词如果包含就执行操作"
    authority_type = "normal"
    subscribe = ("message_group",)
    default_config = {
        "customtext": [],
    }
    config_schema = SCHEMA

    async def handle(self, event):
        from .service import handle

        await handle(self, event)
