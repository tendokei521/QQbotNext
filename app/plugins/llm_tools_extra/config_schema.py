"""工具扩展模块的配置 Schema（MCP 1 键 + 回应要求 2 键 + 结果长度上限）。

OneBot 工具的启用/允许/禁止/逐工具权限**不在本页配置**：它们都在
「高级工具 → OneBot 工具」里按工具开关与设置（那里有工具清单，不用手打内部工具名）。
这些键仍由本模块拥有，只是有意不在插件配置页渲染（见 `SCHEMA_HIDDEN_KEYS`）。
"""

SCHEMA = {
    "onebot_tools_max_result": {
        "type": "number",
        "label": "结果长度上限",
        "description": "单个 OneBot 工具返回给模型的字符上限",
        "default": 2000,
    },
    "poke_cooldown_seconds": {
        "type": "number",
        "label": "戳一戳冷却(秒)",
        "description": "同一目标两次戳之间的最小间隔",
        "default": 20,
    },
    "mcp_servers": {
        "type": "textarea",
        "label": "外部工具（MCP）服务列表",
        "description": "每项含名称、启动命令与参数；留空表示不用外部工具，格式可参考文档示例",
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
