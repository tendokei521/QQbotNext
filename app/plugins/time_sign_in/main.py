"""模块声明：群打卡。"""

from app.plugin import BaseModule, module_hook

from .config_schema import SCHEMA


class Module(BaseModule):
    name = "群打卡"
    sign = "TimeSignInModule"
    description = "每日自动群打卡 + #打卡/#全群打卡 指令"
    # 框架级权限过滤：仅 Bot 拥有者可执行
    permission = "owner"
    category = "消息"
    # 按群响应的模块：同群多账号时可由「单一服务模式」指定唯一负责账号
    supports_single_service = True
    default_config = {
        "enable_signin_command": True,
        "enable_all_signin_command": True,
        "enable_silence_signin": True,
        "enable_ignore_group_check": False,
        "enable_daily_auto_signin": True,
        "daily_signin_time": "00:00",
        "group_mode": "all",
        "group_configs": {},
        "enable_custom_commands": False,
        "custom_commands": [],
        "enable_success_notify": False,
        "notify_groups": {},
        "notify_friends": {},
    }
    config_schema = SCHEMA

    @module_hook("message_group", order=10)
    async def handle(self, event):
        from .service import handle

        await handle(self, event)

    async def on_load(self):
        """旧配置迁移 + 按配置的 daily_signin_time 注册动态定时任务（时间可配置）。"""
        from .service import migrate_legacy_config, register_schedule

        migrate_legacy_config(self)
        await register_schedule(self)

    async def on_unload(self):
        """定时任务由 registry 卸载时按前缀统一注销。"""
        pass
