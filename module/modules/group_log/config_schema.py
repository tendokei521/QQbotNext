"""群聊记录模块配置表单。"""

SCHEMA = {
    "group_log_enable": {
        "type": "boolean",
        "label": "启用群聊记录",
        "description": "持续记录群聊可见事件（消息/表情/戳/撤回/我的发言），供 LLM 作为环境背景。"
                       "关闭后不再记录新事件，已有的记录仍留在本地。",
        "default": True,
    },
    "retention_count": {
        "type": "number",
        "label": "单群保留条数",
        "description": "每个群本地最多保留多少条记录（超出后从最旧的开始丢弃）。这是保留口径，"
                       "一次请求实际携带多少由 Agent 配置里的组装窗口决定。",
        "default": 2000,
        "min": 100,
        "step": 100,
    },
    "retention_hours": {
        "type": "number",
        "label": "保留时长（小时）",
        "description": "超过该时长的记录不再保留。0 表示不限时长（仅按条数淘汰）。",
        "default": 24,
        "min": 0,
        "step": 1,
    },
    "strip_directives": {
        "type": "boolean",
        "label": "剥离控制标记",
        "description": "记录前剥离正文里的 [reply] / [@QQ] / <type=...> 等控制形态，"
                       "避免群成员用这些文字影响模型行为",
        "default": True,
    },
}
