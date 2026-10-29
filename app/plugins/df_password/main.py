"""模块声明：三角洲行动今日密码。"""

from app.plugin import BaseModule, module_hook

from .config_schema import SCHEMA


class Module(BaseModule):
    name = "今日密码"
    sign = "Delta_Password"
    description = "查「三角洲行动」今天的密码"
    permission = "member"
    category = "消息"
    # 按群响应的模块：同群多账号时可由「单一服务模式」指定唯一负责账号
    supports_single_service = True
    default_config = {
        "strict_text": True,
        "enable_command": True,
        "cmd_group_mode": "all",
        "command_response_groups": {},
        "enable_cron": False,
        "cron_time": "08:00",
        "cron_group_mode": "all",
        "cron_send_groups": {},
        "push_enable": False,
        "default_site": "kkrb",
        "enable_fallback": True,
        "fallback_site": "tmini",
    }
    config_schema = SCHEMA

    @module_hook("message_group", order=10)
    async def handle(self, event):
        from .service import handle

        await handle(self, event)

    async def on_load(self):
        """旧配置迁移 + 按配置的 cron_time 注册动态定时任务（时间可配置）。

        账号离线时 `register_daily_schedule` 会拒绝注册（离线不跑任务），
        真正注册发生在 `on_online`。
        """
        from .service import migrate_legacy_config, register_schedule

        migrate_legacy_config(self)
        await register_schedule(self)

    async def on_online(self):
        """账号连上：补注册每日推送任务。"""
        from .service import register_schedule

        await register_schedule(self)

    async def on_unload(self):
        """定时任务由 registry 卸载时按前缀统一注销。"""
        pass
