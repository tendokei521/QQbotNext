"""模块配置 Schema。"""

SCHEMA = {
    "follow_emoji": {
        "type": "boolean",
        "label": "跟随Emoji",
        "description": "有Emoji回复时跟随",
        "default": True,
    },
    "follow_emoji_prob": {
        "type": "number",
        "label": "跟随概率",
        "description": "跟随Emoji的概率",
        "default": 0.5,
        "min": 0.5,
        "max": 1.0,
        "step": 0.1,
    },
}
