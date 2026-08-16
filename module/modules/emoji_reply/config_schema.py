"""Emoji 回复插件配置表单定义。"""

SCHEMA = {
    "follow_emoji": {
        "type": "boolean",
        "label": "启用跟随Emoji",
        "description": "群消息被添加 Emoji 回应时，Bot 按概率跟随添加同一个 Emoji",
        "default": True,
    },
    "follow_emoji_prob": {
        "type": "number",
        "label": "跟随Emoji概率",
        "description": "跟随附加 Emoji 的概率，0~1 之间",
        "default": 0.5,
        "min": 0,
        "max": 1,
        "step": 0.1,
    },
    "keyword_follow_enable": {
        "type": "boolean",
        "label": "启用关键词跟随Emoji",
        "description": "消息文本命中关键词时，Bot 按概率给该消息添加配置的 Emoji 回应",
        "default": True,
    },
    "keyword_emoji_list": {
        "type": "string_list",
        "label": "关键词Emoji列表",
        "description": "每行一个，格式：关键词:emoji_id，例如 哈哈哈:123",
        "default": [],
        "placeholder": "关键词:emoji_id",
    },
    "keyword_follow_prob": {
        "type": "number",
        "label": "关键词跟随概率",
        "description": "关键词命中后发送 Emoji 回应的概率，0~1 之间",
        "default": 0.5,
        "min": 0,
        "max": 1,
        "step": 0.1,
    },
    "cooldown_seconds": {
        "type": "number",
        "label": "同一消息处理冷却时长（秒）",
        "description": "同一消息 ID 在冷却时间内不会被重复处理；0 表示不冷却",
        "default": 60,
        "min": 0,
        "step": 1,
    },
}
