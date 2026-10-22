"""记忆与知识库模块的配置 Schema 常量。

这两个模块覆盖的键分别来自 agent 的「长期记忆」组（17）与「知识库」组（3）。
放在 service 层是为了让 module.py 保持"只写声明"。
"""

MEMORY_SCHEMA = {
    "memory_enable": {
        "type": "boolean",
        "label": "启用长期记忆",
        "description": "跨会话记住用户说过的偏好/事实，并在后续对话里召回",
        "default": False,
    },
    "memory_private_enable": {
        "type": "boolean",
        "label": "私聊记忆",
        "description": "私聊里是否记录与召回",
        "default": True,
    },
    "memory_group_enable": {
        "type": "boolean",
        "label": "群聊记忆",
        "description": "群聊里是否记录与召回",
        "default": True,
    },
    "memory_recall_max": {
        "type": "number",
        "label": "召回条数上限",
        "description": "一次注入最多几条记忆",
        "default": 8,
    },
    "memory_recall_max_chars": {
        "type": "number",
        "label": "召回字符预算",
        "description": "记忆块的总字符上限",
        "default": 1200,
    },
    "memory_save_deterministic": {
        "type": "boolean",
        "label": "确定性兜底写入",
        "description": "用户明确说「记住…」时不等模型，直接落库",
        "default": True,
    },
    "memory_extract_enable": {
        "type": "boolean",
        "label": "自动蒸馏",
        "description": "定期用模型从对话里抽取值得记的事实",
        "default": False,
    },
    "memory_extract_interval_min": {
        "type": "number",
        "label": "蒸馏间隔(分钟)",
        "description": "自动蒸馏的最短间隔",
        "default": 30,
    },
    "memory_max_per_owner": {
        "type": "number",
        "label": "每人记忆上限",
        "description": "同一 owner 保留的记忆条数上限",
        "default": 200,
    },
    "memory_user_cross_group": {
        "type": "boolean",
        "label": "跨群共享用户记忆",
        "description": "同一个人在不同群里共用一份记忆",
        "default": False,
    },
    "memory_audit_enable": {
        "type": "boolean",
        "label": "记忆审计",
        "description": "记录每次召回/写入的审计日志",
        "default": False,
    },
    "memory_audit_inject": {
        "type": "boolean",
        "label": "审计注入",
        "description": "把审计摘要一并注入提示（调试用）",
        "default": False,
    },
    "memory_min_confidence": {
        "type": "number",
        "label": "最低可信度",
        "description": "低于该可信度的记忆不注入",
        "default": 0.5,
    },
    "memory_inject_hedge": {
        "type": "boolean",
        "label": "注入时加不确定性说明",
        "description": "提醒模型「这些是记忆，可能过时」",
        "default": True,
    },
    "memory_max_age_days": {
        "type": "number",
        "label": "记忆最长保留(天)",
        "description": "超过该天数的记忆不再注入；0=不限",
        "default": 0,
    },
    "memory_on_reset": {
        "type": "select",
        "label": "重置会话时的处理",
        "description": "/reset 时怎么处理该会话的记忆",
        "default": "keep",
        "options": {"keep": "保留", "archive": "归档", "clear": "清空"},
    },
    "memory_upgrade_saved_only": {
        "type": "boolean",
        "label": "只升级已保存记忆",
        "description": "升级时只迁移已确认的记忆，跳过待审条目",
        "default": True,
    },
}

KNOWLEDGE_SCHEMA = {
    "knowledge_enable": {
        "type": "boolean",
        "label": "启用知识库",
        "description": "开启后可向知识库写入/检索资料",
        "default": False,
    },
    "knowledge_embedding_model_id": {
        "type": "resource",
        "resource": "provider_model",
        "label": "Embedding 模型",
        "description": "知识库向量化用的模型实例",
        "default": "",
        "filters": {"provider_type": "embedding"},
    },
    "knowledge_recall_limit": {
        "type": "number",
        "label": "检索条数上限",
        "description": "一次检索最多返回几段",
        "default": 5,
    },
}
