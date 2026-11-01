"""提示词组装模块配置（重构中：3 键）。

- 提示词注入：`behavior_rules_override`、`participation`
- 消息组装：`structured_assembly` 纯布尔开关（结构化格式 / 原样）

旧的 8 键（消息格式/时间位置/发送者写法/引用档位/正文标注/消歧说明）已随重构删除：
结构化格式自带 time/sender/at/reply，说明段由格式自解释。
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
    # ---------- 消息组装方式（唯一开关） ----------
    "structured_assembly": {
        "type": "boolean",
        "label": "结构化消息",
        "description": "用 [time][sender]正文[at][reply_to] 的标记格式；关闭则只发原始正文",
        "default": True,
    },
}
