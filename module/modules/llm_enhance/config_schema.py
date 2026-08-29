"""LLM 增强配置表单：调试 Prompt。

其余框架级内容（用户信息感知/私信QQ/回复打断）已迁移到 Agent 配置。
"""

SCHEMA = {
    "group_debug": {"type": "group", "label": "调试", "collapsible": True},

    "debug_prompt": {
        "type": "boolean",
        "label": "调试本轮 Prompt",
        "description": "开启后在本轮 LLM 请求前打印完整 prompt 到日志",
        "default": False,
        "group": "group_debug",
    },
}
