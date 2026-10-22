"""组装策略模块的配置 Schema（原 agent「行为规则 / 组装档位」组，共 10 键）。"""

SCHEMA = {
    "assembly_profile": {
        "type": "select",
        "label": "组装档位",
        "description": "blocks=分块（默认）；qq_agent=复合人格/行为/表情段；legacy=仅旧格式说明",
        "default": "blocks",
        "options": {"blocks": "分块（推荐）", "qq_agent": "QQ Agent 复合档", "legacy": "旧格式"},
    },
    "behavior_rules_enable": {
        "type": "boolean",
        "label": "注入行为规则段",
        "description": "只在 qq_agent 档生效：给出「在这个群里怎么说话」的行为约束",
        "default": False,
    },
    "behavior_rules_override": {
        "type": "textarea",
        "label": "覆盖行为规则",
        "description": "留空则用内置规则；填写后整段替换",
        "default": "",
    },
    "participation": {
        "type": "select",
        "label": "参与度",
        "description": "影响行为规则段的语气与主动程度",
        "default": "normal",
        "options": {"low": "低（少插话）", "normal": "正常", "high": "高（多参与）"},
    },
    "emoji_react_level": {
        "type": "number",
        "label": "贴表情活跃度",
        "description": "0=不贴；越大越频繁（会注入表情词表提示）",
        "default": 0,
    },
    "user_message_format": {
        "type": "select",
        "label": "本轮消息格式",
        "description": "用户消息的渲染形态",
        "default": "default",
        "options": {
            "default": "默认（多行）",
            "single_line": "单行",
            "labeled": "带标签",
        },
    },
    "prompt_time_position": {
        "type": "select",
        "label": "时间行位置",
        "description": "当前时间放在用户消息头部还是尾部",
        "default": "user_head",
        "options": {"user_head": "消息头部", "user_tail": "消息尾部", "off": "不注入"},
    },
    "system_prompt_style": {
        "type": "select",
        "label": "消息组装风格",
        "description": "blocks=多个 system 块；single=折成一整条（对齐第三方形态）",
        "default": "blocks",
        "options": {"blocks": "分块（默认）", "single": "折成一条"},
    },
    "prompt_dump_enable": {
        "type": "boolean",
        "label": "请求落盘（对账）",
        "description": "每轮最终 messages 落盘到 data/llm/<账号>/prompt_dump.jsonl",
        "default": False,
    },
    "prompt_dump_max_entries": {
        "type": "number",
        "label": "落盘保留条数",
        "description": "滚动文件最多保留多少轮请求（从最旧开始丢）",
        "default": 20,
    },
}
