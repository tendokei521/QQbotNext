"""框架级 Agent 配置 Schema（WebUI /agent 页渲染表单）。"""

SCHEMA = {
    # ==================== 分组定义 ====================
    "group_switch": {"type": "group", "label": "功能开关", "collapsible": True},
    "group_model": {"type": "group", "label": "模型参数", "collapsible": True},
    "group_session": {"type": "group", "label": "会话管理", "collapsible": True},
    "group_compress": {"type": "group", "label": "上下文压缩", "collapsible": True},
    "group_trigger": {"type": "group", "label": "触发设置", "collapsible": True},
    "group_context": {"type": "group", "label": "用户信息感知", "collapsible": True},
    "group_interrupt": {"type": "group", "label": "回复打断", "collapsible": True},
    "group_stream": {"type": "group", "label": "流式回复", "collapsible": True},
    "group_proactive": {"type": "group", "label": "主动消息", "collapsible": True},
    "group_schedule": {"type": "group", "label": "定时任务", "collapsible": True},
    "group_memory": {"type": "group", "label": "长期记忆（实验性）", "collapsible": True},
    "group_knowledge": {"type": "group", "label": "知识库", "collapsible": True},
    "group_tavily": {"type": "group", "label": "Tavily 联网搜索", "collapsible": True},
    "group_mcp": {"type": "group", "label": "MCP 工具", "collapsible": True},
    "group_napcat": {"type": "group", "label": "NapCat 工具", "collapsible": True},
    "group_permission": {"type": "group", "label": "权限", "collapsible": True},

    # ==================== 配置项 ====================
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
    "model": {
        "type": "string", "label": "默认模型（回退）", "description": "未配置模型池时的默认模型名",
        "default": "deepseek-chat", "group": "group_model",
    },
    "temperature": {
        "type": "number", "label": "温度", "description": "随机性；越大回复越有创造性",
        "default": 0.7, "min": 0, "max": 2, "step": 0.1, "group": "group_model",
    },
    "max_tokens": {
        "type": "number", "label": "最大输出 Tokens", "description": "单次回复的最大 token 数",
        "default": 1024, "min": 1, "max": 32768, "group": "group_model",
    },
    "stream_output": {
        "type": "boolean", "label": "流式输出", "description": "启用后 LLM 回复按句子流式发送（支持带 tools 的工具调用）",
        "default": False, "group": "group_stream",
    },    "stream_send_pool_enabled": {
        "type": "boolean", "label": "启用流式消息池", "description": "开启后流式句子进入有序消息池，按频率发送",
        "default": False, "group": "group_stream",
    },
    "stream_send_by_sentence": {
        "type": "boolean", "label": "按句子发送", "description": "按句切分后逐条发送",
        "default": True, "group": "group_stream",
    },
    "stream_sentence_max_length": {
        "type": "number", "label": "单句最大长度", "description": "流式输出时每条消息的最大字符数",
        "default": 200, "min": 10, "max": 500, "group": "group_stream",
    },
    "stream_send_interval_mode": {
        "type": "select", "label": "发送间隔模式", "description": "none=不等待；fixed=固定间隔；length_curve=按字数曲线",
        "default": "none", "group": "group_stream",
        "options": {
            "none": "不等待",
            "fixed": "固定间隔",
            "length_curve": "按字数曲线",
        },
    },
    "stream_send_interval_base_ms": {
        "type": "number", "label": "基础发送间隔(ms)", "description": "固定间隔或曲线的基础延迟",
        "default": 600, "min": 0, "max": 10000, "group": "group_stream",
    },
    "stream_send_interval_min_ms": {
        "type": "number", "label": "最短发送间隔(ms)", "description": "最终延迟的下限",
        "default": 100, "min": 0, "max": 10000, "group": "group_stream",
    },
    "stream_send_interval_max_ms": {
        "type": "number", "label": "最长发送间隔(ms)", "description": "最终延迟的上限",
        "default": 3000, "min": 0, "max": 30000, "group": "group_stream",
    },
    "stream_send_curve": {
        "type": "select", "label": "发送曲线", "description": "按字数计算延迟的曲线",
        "default": "sqrt", "group": "group_stream",
        "options": {
            "fixed": "固定",
            "sqrt": "平方根（短句稍慢，长句增速放缓）",
            "log": "对数（长句增速更平缓）",
            "inverse": "反比（短句慢，长句快）",
            "short_long": "短/长两档",
        },
    },
    "stream_send_curve_k": {
        "type": "number", "label": "曲线强度", "description": "曲线系数 k，越大延迟随字数增长越明显",
        "default": 200, "min": 0, "max": 5000, "group": "group_stream",
    },
    "stream_short_message_length": {
        "type": "number", "label": "短句阈值(字)", "description": "short_long 曲线中判断短句的长度阈值",
        "default": 10, "min": 1, "max": 100, "group": "group_stream",
    },
    "stream_short_message_delay_ms": {
        "type": "number", "label": "短句发送延迟(ms)", "description": "short_long 曲线中短句的延迟",
        "default": 1200, "min": 0, "max": 10000, "group": "group_stream",
    },
    "stream_long_message_delay_ms": {
        "type": "number", "label": "长句发送延迟(ms)", "description": "short_long 曲线中长句的延迟",
        "default": 400, "min": 0, "max": 10000, "group": "group_stream",
    },
    "stream_send_prefix": {
        "type": "string", "label": "发送前缀", "description": "每条流式消息发送前追加的文本",
        "default": "", "group": "group_stream",
    },
    "stream_send_suffix": {
        "type": "string", "label": "发送后缀", "description": "每条流式消息发送后追加的文本",
        "default": "", "group": "group_stream",
    },
    "stream_send_max_queue": {
        "type": "number", "label": "消息池最大排队数", "description": "队列满时的处理策略见下",
        "default": 20, "min": 1, "max": 200, "group": "group_stream",
    },
    "stream_queue_full_policy": {
        "type": "select", "label": "队列满策略", "description": "消息池满时的行为",
        "default": "backpressure", "group": "group_stream",
        "options": {
            "backpressure": "背压（等待队列有空位）",
            "drop_newest": "丢弃新消息",
            "drop_oldest": "丢弃最旧消息",
        },
    },
    "stream_flush_on_finish": {
        "type": "boolean", "label": "结束后立即清空", "description": "流结束后是否忽略剩余间隔立即发送剩余消息",
        "default": False, "group": "group_stream",
    },
    "stream_keep_order": {
        "type": "boolean", "label": "严格按顺序发送", "description": "保持生成顺序逐条发送（建议开启）",
        "default": True, "group": "group_stream",
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
    "context_compress_enable": {
        "type": "boolean", "label": "启用上下文压缩", "description": "历史超过 history_rounds 时，只把超出的部分压缩成摘要，保留最近 history_rounds 条原文（仿 AstrBot）",
        "default": True, "group": "group_compress",
    },
    "context_compress_prompt": {
        "type": "textarea", "label": "压缩提示词", "description": "让 LLM 生成摘要的指令",
        "default": "请把上面的历史对话压缩成一份简洁但完整的摘要，用于后续对话无缝续接：\n1. 覆盖所有核心话题及最终结论/结果；\n2. 高亮最近的主要关注点；\n3. 如有工具调用、任务进度或待办，说明当前状态和下一步；\n4. 保留用户的重要个人信息、偏好、称呼等关键事实；\n5. 使用与对话相同的语言输出。\n只输出摘要内容，不要输出额外解释。",
        "rows": 6, "group": "group_compress",
    },
    "max_message_length": {
        "type": "number", "label": "消息最大长度", "description": "单条消息的最大字符数",
        "default": 200, "min": 20, "max": 500, "group": "group_session",
    },
    "include_pre_history": {
        "type": "boolean", "label": "包含群聊环境背景", "description": "开启时拉取群聊最近消息，并附上群名/群号/当前时间作为 LLM 背景信息（不计入会话历史；未开启则完全不拉取在线历史）",
        "default": False, "group": "group_session",
    },
    "include_private_pre_history": {
        "type": "select", "label": "包含私信会话前历史", "description": "私信时是否将会话开始前的私信消息提供给LLM",
        "default": "default",
        "options": {"default": "不加载", "history": "加载为背景消息", "load": "加载为会话历史"},
        "group": "group_session",
    },
    "clean_output_parentheses": {
        "type": "boolean", "label": "强制清洗括号内容", "description": "写入会话历史时剥离大模型输出中的（…）/(…)内容，避免后续回复模仿括号风格（本次展示原文不变）",
        "default": True, "group": "group_session",
    },
    "trigger_at": {
        "type": "boolean", "label": "@触发", "description": "被@时启动对话（默认关闭，群聊默认不响应普通消息）",
        "default": False, "group": "group_trigger",
    },
    "trigger_keyword": {
        "type": "string_list", "label": "触发关键词", "description": "包含此关键词时触发（可选）",
        "default": [], "placeholder": "例如：AI、助手", "group": "group_trigger",
    },

    # ==================== 用户信息感知（LLM 增强注入；无总开关，按子项生效） ====================
    "include_time": {
        "type": "boolean", "label": "包含当前时间",
        "description": "在最新一轮用户消息开头插入当前时间，例如 (时间：2026-08-17 10:00:00)",
        "default": True, "group": "group_context",
    },
    "include_private_qq": {
        "type": "boolean", "label": "私信包含对方QQ",
        "description": "私信场景下在时间行后独立注入对方 QQ，格式：(QQ: 123456789)",
        "default": True, "group": "group_context",
    },
    "include_sender": {
        "type": "boolean", "label": "包含发送者信息",
        "description": "群聊中附加发送者昵称和 QQ",
        "default": True, "group": "group_context",
    },
    "include_mentioned": {
        "type": "boolean", "label": "包含提到了信息",
        "description": "群聊中附加被 @ 的人（自动过滤机器人自身）",
        "default": True, "group": "group_context",
    },
    "include_quote": {
        "type": "boolean", "label": "包含引用消息",
        "description": "附加引用消息内容",
        "default": True, "group": "group_context",
    },
    "include_quote_sender": {
        "type": "boolean", "label": "引用消息包含发送者",
        "description": "群聊中引用消息附带发送者昵称和 QQ",
        "default": True, "group": "group_context",
    },
    "include_sent": {
        "type": "boolean", "label": "包含发送内容",
        "description": "附加当前消息文本为“发送了：xxx”",
        "default": True, "group": "group_context",
    },
    "fetch_at_nickname": {
        "type": "boolean", "label": "拉取 @ 对象昵称",
        "description": "关闭时只附加 QQ，不额外请求群成员信息",
        "default": True, "group": "group_context",
    },
    "fetch_quote_content": {
        "type": "boolean", "label": "拉取引用消息内容",
        "description": "关闭时不请求原始消息内容",
        "default": True, "group": "group_context",
    },

    # ==================== 回复打断 ====================
    "interrupt_enable": {
        "type": "boolean", "label": "启用回复打断",
        "description": "LLM 输出过程中收到新消息时取消剩余发送并开始新一轮请求",
        "default": False, "group": "group_interrupt",
    },
    "interrupt_save_sent": {
        "type": "boolean", "label": "保存已发送内容",
        "description": "中断时把实际已发送的句子写入历史",
        "default": True, "group": "group_interrupt",
    },
    "interrupt_debug": {
        "type": "boolean", "label": "调试回复打断",
        "description": "开启后打印回复被打断的日志",
        "default": False, "group": "group_interrupt",
    },

    # ==================== 权限 ====================
    "permission": {
        "type": "select", "label": "权限角色", "description": "Agent 可响应的事件权限角色",
        "default": "member", "group": "group_permission",
        "options": {
            "everyone": "所有人",
            "member": "普通成员及以上",
            "group_admin": "群管理/群主",
            "group_owner": "仅群主",
            "owner": "仅 Bot 拥有者",
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
    "stream_proactive_enabled": {
        "type": "boolean", "label": "主动消息使用流式", "description": "开启后主动消息使用与普通消息相同的流式发送配置",
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
    "stream_scheduled_enabled": {
        "type": "boolean", "label": "定时任务使用流式", "description": "开启后定时任务使用与普通消息相同的流式发送配置",
        "default": False, "group": "group_schedule",
    },
    "schedule_prompt": {
        "type": "textarea", "label": "定时触发提示词", "description": "定时任务触发时给 LLM 的指令，可用 {{content}} / {{current_time}} / {{job_json}} 占位",
        "default": "你被一个定时任务唤醒，这不是一次用户对话。\n规则：\n1. 这不是聊天轮次：不要打招呼，不要反问用户。\n2. 结合最近的历史对话理解与用户的关系和上下文，用符合你人设的语气自然开口。\n3. 自然地说明你联系的原因，参考任务内容即可，不要提及\"定时任务\"\"工具\"等技术细节。\n4. 当前时间：{{current_time}}；需要完成的事情：{{content}}。\n任务信息：{{job_json}}",
        "rows": 7, "group": "group_schedule",
    },

    # ==================== 长期记忆（实验性） ====================
    "experimental_long_term_memory": {
        "type": "boolean", "label": "启用长期记忆实验方案", "description": "开启后才启用长期记忆，并同步使用新版单行脱敏提示词；关闭时保持默认无记忆 + 旧版感知增强格式",
        "default": False, "group": "group_memory",
    },
    "memory_enable": {
        "type": "boolean", "label": "长期记忆总开关", "description": "关闭后不写入、不注入记忆（数据保留）；需同时开启上方实验性开关才生效",
        "default": False, "group": "group_memory",
    },
    "memory_private_enable": {
        "type": "boolean", "label": "私聊记忆", "description": "私聊场景记录与注入",
        "default": True, "group": "group_memory",
    },
    "memory_group_enable": {
        "type": "boolean", "label": "群聊记忆", "description": "群聊场景记录与注入（按群内成员 owner 隔离）",
        "default": True, "group": "group_memory",
    },
    "memory_recall_max": {
        "type": "number", "label": "注入条数上限", "description": "每次请求最多注入几条长期记忆",
        "default": 8, "min": 1, "max": 30, "group": "group_memory",
    },
    "memory_recall_max_chars": {
        "type": "number", "label": "注入字符上限", "description": "记忆块总字符数上限，防止撑爆上下文",
        "default": 600, "min": 100, "max": 3000, "group": "group_memory",
    },
    "memory_save_deterministic": {
        "type": "boolean", "label": "“记住…”确定性兜底", "description": "检测到“记住/我喜欢/我叫…”等指令时直接入库，不依赖模型调工具",
        "default": True, "group": "group_memory",
    },
    "memory_extract_enable": {
        "type": "boolean", "label": "自动蒸馏", "description": "对话后自动提炼值得记住的事实（额外少量 LLM 调用）",
        "default": True, "group": "group_memory",
    },
    "memory_extract_interval_min": {
        "type": "number", "label": "蒸馏最小间隔(分钟)", "description": "同一会话两次蒸馏之间的最小间隔，控制成本",
        "default": 10, "min": 1, "max": 1440, "group": "group_memory",
    },
    "memory_max_per_owner": {
        "type": "number", "label": "每对象记忆上限", "description": "每个用户/群保存的记忆条数上限，超出按重要度与新旧淘汰",
        "default": 300, "min": 10, "max": 5000, "group": "group_memory",
    },
    "memory_user_cross_group": {
        "type": "boolean", "label": "跨群用户画像", "description": "允许把用户画像跨群共享（默认关闭，保护隐私）",
        "default": False, "group": "group_memory",
    },
    "memory_audit_enable": {
        "type": "boolean", "label": "事件记录", "description": "记录记忆的写入/读取/删除/注入事件（#chat memory audit 查看）",
        "default": True, "group": "group_memory",
    },
    "memory_audit_inject": {
        "type": "boolean", "label": "记录每次注入", "description": "是否记录每次向提示词的记忆注入（默认关闭，避免日志噪音）",
        "default": False, "group": "group_memory",
    },
    "memory_min_confidence": {
        "type": "number", "label": "注入最低置信度", "description": "低于此置信度的记忆默认不注入（被点名时仍上浮供核对）",
        "default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05, "group": "group_memory",
    },
    "memory_inject_hedge": {
        "type": "boolean", "label": "低可信试探语气", "description": "中/低置信记忆注入时加“（好像）/（记不太清）”前缀，让模型以询问而非断言回应",
        "default": True, "group": "group_memory",
    },
    "memory_max_age_days": {
        "type": "number", "label": "记忆最大龄(天)", "description": "超过此龄不再注入（数据保留，可 #chat memory list --all 查看）；0=不限",
        "default": 180, "min": 0, "max": 3650, "group": "group_memory",
    },
    "memory_on_reset": {
        "type": "select", "label": "会话重置时记忆", "description": "#chat new/exit/stop 或 memory reset 时如何处置记忆",
        "default": "suspend", "group": "group_memory",
        "options": {
            "suspend": "挂起旧记忆（默认，数据保留）",
            "clear": "清除该对象记忆",
            "keep": "保持不变",
        },
    },
    "memory_upgrade_saved_only": {
        "type": "boolean", "label": "重置只保留已保存型", "description": "重置挂起时仅保留“明确保存/已确认”型记忆，自动蒸馏型一律挂起",
        "default": True, "group": "group_memory",
    },

    # ==================== 知识库 ====================
    "knowledge_enable": {
        "type": "boolean", "label": "启用知识库", "description": "开启后 LLM 可使用 knowledge_search / knowledge_add / knowledge_delete 工具",
        "default": False, "group": "group_knowledge",
    },
    "knowledge_embedding_model_id": {
        "type": "string", "label": "Embedding 模型实例 ID", "description": "留空自动选择第一个启用且能力类型为 Embedding 的模型实例",
        "default": "", "group": "group_knowledge",
    },
    "knowledge_recall_limit": {
        "type": "number", "label": "默认检索条数", "description": "knowledge_search 默认返回的片段数量",
        "default": 5, "min": 1, "max": 20, "group": "group_knowledge",
    },

    # ==================== Tavily 联网搜索 ====================
    "tavily_enable": {
        "type": "boolean", "label": "启用 Tavily 搜索", "description": "开启后 LLM 可使用 tavily_search 工具进行联网搜索",
        "default": False, "group": "group_tavily",
    },
    "tavily_api_key": {
        "type": "password", "label": "Tavily API Key", "description": "在 https://app.tavily.com 获取，格式 tvly-xxx",
        "default": "", "group": "group_tavily",
    },
    "tavily_max_results": {
        "type": "number", "label": "默认搜索条数", "description": "tavily_search 默认返回的结果数量",
        "default": 5, "min": 1, "max": 20, "group": "group_tavily",
    },
    "tavily_search_depth": {
        "type": "select", "label": "搜索深度", "description": "basic 快且省额度；advanced 结果更准但成本更高",
        "default": "basic", "group": "group_tavily",
        "options": {
            "basic": "basic（推荐）",
            "advanced": "advanced（更准）",
        },
    },
    "tavily_max_content_chars": {
        "type": "number", "label": "返回内容截断长度", "description": "搜索结果文本超过该长度后截断，防止撑爆上下文",
        "default": 2000, "min": 500, "max": 20000, "group": "group_tavily",
    },

    # ==================== MCP ====================
    "mcp_servers": {
        "type": "textarea", "label": "MCP Servers (JSON 数组)", "description": "每个元素：{\"name\":\"...\",\"command\":\"...\",\"args\":[...],\"env\":{},\"cwd\":\"...\",\"timeout\":30}",
        "default": "[]", "rows": 5, "group": "group_mcp",
    },

    # ==================== NapCat 工具 ====================
    "napcat_tools_enable": {
        "type": "boolean", "label": "启用 NapCat 工具", "description": "把 NapCat/OneBot API 暴露给 LLM 作为 function calling 工具",
        "default": False, "group": "group_napcat",
    },
    "napcat_tools_allowed": {
        "type": "string_list", "label": "允许的工具（白名单）", "description": "留空表示允许全部；非空时只允许列出的工具",
        "default": [], "group": "group_napcat",
    },
    "napcat_tools_denied": {
        "type": "string_list", "label": "禁用的工具（黑名单）", "description": "黑名单优先于白名单，可在此关闭敏感工具",
        "default": [], "group": "group_napcat",
    },
    "napcat_tools_max_result": {
        "type": "number", "label": "返回结果截断长度", "description": "API 返回内容超过该长度后截断，防止撑爆上下文",
        "default": 2000, "min": 100, "max": 20000, "group": "group_napcat",
    },
    "napcat_tools_debug": {
        "type": "boolean", "label": "NapCat 调试日志", "description": "开启后完整记录每个 NapCat 工具调用的请求参数与响应内容",
        "default": False, "group": "group_napcat",
    },

    # ==================== 感知增强提示词细调（实验性） ====================
    "meta_sender_style": {
        "type": "select", "label": "发送者标签样式", "description": "旧版=“发送者：”；新版=“发送者昵称：”；单行=“昵称(QQ): 正文”",
        "default": "legacy", "group": "group_memory",
        "options": {
            "legacy": "旧版：发送者：昵称(QQ)",
            "new": "新版：发送者昵称：昵称(QQ)",
            "single": "单行：昵称(QQ): 正文",
        },
    },
    "meta_sent_style": {
        "type": "select", "label": "正文标签样式", "description": "旧版=“发送了：”；新版=“消息正文：”（单行发送者样式下自动合并为一行）",
        "default": "legacy", "group": "group_memory",
        "options": {
            "legacy": "旧版：发送了：正文",
            "new": "新版：消息正文：正文",
        },
    },
    "meta_instruction_mode": {
        "type": "select", "label": "消息元信息消歧说明", "description": "选择注入哪一套系统级提示词说明；off 表示完全不注入",
        "default": "legacy", "group": "group_memory",
        "options": {
            "off": "不注入",
            "legacy": "旧版说明（保持真人感）",
            "new": "新版说明（单行脱敏 + 不知道名字不要叫代号）",
        },
    },
    "meta_mask_nickname": {
        "type": "boolean", "label": "句子型昵称脱敏", "description": "开启后把像句子/超长的昵称替换为 用户<QQ>，避免昵称内容被当作对话内容；普通短昵称保留（默认开启）",
        "default": True, "group": "group_memory",
    },
}

# 长期记忆（实验性）细调项：只有开启 experimental_long_term_memory 后才显示
for _key, _def in SCHEMA.items():
    if (
        isinstance(_def, dict)
        and _def.get("group") == "group_memory"
        and _key != "experimental_long_term_memory"
    ):
        _def["showIf"] = {"key": "experimental_long_term_memory", "value": True}

# ==================== 前端页面/重要性元数据 ====================
# Agent 前端用专用页面承载核心配置，不再只依赖通用 ConfigForm。
_PAGE_BY_GROUP = {
    "group_switch": "basic",
    "group_model": "model",
    "group_session": "basic",
    "group_compress": "basic",
    "group_trigger": "behavior",
    "group_context": "behavior",
    "group_interrupt": "behavior",
    "group_stream": "stream",
    "group_proactive": "panels",
    "group_schedule": "panels",
    "group_memory": "memory",
    "group_knowledge": "knowledge",
    "group_tavily": "basic",
    "group_mcp": "mcp",
    "group_napcat": "napcat",
    "group_permission": "permission",
}

_IMPORTANCE_BY_GROUP = {
    "group_switch": "basic",
    "group_model": "basic",
    "group_session": "basic",
    "group_compress": "advanced",
    "group_trigger": "basic",
    "group_context": "advanced",
    "group_interrupt": "advanced",
    "group_stream": "advanced",
    "group_proactive": "advanced",
    "group_schedule": "advanced",
    "group_memory": "advanced",
    "group_knowledge": "advanced",
    "group_tavily": "advanced",
    "group_mcp": "advanced",
    "group_napcat": "advanced",
    "group_permission": "basic",
}

for _key, _def in SCHEMA.items():
    if isinstance(_def, dict) and _def.get("group"):
        _def.setdefault("page", _PAGE_BY_GROUP.get(_def["group"], "basic"))
        _def.setdefault("importance", _IMPORTANCE_BY_GROUP.get(_def["group"], "advanced"))

# ==================== 流式回复预设 ====================
# fast：平均 500–1000ms；normal：平均 1000–2000ms；slow：平均 3000–4000ms
STREAM_PRESETS: dict[str, dict] = {
    "fast": {
        "label": "快速",
        "description": "平均约 500–1000ms，消息紧跟生成节奏",
        "config": {
            "stream_send_interval_mode": "fixed",
            "stream_send_interval_base_ms": 750,
            "stream_send_interval_min_ms": 500,
            "stream_send_interval_max_ms": 1000,
            "stream_send_curve": "sqrt",
            "stream_send_curve_k": 200,
            "stream_short_message_length": 10,
            "stream_short_message_delay_ms": 500,
            "stream_long_message_delay_ms": 1000,
        },
    },
    "normal": {
        "label": "正常",
        "description": "平均约 1000–2000ms，自然但有节奏",
        "config": {
            "stream_send_interval_mode": "fixed",
            "stream_send_interval_base_ms": 1500,
            "stream_send_interval_min_ms": 1000,
            "stream_send_interval_max_ms": 2000,
            "stream_send_curve": "sqrt",
            "stream_send_curve_k": 200,
            "stream_short_message_length": 10,
            "stream_short_message_delay_ms": 1200,
            "stream_long_message_delay_ms": 2000,
        },
    },
    "slow": {
        "label": "偏慢",
        "description": "平均约 3000–4000ms，更有“思考感”",
        "config": {
            "stream_send_interval_mode": "fixed",
            "stream_send_interval_base_ms": 3500,
            "stream_send_interval_min_ms": 3000,
            "stream_send_interval_max_ms": 4000,
            "stream_send_curve": "sqrt",
            "stream_send_curve_k": 200,
            "stream_short_message_length": 10,
            "stream_short_message_delay_ms": 3000,
            "stream_long_message_delay_ms": 4000,
        },
    },
}
