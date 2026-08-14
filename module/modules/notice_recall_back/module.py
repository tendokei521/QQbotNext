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
        "db_enable": True,
        "db_max_messages": 5000,
        "db_retention_minutes": 60,
    }
    config_schema = SCHEMA

    async def handle(self, event):
        from .service import handle

        await handle(self, event)

    async def on_load(self):
        """启动时清理过期/超量的持久化消息缓存。"""
        from .service import on_load

        await on_load(self)
