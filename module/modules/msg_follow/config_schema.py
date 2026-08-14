"""模块配置 Schema。"""

SCHEMA = {
    "follow_msg_count": {
        "type": "number",
        "label": "跟随消息数量",
        "description": "跟随的消息起始数量，默认3条消息",
        "default": 3,
        "placeholder": "输入跟随的消息起始数量",
    },
    "follow_msg_type": {
        "type": "select",
        "label": "消息匹配方式",
        "description": "选择相同消息的匹配方式",
        "default": "text",
        "options": {
            "text": "仅文本",
            "image": "仅图片",
            "text_image": "文字或图片(单选)",
        },
    },
    "follow_msg_time": {
        "type": "number",
        "label": "跟随消息时间",
        "description": "跟随消息的最大时间间隔，默认单位秒",
        "default": 60,
        "placeholder": "满足跟随情况的时间间隔，单位秒",
    },
}
