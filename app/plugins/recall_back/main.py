"""模块声明：防撤回。"""

from app.plugin import BaseModule, module_hook

from .config_schema import SCHEMA


class Module(BaseModule):
    name = "防撤回模块"
    sign = "RecallBack"
    description = "防撤回模块，监听消息并转发被撤回的消息"
    permission = "everyone"
    category = "通知"
    # 按群响应的模块：同群多账号时可由「单一服务模式」指定唯一负责账号
    supports_single_service = True
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
        """旧配置迁移 + 启动清理（**不武装计时器**，见 on_online）。"""
        from .service import migrate_legacy_config, on_load

        migrate_legacy_config(self)
        await on_load(self)

    async def on_online(self):
        """账号连上/模块启用：开始周期清理（禁用/断开时由 on_offline 停掉）。"""
        from .service import on_online

        await on_online(self)

    async def on_offline(self):
        """账号断开/模块禁用：停掉周期清理任务。"""
        from .service import on_offline

        await on_offline(self)
