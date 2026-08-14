"""模块声明：群申请处理。"""

from app.modules import BaseModule
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "群申请处理"
    sign = "RequestGroup"
    description = "按关键词 / 账号等级自动处理加群申请"
    authority_type = "normal"
    subscribe = ("request_group",)
    default_config = {
        "example": "",
        "group_keywords_data": "",
    }
    config_schema = SCHEMA

    async def handle(self, event):
        from .service import handle

        await handle(self, event)
