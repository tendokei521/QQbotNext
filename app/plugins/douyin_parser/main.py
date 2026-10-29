"""模块声明：抖音短视频解析。"""

from app.plugin import BaseModule, module_hook

from .config_schema import SCHEMA


class Module(BaseModule):
    name = "抖音视频解析"
    sign = "DouyinParser"
    description = "贴抖音分享链接，自动转发文案和视频"
    permission = "member"
    category = "消息"
    # 按群响应的模块：同群多账号时可由「单一服务模式」指定唯一负责账号
    supports_single_service = True
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
