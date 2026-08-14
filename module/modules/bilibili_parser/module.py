"""模块声明：B 站视频解析。"""

from app.modules import BaseModule
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "B站视频解析"
    sign = "BilibiliParser"
    description = "自动解析B站视频链接，支持小程序卡片、短链和直链"
    authority_type = "normal"
    subscribe = ("message_group", "message_private")
    default_config = {
        "enable_json_video": True,
        "enable_link_video": True,
        "show_cover": True,
        "max_parse_count": 3,
        "timeout": 10,
    }
    config_schema = SCHEMA

    async def handle(self, event):
        from .service import handle

        await handle(self, event)
