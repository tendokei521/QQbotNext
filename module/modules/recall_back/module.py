"""模块声明：防撤回。"""

from app.modules import BaseModule, module_hook
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "防撤回模块"
    sign = "RecallBack"
    description = "防撤回模块，监听消息并转发被撤回的消息"
    permission = "everyone"
    category = "通知"
    default_config = {
        "cache_time": 600,
        "enable_group_listen": True,
        "enable_private_listen": True,
        "enable_forward_to_group": True,
        "enable_forward_to_private": True,
        "target_groups": {},
        "target_groups_mode": "all",
        "target_users": {},
        "target_users_mode": "all",
        "db_enable": True,
        "db_max_messages": 5000,
        "db_max_per_group": 200,
        "db_retention_minutes": 60,
        "db_clean_interval_minutes": 60,
    }
    config_schema = SCHEMA

    @module_hook("message_group", order=10)
    @module_hook("message_private", order=10)
    async def handle_message(self, event):
        from .service import handle_message

        await handle_message(self, event)

    @module_hook("notice_group_recall", order=10)
    @module_hook("notice_private_recall", order=10)
    async def handle_recall(self, event):
        from .service import handle_recall

        await handle_recall(self, event)

    async def on_load(self):
        """旧配置迁移 + 启动清理 + 运行中周期清理。"""
        from .service import migrate_legacy_config, on_load

        migrate_legacy_config(self)
        await on_load(self)
