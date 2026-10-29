"""LLM 增强配置表单：调试 Prompt。

其余框架级内容（用户信息感知/私信QQ/回复打断）已迁移到 Agent 配置。
"""

SCHEMA = {
    "group_debug": {"type": "group", "label": "调试", "collapsible": True},

    "debug_prompt": {
        "type": "boolean",
        "label": "记录本轮请求内容",
        "description": "开启后把本轮发给模型的完整内容写进日志",
        "default": False,
        "group": "group_debug",
    },
}
