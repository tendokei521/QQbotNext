"""模块声明：抖音短视频解析。"""

from app.modules import BaseModule, module_hook

from .config_schema import SCHEMA


class Module(BaseModule):
    name = "抖音视频解析"
    sign = "DouyinParser"
    description = "自动解析抖音分享链接，合并转发文案 + 无水印视频/图集"
    permission = "member"
    category = "消息"
    default_config = {
        "enable_auto_parse": True,
        "enable_link_parse": True,
        "enable_json_parse": True,
        "show_cover": True,
        "max_parse_count": 3,
        "timeout": 10,
        "cookie": "",
        "user_agent": "",
        "group_mode": "all",
        "group_configs": {},
        "is_reply": True,
        "use_forward_msg": True,
        "enable_video_download": True,
        "video_quality": "highest",
        "video_max_mb": 80,
        "video_download_timeout": 60,
        "enable_album_download": True,
        "album_max_images": 9,
        "video_cache_enabled": True,
        "video_cache_ttl_minutes": 720,
        "video_cache_max_mb": 1024,
        "enable_aweme_dedup": True,
        "aweme_dedup_timeout": 60,
    }
    config_schema = SCHEMA

    @module_hook("message_group", order=10)
    @module_hook("message_private", order=10)
    async def handle(self, event):
        from .service import handle

        await handle(self, event)
