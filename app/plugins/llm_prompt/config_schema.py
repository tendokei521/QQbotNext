"""提示词组装模块的配置 Schema（8 键）。

精简口径：只保留两类功能——
1. **提示词注入**：行为规则覆盖、参与度；
2. **消息组装方式**：本轮消息格式、时间位置、发送者写法、引用与 @ 的处理、正文标注、消歧说明。

原先分散在 14 个开关里的「拉不拉引用正文 / 要不要昵称 / 脱敏」都收进
`sender_label` 与 `quote_and_at` 两个档位选择里；表情提示、括号清洗、请求落盘
分别迁到「工具扩展」「流式输出」「LLM调试」模块。
"""

SCHEMA = {
    # ---------- 提示词注入 ----------
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
    # ---------- 消息组装方式 ----------
    "user_message_format": {
        "type": "select",
        "label": "消息格式",
        "description": "本轮消息的写法。单行与聊天记录同形，推荐",
        "default": "single_line",
        "options": {"single_line": "单行（推荐）", "multi_line": "多行块"},
    },
    "prompt_time_position": {
        "type": "select",
        "label": "时间位置",
        "description": "当前时间放消息头、消息尾，或者不放",
        "default": "tail",
        "options": {"off": "不注入", "head": "消息头部", "tail": "消息尾部"},
    },
    "sender_label": {
        "type": "select",
        "label": "发送者写法",
        "description": "昵称与 QQ 的呈现方式。脱敏适合不想暴露完整昵称的场景",
        "default": "compact",
        "options": {
            "off": "不标发送者",
            "compact": "昵称(QQ)",
            "verbose": "详细（含私信 QQ）",
            "masked": "昵称脱敏",
        },
    },
    "quote_and_at": {
        "type": "select",
        "label": "引用与 @",
        "description": "关闭=不标；只标关系=只写引用了哪条；带内容=自动取回被引用的话",
        "default": "enrich",
        "options": {"off": "关闭", "mark": "只标关系", "enrich": "带内容（自动取回）"},
    },
    "include_sent": {
        "type": "boolean",
        "label": "标注发送内容",
        "description": "多行块里单独标出正文，方便模型分清元信息",
        "default": True,
        "showIf": {"key": "user_message_format", "value": "multi_line"},
    },
    "auto_disambiguation": {
        "type": "boolean",
        "label": "消歧说明",
        "description": "告诉模型「昵称、时间这些是标注，不是对方说的话」",
        "default": True,
    },
}
