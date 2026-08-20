"""LLM 增强配置表单：防抖 + 调试。

用户信息感知（发送者/提到/引用/正文/时间）与回复打断已迁移到 Agent 配置
（app/llm/config_schema.py -> SCHEMA，读取走 AgentConfig），此处不再保留。
"""

SCHEMA = {
    # ==================== 分组 ====================
    "group_debounce": {"type": "group", "label": "防抖", "collapsible": True},
    "group_debug": {"type": "group", "label": "调试", "collapsible": True},

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

    # ==================== 调试 ====================
    "debug_prompt": {
        "type": "boolean",
        "label": "调试本轮 Prompt",
        "description": "开启后在本轮 LLM 请求前打印完整 prompt 到日志",
        "default": False,
        "group": "group_debug",
    },
}
