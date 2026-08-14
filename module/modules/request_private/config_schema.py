"""模块配置 Schema。"""

SCHEMA = {
    "private_keywords_data": {
        "type": "textarea",
        "label": "私聊申请关键词配置",
        "description": "私聊申请关键词配置，格式为 JSON 字符串",
        "default": "",
        "placeholder": "请输入私聊申请关键词配置",
        "rows": 10,
    },
}
