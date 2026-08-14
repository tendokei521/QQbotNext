"""模块配置 Schema。"""

SCHEMA = {
    "enable_json_video": {
        "type": "boolean",
        "label": "解析小程序卡片",
        "description": "自动解析QQ内分享的B站小程序卡片",
        "default": True,
    },
    "enable_link_video": {
        "type": "boolean",
        "label": "解析链接",
        "description": "自动解析b23.tv短链和bilibili.com直链",
        "default": True,
    },
    "show_cover": {
        "type": "boolean",
        "label": "显示视频封面",
        "description": "在回复中显示视频封面图片",
        "default": True,
    },
    "max_parse_count": {
        "type": "integer",
        "label": "最大解析数量",
        "description": "单次消息中最多解析的视频数量",
        "default": 3,
        "min": 1,
        "max": 10,
    },
    "timeout": {
        "type": "integer",
        "label": "请求超时时长",
        "description": "API请求超时时间(秒)",
        "default": 10,
        "min": 5,
        "max": 30,
    },
}
