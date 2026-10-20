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
    "keyword_symbol_clean": {
        "type": "boolean",
        "label": "符号清洗",
        "description": "开启后判断关键词前先移除消息中的标点、符号与空白，用于命中带符号分隔的关键词（如“哈-哈-哈”可命中“哈哈哈”）",
        "default": False,
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
        "label": "同一表情冷却时长（秒）",
        "description": "同一消息上的同一个 Emoji 在冷却时间内不会重复发送；0 表示不冷却。同一消息上的不同 Emoji 不受影响",
        "default": 60,
        "min": 0,
        "step": 1,
    },
}
