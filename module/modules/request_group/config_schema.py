"""模块配置 Schema。"""

SCHEMA = {
    "example": {
        "type": "textarea",
        "label": "示例",
        "description": "配置示例",
        "default": "",
        "placeholder": "配置示例",
        "rows": 5,
    },
    "group_keywords_data": {
        "type": "textarea",
        "label": "群关键词配置",
        "description": "群关键词配置，格式为 JSON 字符串",
        "default": "",
        "placeholder": "请输入群关键词配置",
        "rows": 10,
    },
}
