"""模块声明：防撤回。"""

from app.modules import BaseModule
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "防撤回模块"
    sign = "RecallBack"
    description = "防撤回模块，监听消息并转发被撤回的消息"
    authority_type = "all"
    subscribe = ("message_group", "message_private", "notice_group_recall", "notice_private_recall")
    default_config = {
        "cache_time": 600,
        "target": "default",
        "target_groups": {},
        "target_groups_mode": "all",
        "target_users": {},
        "target_users_mode": "all",
    }
    config_schema = SCHEMA

    async def handle(self, event):
        from .service import handle

        await handle(self, event)
