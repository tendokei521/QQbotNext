"""模块配置 Schema。"""

SCHEMA = {
    "set_essence_mode": {
        "type": "select",
        "label": "响应范围",
        "description": "响应哪些人的消息",
        "default": "auto",
        "options": {
            "nobody": "关闭",
            "admin": "仅管理员",
            "all": "管理员与群员",
        },
    },
    "strict_text": {
        "type": "boolean",
        "label": "严格模式",
        "description": "对触发词进行严格判定",
        "default": False,
    },
}
