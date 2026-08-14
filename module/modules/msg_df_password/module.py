"""模块声明：三角洲行动今日密码。"""

from app.modules import BaseModule
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "今日密码"
    sign = "Delta_Password"
    description = "三角洲行动今日密码"
    authority_type = "normal"
    subscribe = ("message_group",)
    # 每日 05:00 自动推送（精确到点触发，替代旧 time_core 广播）
    SCHEDULES = {"05:00:00": "daily_push"}
    default_config = {
        "strict_text": True,
        "group_list": {},
        "group_list_mode": "all",
        "push_enable": False,
    }
    config_schema = SCHEMA

    async def handle(self, event):
        from .service import handle

        await handle(self, event)

    async def daily_push(self):
        """定时推送今日密码到启用的群。"""
        from .service import daily_push

        await daily_push(self, self.ctx.bot)
