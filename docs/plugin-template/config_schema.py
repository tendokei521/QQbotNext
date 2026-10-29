"""示例配置 Schema（键与 main.py 的 default_config 一一对应）。"""

SCHEMA = {
    "enable": {
        "type": "boolean",
        "label": "启用插件",
        "description": "关闭后不会响应任何消息",
        "default": False,
    },
    "keywords": {
        "type": "string_list",
        "label": "触发关键词",
        "description": "消息里出现任一个就回复",
        "default": ["hello"],
    },
}
