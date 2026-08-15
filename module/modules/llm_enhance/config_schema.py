"""LLM 增强配置表单：防抖 + 用户信息感知。"""

SCHEMA = {
    # ==================== 分组 ====================
    "group_debounce": {"type": "group", "label": "防抖", "collapsible": True},
    "group_context": {"type": "group", "label": "用户信息感知", "collapsible": True},

    # ==================== 防抖 ====================
    "debounce_enable": {
        "type": "boolean",
        "label": "启用防抖",
        "description": "同一会话短时间内多条消息只触发一次 LLM 请求",
        "default": True,
        "group": "group_debounce",
    },
    "debounce_seconds": {
        "type": "number",
        "label": "防抖等待秒数",
        "default": 1.5,
        "min": 0,
        "step": 0.1,
        "group": "group_debounce",
    },
    "merge_messages": {
        "type": "boolean",
        "label": "合并等待期间的消息",
        "default": False,
        "description": "开启后同一会话在防抖窗口内的多条消息会合并为一次 LLM 请求",
        "group": "group_debounce",
    },
    "merge_separator": {
        "type": "string",
        "label": "合并分隔符",
        "default": "\n",
        "group": "group_debounce",
    },

    # ==================== 用户信息感知 ====================
    "context_enable": {
        "type": "boolean",
        "label": "启用用户信息感知",
        "description": "在群聊 LLM 请求前附加发送者/@/引用消息上下文",
        "default": True,
        "group": "group_context",
    },
    "include_sender": {
        "type": "boolean",
        "label": "包含发送者信息",
        "description": "附加发送者昵称和 QQ",
        "default": True,
        "group": "group_context",
    },
    "include_at": {
        "type": "boolean",
        "label": "包含 @ 信息",
        "description": "附加被 @ 的人的昵称和 QQ",
        "default": True,
        "group": "group_context",
    },
    "include_quote": {
        "type": "boolean",
        "label": "包含引用消息",
        "description": "附加引用消息内容、发送者昵称和 QQ",
        "default": True,
        "group": "group_context",
    },
    "fetch_at_nickname": {
        "type": "boolean",
        "label": "拉取 @ 对象昵称",
        "description": "关闭时只附加 QQ，不额外请求群成员信息",
        "default": True,
        "group": "group_context",
    },
    "fetch_quote_content": {
        "type": "boolean",
        "label": "拉取引用消息内容",
        "description": "关闭时不请求原始消息内容",
        "default": True,
        "group": "group_context",
    },
}
