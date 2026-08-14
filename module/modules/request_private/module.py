"""模块声明：私聊申请处理。"""

from app.modules import BaseModule
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "私聊申请处理"
    sign = "RequestPrivate"
    description = "按关键词 / 账号等级自动处理好友申请"
    authority_type = "normal"
    subscribe = ("request_private",)
    default_config = {
        "example": "",
        "private_keywords_data": "",
    }
    config_schema = SCHEMA

    async def handle(self, event):
        from .service import handle

        await handle(self, event)
