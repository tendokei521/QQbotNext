"""模块声明：群打卡。"""

from app.modules import BaseModule
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "群打卡"
    sign = "TimeSignInModule"
    description = "每天0点0分自动群打卡"
    authority_type = "admin"
    subscribe = ("message_group",)
    # 每日 00:00 自动打卡（精确到点触发，替代旧 time_core 广播）
    SCHEDULES = {"00:00:00": "daily_sign_in"}
    default_config = {
        "priority_groups": {},
        "priority_groups_mode": "all",
    }
    config_schema = SCHEMA

    async def handle(self, event):
        from .service import handle

        await handle(self, event)

    async def daily_sign_in(self):
        """定时任务：全群打卡。"""
        from .service import daily_sign_in

        await daily_sign_in(self, self.ctx.bot)
