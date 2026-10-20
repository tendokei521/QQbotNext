"""戳一戳回复配置表单。"""

SCHEMA = {
    "enable_follow_poke": {
        "type": "boolean",
        "label": "启用跟随戳一戳",
        "description": "别人戳别人时，Bot 概率跟随戳同一目标",
        "default": True,
    },
    "follow_poke_probability": {
        "type": "number",
        "label": "跟随戳一戳概率",
        "description": "0~1，跟随戳一戳的概率",
        "default": 0.3,
        "min": 0,
        "max": 1,
        "step": 0.05,
    },
    "enable_poke_back": {
        "type": "boolean",
        "label": "启用被戳反戳",
        "description": "Bot 被戳时概率反戳对方",
        "default": True,
    },
    "poke_back_probability": {
        "type": "number",
        "label": "被戳反戳概率",
        "description": "0~1，反戳概率",
        "default": 0.5,
        "min": 0,
        "max": 1,
        "step": 0.05,
    },
    "enable_poke_reply": {
        "type": "boolean",
        "label": "启用被戳回复文本",
        "description": "Bot 被戳时概率回复文本",
        "default": True,
    },
    "poke_reply_text": {
        "type": "string",
        "label": "被戳回复文本内容",
        "description": "被戳后可能回复的内容",
        "default": "别戳了别戳了",
    },
    "poke_reply_probability": {
        "type": "number",
        "label": "被戳回复文本概率",
        "description": "0~1，回复文本概率",
        "default": 0.4,
        "min": 0,
        "max": 1,
        "step": 0.05,
    },
    "poke_interval": {
        "type": "number",
        "label": "戳一戳处理间隔(秒)",
        "description": "同一人/同一群短时间内只处理一次戳一戳，避免刷屏",
        "default": 5,
        "min": 0,
        "max": 3600,
    },
}
