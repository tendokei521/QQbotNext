"""上下文增强模块的配置 Schema（原 agent「会话管理 / 指代」组，共 10 键）。"""

SCHEMA = {
    "context_expand_enable": {
        "type": "boolean",
        "label": "未展开标记与按需展开",
        "description": "开启后聊天记录里取不到的内容会标【未展开:…】，并提供 expand_* 工具按需取回",
        "default": True,
    },
    "referent_resolve_enable": {
        "type": "boolean",
        "label": "指代消解",
        "description": "维护会话焦点表，让「那个样子/上一条」这类回指能定位到具体消息",
        "default": True,
    },
    "referent_prefetch_enable": {
        "type": "boolean",
        "label": "指代预取",
        "description": "判定出明确指向时，请求前直接把内容取回来（不等模型决定要不要调工具）",
        "default": True,
    },
    "referent_ambiguous_policy": {
        "type": "select",
        "label": "指代不明时的处理",
        "description": "候选不止一个时的策略",
        "default": "fetch_all",
        "options": {
            "fetch_all": "全部取回（推荐）",
            "focus_first": "只取焦点项",
            "ask": "让模型先问",
        },
    },
    "referent_prefetch_max": {
        "type": "number",
        "label": "预取候选上限",
        "description": "一轮最多预取几个候选（预算保护）",
        "default": 2,
    },
    "referent_focus_ttl": {
        "type": "number",
        "label": "焦点存活时长(秒)",
        "description": "焦点项多久没被关注就从焦点表淘汰",
        "default": 1800,
    },
    "referent_focus_max": {
        "type": "number",
        "label": "焦点项上限",
        "description": "每会话保留的焦点项数量上限",
        "default": 12,
    },
    "referent_prompt_enable": {
        "type": "boolean",
        "label": "指代提示行",
        "description": "是否把「当前对话焦点」注入请求（按本轮可用能力裁剪）",
        "default": True,
    },
    "history_auto_qq_min_local": {
        "type": "number",
        "label": "聊天记录工具补拉阈值",
        "description": "get_chat_history 在本地记录少于该条数时自动补拉 QQ 记录；0=总是补拉",
        "default": 4,
    },
    "history_cross_query_enable": {
        "type": "boolean",
        "label": "允许私聊跨会话查群记录",
        "description": "私聊里允许查询「发起人自己也是成员」的群历史（结果只回给发起人）",
        "default": True,
    },
}
