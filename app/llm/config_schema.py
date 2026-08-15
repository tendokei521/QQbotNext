"""框架级 Agent 配置 Schema（WebUI /agent 页渲染表单）。"""

SCHEMA = {
    # ==================== 分组定义 ====================
    "group_api": {"type": "group", "label": "API 设置", "collapsible": True},
    "group_model": {"type": "group", "label": "模型参数", "collapsible": True},
    "group_switch": {"type": "group", "label": "功能开关", "collapsible": True},
    "group_session": {"type": "group", "label": "会话管理", "collapsible": True},
    "group_trigger": {"type": "group", "label": "触发设置", "collapsible": True},
    "group_proactive": {"type": "group", "label": "主动消息", "collapsible": True},
    "group_schedule": {"type": "group", "label": "定时任务", "collapsible": True},
    "group_permission": {"type": "group", "label": "权限", "collapsible": True},

    # ==================== 配置项 ====================
    "api_key": {
        "type": "password", "label": "API密钥", "description": "OpenAI API密钥或其他兼容API密钥（多 key 换行/逗号分隔，自动轮换）",
        "default": "", "placeholder": "sk-...", "group": "group_api",
    },
    "api_base": {
        "type": "text", "label": "API基础URL", "description": "API接口地址，支持OpenAI兼容格式",
        "default": "https://api.deepseek.com", "placeholder": "https://api.deepseek.com", "group": "group_api",
    },
    "provider": {
        "type": "select", "label": "Provider 类型", "description": "LLM 后端实现（当前内置 openai 兼容）",
        "default": "openai", "group": "group_api",
        "options": {"openai": "OpenAI 兼容（DeepSeek/中转等）"},
    },
    "retry_attempts": {
        "type": "number", "label": "最大重试次数", "description": "限流/5xx/网络错误时重试（认证错误不重试）",
        "default": 3, "min": 1, "max": 6, "group": "group_api",
    },
    "model": {
        "type": "text", "label": "模型名称", "description": "使用的LLM模型",
        "default": "deepseek-chat", "placeholder": "deepseek-chat", "group": "group_model",
    },
    "max_tokens": {
        "type": "number", "label": "最大输出Token", "description": "模型回复的最大token数",
        "default": 1024, "min": 50, "max": 4096, "group": "group_model",
    },
    "temperature": {
        "type": "number", "label": "温度", "description": "回复的随机性，0-1之间",
        "default": 0.7, "min": 0.0, "max": 2.0, "step": 0.1, "group": "group_model",
    },

    "group_enable": {
        "type": "boolean", "label": "群回复启用", "description": "是否启用群LLM功能",
        "default": False, "group": "group_switch",
    },
    "private_enable": {
        "type": "boolean", "label": "私信回复启用", "description": "是否启用私信LLM功能",
        "default": True, "group": "group_switch",
    },
    "reply_cooldown": {
        "type": "number", "label": "回复冷却时间(秒)", "description": "群聊时Bot回复后多少秒内不再响应新消息",
        "default": 5, "min": 1, "max": 30, "group": "group_switch",
    },
    "stream_output": {
        "type": "boolean", "label": "流式输出", "description": "启用后 LLM 回复按句子流式发送（支持带 tools 的工具调用）",
        "default": False, "group": "group_switch",
    },
    "stream_sentence_max_length": {
        "type": "number", "label": "流式单句最大长度", "description": "流式输出时每条消息的最大字符数",
        "default": 50, "min": 10, "max": 200, "group": "group_switch",
    },

    "system_prompt": {
        "type": "textarea", "label": "系统提示词", "description": "设定AI角色的系统提示词",
        "default": "你是一个友好的助手。", "placeholder": "你是一个友好的助手...", "group": "group_session",
    },
    "session_timeout": {
        "type": "number", "label": "会话超时时间(秒)", "description": "无消息后多久结束会话",
        "default": 60, "min": 30, "max": 300, "group": "group_session",
    },
    "history_rounds": {
        "type": "number", "label": "历史对话轮数", "description": "保留多少轮对话历史",
        "default": 50, "min": 5, "max": 100, "group": "group_session",
    },
    "max_message_length": {
        "type": "number", "label": "消息最大长度", "description": "单条消息的最大字符数",
        "default": 50, "min": 20, "max": 100, "group": "group_session",
    },
    "include_pre_history": {
        "type": "boolean", "label": "包含群聊会话前历史", "description": "群聊时是否将会话开始前的群消息作为背景信息提供给LLM（不计入会话历史）",
        "default": False, "group": "group_session",
    },
    "include_private_pre_history": {
        "type": "select", "label": "包含私信会话前历史", "description": "私信时是否将会话开始前的私信消息提供给LLM",
        "default": "default",
        "options": {"default": "不加载", "history": "加载为背景消息", "load": "加载为会话历史"},
        "group": "group_session",
    },

    "trigger_at": {
        "type": "boolean", "label": "@触发", "description": "被@时启动对话",
        "default": True, "group": "group_trigger",
    },
    "trigger_keyword": {
        "type": "string_list", "label": "触发关键词", "description": "包含此关键词时触发（可选）",
        "default": [], "placeholder": "例如：AI、助手", "group": "group_trigger",
    },

    # ==================== 权限 ====================
    "authority_type": {
        "type": "select", "label": "权限等级", "description": "可响应的事件权限（strict=仅管理员及以上）",
        "default": "strict", "group": "group_permission",
        "options": {
            "all": "所有人",
            "normal": "普通用户及以上",
            "strict": "仅管理员及以上",
            "admin": "仅 Bot 拥有者",
        },
    },

    # ==================== 主动消息 ====================
    "proactive_friend_enable": {
        "type": "boolean", "label": "私聊主动发言", "description": "对下方私聊会话按随机间隔主动发言",
        "default": False, "group": "group_proactive",
    },
    "proactive_group_enable": {
        "type": "boolean", "label": "群聊主动发言", "description": "对下方群聊会话在沉默后主动开口",
        "default": False, "group": "group_proactive",
    },
    "proactive_friend_sessions": {
        "type": "string_list", "label": "主动私聊会话（QQ号）", "description": "每行一个 QQ 号",
        "default": [], "group": "group_proactive",
    },
    "proactive_group_sessions": {
        "type": "string_list", "label": "主动群聊会话（群号）", "description": "每行一个群号",
        "default": [], "group": "group_proactive",
    },
    "proactive_min_interval_minutes": {
        "type": "number", "label": "私聊最小间隔(分钟)", "description": "主动发言的随机间隔下限",
        "default": 30, "min": 1, "max": 1440, "group": "group_proactive",
    },
    "proactive_max_interval_minutes": {
        "type": "number", "label": "私聊最大间隔(分钟)", "description": "主动发言的随机间隔上限",
        "default": 900, "min": 1, "max": 2880, "group": "group_proactive",
    },
    "proactive_max_unanswered": {
        "type": "number", "label": "未回复上限", "description": "连续无人回复达到此数即停止主动发言（0=不限）",
        "default": 3, "min": 0, "max": 20, "group": "group_proactive",
    },
    "proactive_quiet_hours_start": {
        "type": "number", "label": "免打扰开始(时)", "description": "免打扰时段开始小时（0-23）",
        "default": 1, "min": 0, "max": 23, "group": "group_proactive",
    },
    "proactive_quiet_hours_end": {
        "type": "number", "label": "免打扰结束(时)", "description": "免打扰时段结束小时（0-23）",
        "default": 7, "min": 0, "max": 23, "group": "group_proactive",
    },
    "proactive_group_idle_minutes": {
        "type": "number", "label": "群聊沉默触发(分钟)", "description": "群聊沉默多久后主动开口",
        "default": 10, "min": 1, "max": 1440, "group": "group_proactive",
    },
    "proactive_prompt": {
        "type": "textarea", "label": "主动发言提示词", "description": "可用 {{unanswered_count}} / {{current_time}} 占位",
        "default": "你在群聊/私聊中发起主动消息，像真人一样自然开口。当前时间：{{current_time}}；之前已主动发言但无人接话的次数：{{unanswered_count}}。结合最近对话，自然地说一句适合此刻的话。",
        "rows": 5, "group": "group_proactive",
    },

    # ==================== 定时任务 ====================
    "schedule_enable": {
        "type": "boolean", "label": "定时任务启用", "description": "识别对话中的定时请求（如\"明天早上8点提醒我\"）并按点回复；关闭后不再创建新任务",
        "default": True, "group": "group_schedule",
    },
    "schedule_prompt": {
        "type": "textarea", "label": "定时触发提示词", "description": "定时任务触发时给 LLM 的指令，可用 {{content}} / {{current_time}} / {{job_json}} 占位",
        "default": "你被一个定时任务唤醒，这不是一次用户对话。\n规则：\n1. 这不是聊天轮次：不要打招呼，不要反问用户。\n2. 结合最近的历史对话理解与用户的关系和上下文，用符合你人设的语气自然开口。\n3. 自然地说明你联系的原因，参考任务内容即可，不要提及\"定时任务\"\"工具\"等技术细节。\n4. 当前时间：{{current_time}}；需要完成的事情：{{content}}。\n任务信息：{{job_json}}",
        "rows": 7, "group": "group_schedule",
    },
}
