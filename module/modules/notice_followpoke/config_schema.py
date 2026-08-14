"""模块配置 Schema。"""

SCHEMA = {
    "follow_poke": {
        "type": "boolean",
        "label": "跟随戳一戳",
        "description": "有戳一戳时跟随",
        "default": True,
    },
    "follow_poke_prob": {
        "type": "number",
        "label": "跟随概率",
        "description": "跟随戳一戳的概率",
        "default": 0.5,
        "min": 0.5,
        "max": 1.0,
        "step": 0.1,
    },
    "poke_back": {
        "type": "boolean",
        "label": "反戳",
        "description": "被戳一戳时回击",
        "default": True,
    },
    "poke_prob": {
        "type": "number",
        "label": "反戳概率",
        "description": "回击的概率",
        "default": 1.0,
        "min": 0.5,
        "max": 1.0,
        "step": 0.1,
    },
    "poke_text": {
        "type": "text",
        "label": "被戳文本",
        "description": "被戳时发送文本",
        "default": "喵？",
    },
    "poke_text_prob": {
        "type": "number",
        "label": "被戳文本概率",
        "description": "被戳时发送文本的概率",
        "default": 0.5,
        "min": 0.5,
        "max": 1.0,
        "step": 0.1,
    },
}
