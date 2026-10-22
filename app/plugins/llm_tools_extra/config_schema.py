"""工具扩展模块的配置 Schema（OneBot 6 键 + MCP 1 键 + 回应要求 2 键）。"""

SCHEMA = {
    "onebot_tools_enable": {
        "type": "boolean",
        "label": "启用 OneBot 工具",
        "description": "把 OneBot API 暴露给模型（发消息/查群/撤回等），按下面的名单过滤",
        "default": False,
    },
    "onebot_tools_allowed": {
        "type": "string_list",
        "label": "允许的工具",
        "description": "留空=按内置权限表放行；填写则只放行这些（工具名）",
        "default": [],
    },
    "onebot_tools_denied": {
        "type": "string_list",
        "label": "禁止的工具",
        "description": "无论内置策略如何都禁止（工具名）",
        "default": [],
    },
    "onebot_tools_max_result": {
        "type": "number",
        "label": "结果长度上限",
        "description": "单个 OneBot 工具返回给模型的字符上限",
        "default": 2000,
    },
    "onebot_tools_debug": {
        "type": "boolean",
        "label": "调试日志",
        "description": "记录每次 OneBot 工具调用的 action 与参数",
        "default": False,
    },
    "poke_cooldown_seconds": {
        "type": "number",
        "label": "戳一戳冷却(秒)",
        "description": "同一目标两次戳之间的最小间隔",
        "default": 20,
    },
    "mcp_servers": {
        "type": "textarea",
        "label": "MCP servers",
        "description": "stdio server 配置（JSON 数组，每项含 name/command/args）",
        "default": "[]",
    },
    "tool_result_directive_enable": {
        "type": "boolean",
        "label": "启用工具结果回应要求",
        "description": "在工具结果末尾追加一句「用自己的口吻回应」，压住复述倾向",
        "default": True,
    },
    "tool_result_directive": {
        "type": "textarea",
        "label": "回应要求文案",
        "description": "留空用内置文案；填写后整段替换",
        "default": "",
    },
}
