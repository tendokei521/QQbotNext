"""LLM 用户信息感知配置表单。"""

SCHEMA = {
    "enable": {
        "type": "boolean",
        "label": "启用用户信息感知",
        "description": "在 LLM 请求前附加发送者、@、引用消息上下文",
        "default": True,
    },
    "include_sender": {
        "type": "boolean",
        "label": "包含发送者信息",
        "description": "附加发送者昵称和 QQ",
        "default": True,
    },
    "include_at": {
        "type": "boolean",
        "label": "包含 @ 信息",
        "description": "附加被 @ 的人的昵称和 QQ",
        "default": True,
    },
    "include_quote": {
        "type": "boolean",
        "label": "包含引用消息",
        "description": "附加引用消息内容、发送者昵称和 QQ",
        "default": True,
    },
    "fetch_at_nickname": {
        "type": "boolean",
        "label": "拉取 @ 对象昵称",
        "description": "关闭时只附加 QQ，不额外请求群成员信息",
        "default": True,
    },
    "fetch_quote_content": {
        "type": "boolean",
        "label": "拉取引用消息内容",
        "description": "关闭时不请求原始消息内容",
        "default": True,
    },
}
