"""LLM 防抖配置表单。"""

SCHEMA = {
    "enable": {
        "type": "boolean",
        "label": "启用防抖",
        "default": True,
    },
    "debounce_seconds": {
        "type": "number",
        "label": "防抖等待秒数",
        "default": 1.5,
        "min": 0,
        "step": 0.1,
    },
    "merge_messages": {
        "type": "boolean",
        "label": "合并等待期间的消息",
        "default": False,
        "description": "开启后，同一会话在防抖窗口内的多条消息会合并为一次 LLM 请求",
    },
    "merge_separator": {
        "type": "string",
        "label": "合并分隔符",
        "default": "\n",
    },
}
