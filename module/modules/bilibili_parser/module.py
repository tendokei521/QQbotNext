"""模块声明：B 站视频解析。"""

from app.modules import BaseModule, module_hook
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "B站视频解析"
    sign = "BilibiliParser"
    description = "自动解析B站视频链接，合并转发视频简介 + 720P 视频"
    permission = "member"
    category = "消息"
    default_config = {
        "enable_auto_parse": True,
        "enable_json_video": True,
        "enable_link_video": True,
        "show_cover": True,
        "max_parse_count": 3,
        "timeout": 10,
        "cookie": "",
        "group_mode": "all",
        "group_configs": {},
        "is_reply": True,
        "enable_bv_dedup": True,
        "bv_dedup_timeout": 60,
        # 合并转发 + 720P 视频
        "use_forward_msg": True,
        "enable_video_download": True,
        "video_quality": "720",
        "video_max_mb": 80,
        "video_download_timeout": 60,
        "video_cache_enabled": True,
        "video_cache_ttl_minutes": 720,
        "video_cache_max_mb": 1024,
    }
    config_schema = SCHEMA

    @module_hook("message_group", order=10)
    @module_hook("message_private", order=10)
    async def handle(self, event):
        from .service import handle

        await handle(self, event)
