"""框架级 Agent 配置 Schema（WebUI /agent 页渲染表单）。"""

SCHEMA = {
    # ==================== 分组定义 ====================
    "group_switch": {"type": "group", "label": "功能开关", "collapsible": True},
    "group_model": {"type": "group", "label": "模型参数", "collapsible": True},
    "group_session": {"type": "group", "label": "会话管理", "collapsible": True},
    "group_compress": {"type": "group", "label": "上下文压缩", "collapsible": True},
    "group_trigger": {"type": "group", "label": "触发设置", "collapsible": True},
    "group_debounce": {"type": "group", "label": "消息抖动", "collapsible": True},
    "group_context": {"type": "group", "label": "用户信息感知", "collapsible": True},
    "group_behavior": {"type": "group", "label": "行为规则（拟人化）", "collapsible": True},
    "group_interrupt": {"type": "group", "label": "回复打断", "collapsible": True},
    "group_stream": {"type": "group", "label": "流式回复", "collapsible": True},
    "group_proactive": {"type": "group", "label": "主动消息", "collapsible": True},
    "group_schedule": {"type": "group", "label": "定时任务", "collapsible": True},
    "group_memory": {"type": "group", "label": "长期记忆（实验性）", "collapsible": True},
    "group_knowledge": {"type": "group", "label": "知识库", "collapsible": True},
    "group_tavily": {"type": "group", "label": "Tavily 联网搜索", "collapsible": True},
    "group_mcp": {"type": "group", "label": "MCP 工具", "collapsible": True},
    "group_onebot": {"type": "group", "label": "OneBot 工具", "collapsible": True},
    "group_tools": {"type": "group", "label": "工具调用", "collapsible": True},
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
        "type": "number", "label": "最大输出 Tokens（兜底）",
        "description": "整条模型链都未设置时使用的兜底上限。通常应在 Provider 模型的「最大输出 Token」中配置。",
        "default": 1024, "min": 1, "max": 1000000, "group": "group_model",
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
        "type": "select", "label": "发送间隔模式", "description": "固定间隔 = 恒定节奏；按字数曲线 = 长句多等；不等待 = 连续发送。",
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
        "type": "number", "label": "短句阈值(字)", "description": "按字数曲线模式下，判定为短句的长度阈值。",
        "default": 10, "min": 1, "max": 100, "group": "group_stream",
    },
    "stream_short_message_delay_ms": {
        "type": "number", "label": "短句发送延迟(ms)", "description": "按字数曲线模式下，短句的发送延迟。",
        "default": 1200, "min": 0, "max": 10000, "group": "group_stream",
    },
    "stream_long_message_delay_ms": {
        "type": "number", "label": "长句发送延迟(ms)", "description": "按字数曲线模式下，长句的发送延迟。",
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
    "prompt_preset": {
        "type": "select", "label": "提示词方案",
        "description": "替换框架生成的系统提示词，人格设定不受影响；留空沿用内置文案。",
        "default": "", "group": "group_session",
        "options": {
            "": "未选择（沿用内置文案）",
            "chat": "聊天：普通群友口吻，短句、选择性接话",
            "roleplay": "角色扮演：以人设身份持续扮演，不跳出",
            "assistant": "助手：有求必应、先给结论、结构化",
        },
    },
    "persona_preset_id": {
        "type": "resource", "resource": "role_preset",
        "label": "角色预设（活引用）",
        "description": "选择预设后每轮实时解析提示词，改动立即生效；留空则用自定义系统提示词。",
        "default": "", "group": "group_session",
    },
    "system_prompt_style": {
        "type": "select", "label": "消息组装风格",
        "description": "分块 = 人设、技能、记忆各一条系统消息（默认）；单条 = 合并为一条。",
        "default": "blocks", "group": "group_session",
        "options": {
            "blocks": "分块：每类一块 system（默认）",
            "single": "单条：折成一整条 system prompt",
        },
    },
    "assembly_profile": {
        "type": "select", "label": "组装档位",
        "description": "标准 = 注入行为规则段，拟人化效果更好；旧版 = 保持原有组装行为。",
        "default": "legacy", "group": "group_behavior",
        "options": {
            "legacy": "旧版：只有人格设定（默认）",
            "qq_agent": "拟人化：人格 + 行为规则段",
        },
    },
    "behavior_rules_enable": {
        "type": "boolean", "label": "注入行为规则段",
        "description": "关闭后即使档位是拟人化也不注入（排查「为什么回复变味」时用）",
        "default": True, "group": "group_behavior",
    },
    "behavior_rules_override": {
        "type": "textarea", "label": "覆盖行为规则",
        "description": "留空使用内置行为规则；填写后整体替换，安全规则与工作方式仍强制保留。",
        "default": "", "rows": 8, "group": "group_behavior",
    },
    "participation": {
        "type": "select", "label": "参与度",
        "description": "改写行为段【该说/不该说】的首行：安静=多在潜水，活跃=多接话（仍选择性接，不刷屏）",
        "default": "medium", "group": "group_behavior",
        "options": {
            "low": "安静：少开口",
            "medium": "普通群友（默认）",
            "high": "活跃：多接话",
        },
    },
    "emoji_react_level": {
        "type": "number", "label": "贴表情活跃度",
        "description": "0 = 几乎不贴；1 = 每 3~5 轮一次（默认）；2 = 优先贴；3 = 能贴就贴。",
        "default": 1, "min": 0, "max": 3, "group": "group_behavior",
    },
    "user_message_format": {
        "type": "select", "label": "本轮消息格式",
        "description": "单行 = 「昵称(QQ): 正文」；多行 = 时间、群号、发送者分行列出。",
        "default": "single_line", "group": "group_behavior",
        "options": {
            "single_line": "单行：昵称(QQ): 正文（默认）",
            "legacy": "多行：六行元信息块",
        },
    },
    "prompt_time_position": {
        "type": "select", "label": "时间行位置",
        "description": "末尾=把时间挪到消息最后（默认，前头的稳定行不变，前缀缓存更划算）；开头=旧行为",
        "default": "tail", "group": "group_behavior",
        "options": {
            "tail": "末尾（默认）",
            "head": "开头",
        },
    },
    "speech_via_tool": {
        "type": "boolean", "label": "发言走工具（文本=思考）",
        "description": "开启后模型输出只作思考，发言必须调用工具；需同时开启流式输出。",
        "default": False, "group": "group_behavior",
    },
    "speech_max_bubbles": {
        "type": "number", "label": "单轮最多几条",
        "description": "一轮最多发几条气泡（防复读机式刷屏）",
        "default": 4, "min": 1, "max": 10, "group": "group_behavior",
    },
    "speech_max_bubble_chars": {
        "type": "number", "label": "单条字数上限",
        "description": "单条气泡字数上限，超长会被裁短（QQ 单条太长会被折叠，也不像人说话）",
        "default": 300, "min": 20, "max": 2000, "group": "group_behavior",
    },
    "speech_reminder_rounds": {
        "type": "number", "label": "催发言提醒轮次",
        "description": "工具轮次只剩这么多仍没开口时提醒一次。0 = 不提醒。",
        "default": 3, "min": 0, "max": 10, "group": "group_behavior",
    },
    "speech_force_reply_on_call": {
        "type": "boolean", "label": "被点名必须回应",
        "description": "被 @ / 私聊时若一句话没说，再补一轮要求它发言（防「叫了不理」）",
        "default": True, "group": "group_behavior",
    },
    "speech_fallback_enable": {
        "type": "boolean", "label": "静默时补兜底话术",
        "description": "关闭（默认）=静默就是真沉默，符合拟人化；开启则静默时仍发「抱歉，我暂时无法回答」",
        "default": False, "group": "group_behavior",
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
        "type": "boolean", "label": "启用上下文压缩", "description": "历史超过轮数上限时，把较早部分压缩成摘要，保留最近若干条原文。",
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
    "prompt_dump_enable": {
        "type": "boolean", "label": "请求落盘（迁移对账）",
        "description": "把每轮最终请求写入文件，便于逐条比对。默认关闭。",
        "default": False, "group": "group_session",
    },
    "prompt_dump_max_entries": {
        "type": "number", "label": "请求落盘保留条数", "description": "滚动文件最多保留多少轮请求（从最旧开始丢）",
        "default": 20, "min": 1, "max": 500, "group": "group_session",
    },
    "history_mode": {
        "type": "select", "label": "历史注入方式",
        "description": "recent = 只注入最近若干条，更早的由「历史检索」工具按需取回；full = 全部注入（旧行为）。",
        "default": "recent", "group": "session_window",
        "options": {"recent": "最近若干条 + 按需检索", "full": "全部注入"},
    },
    "history_recent_messages": {
        "type": "number", "label": "注入最近条数",
        "description": "「最近若干条」模式下注入多少条历史；更早的内容仍可通过工具检索。",
        "default": 12, "min": 2, "max": 200, "group": "session_window",
    },
    # Q1：上下文预算（按 token 压力触发压缩，而不是按条数）
    "context_window": {
        "type": "number", "label": "模型上下文窗口（token）",
        "description": "用来判断是否该压缩历史。0 = 不启用压力触发（回退按「保留条数」那个配置）。",
        "default": 0, "min": 0, "max": 2000000, "group": "group_compress",
    },
    "context_compress_trigger_ratio": {
        "type": "number", "label": "压缩触发比例",
        "description": "估算输入占窗口的比例超过它就压缩。0 = 不启用（按条数）。",
        "default": 0.8, "min": 0, "max": 1, "group": "group_compress",
    },
    "context_compress_keep_ratio": {
        "type": "number", "label": "压缩后保留比例",
        "description": "压缩时保留最近多少比例的原文（不超过「保留条数」配置的上限）。",
        "default": 0.16, "min": 0, "max": 0.9, "group": "group_compress",
    },
    "context_compress_backend": {
        "type": "select", "label": "压缩方式",
        "description": "summary = 调模型写摘要；prune = 只裁剪旧工具结果（零成本）；both = 先裁再摘要。",
        "default": "summary", "group": "group_compress",
        "options": {"summary": "模型摘要", "prune": "只裁剪工具结果", "both": "先裁再摘要"},
    },
    # P4：在线不变式。开启后每次请求前核对「会话日志派生结果」与「内存历史」是否一致，
    # 不等就落盘 diff。默认关：这是一条排查开关，每次请求都做投影比对有开销。
    "prompt_dump_verify": {
        "type": "boolean", "label": "历史一致性自检",
        "description": "每次请求前核对会话日志与内存历史是否一致，不一致时落盘 diff。默认关闭。",
        "default": False, "group": "group_session",
    },
    "context_token_budget": {
        "type": "number", "label": "上下文 token 预算",
        "description": "上下文 token 上限，超出时从最旧的历史开始丢弃。0 = 不限制。",
        "default": 0, "min": 0, "max": 200000, "group": "group_compress",
    },
    "context_token_chars_per_token": {
        "type": "number", "label": "token 估算：每 token 字符数",
        "description": "粗估用（中文约 1-2 字符/token，英文约 4）；只影响防爆裁剪，不影响真实计费",
        "default": 2.0, "min": 0.5, "max": 8, "group": "group_compress",
    },
    "include_pre_history": {
        "type": "boolean", "label": "包含群聊环境背景", "description": "请求前拉取群聊最近消息作为背景，不计入会话历史。",
        "default": False, "group": "group_session",
    },
    "group_log_enable": {
        "type": "boolean", "label": "注入群聊环境记录",
        "description": "注入群聊记录（消息、表情回应、戳一戳、撤回）作为环境块，与在线历史不会重复。",
        "default": True, "group": "group_session",
    },
    "group_log_window_minutes": {
        "type": "number", "label": "群聊记录窗口（分钟）",
        "description": "每次请求最多带回最近多少分钟的群聊记录（本地保留窗口由模块配置决定，此处只控制组装）",
        "default": 60, "min": 1, "max": 1440, "group": "group_session",
    },
    "group_log_window_limit": {
        "type": "number", "label": "群聊记录条数上限",
        "description": "每次请求最多带回多少条群聊记录（超出时保留最新的）",
        "default": 50, "min": 1, "max": 500, "group": "group_session",
    },
    "group_log_max_chars": {
        "type": "number", "label": "群聊环境块字符预算",
        "description": "环境块的字符上限，超出时从最旧记录开始丢弃。0 = 不限制，会显著增加消耗。",
        "default": 4000, "min": 0, "max": 20000, "group": "group_session",
    },
    "include_private_pre_history": {
        "type": "select", "label": "包含私信会话前历史", "description": "私信时是否将会话开始前的私信消息提供给LLM",
        "default": "default",
        "options": {"default": "不加载", "history": "加载为背景消息", "load": "加载为会话历史"},
        "group": "group_session",
    },
    "history_auto_qq_min_local": {
        "type": "number", "label": "聊天记录工具补拉阈值", "description": "本地记录少于该条数时自动补拉聊天记录。0 = 总是补拉。",
        "default": 4, "min": 0, "max": 50, "group": "group_session",
    },
    "history_cross_query_enable": {
        "type": "boolean", "label": "允许私聊跨会话查群记录", "description": "私聊中允许查询本人所在的群历史；查询他人私聊始终拒绝。",
        "default": True, "group": "group_session",
    },
    "context_expand_enable": {
        "type": "boolean", "label": "未展开内容标记与按需展开",
        "description": "把展不开的内容标为【未展开】，并允许模型按需展开。",
        "default": True, "group": "group_session",
    },
    "referent_resolve_enable": {
        "type": "boolean", "label": "指代消解",
        "description": "注入当前对话焦点，让模型知道「那条」「刚才的」指什么。",
        "default": True, "group": "group_session",
    },
    "referent_prefetch_enable": {
        "type": "boolean", "label": "指代预取",
        "description": "判定出明确指向时提前取回内容，不必等模型调工具。",
        "default": True, "group": "group_session",
    },
    "referent_ambiguous_policy": {
        "type": "select", "label": "指代不明时的处理",
        "description": "候选不止一个且难以判断时：都取回（推荐）/ 只取焦点最高项 / 先向用户确认",
        "default": "fetch_all",
        "options": {"fetch_all": "都取回", "focus_first": "只取焦点项", "ask": "让模型先确认"},
        "group": "group_session",
    },
    "referent_prefetch_max": {
        "type": "number", "label": "预取候选上限", "description": "一轮最多预取几个候选（预算保护）",
        "default": 2, "min": 1, "max": 5, "group": "group_session",
    },
    "referent_focus_ttl": {
        "type": "number", "label": "焦点存活时长(秒)", "description": "焦点项多久没被关注就从焦点表淘汰",
        "default": 1800, "min": 60, "max": 86400, "group": "group_session",
    },
    "referent_focus_max": {
        "type": "number", "label": "焦点项上限", "description": "每会话保留的焦点项数量上限",
        "default": 12, "min": 3, "max": 50, "group": "group_session",
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
    "message_debounce_seconds": {
        "type": "number", "label": "消息抖动(秒)",
        "description": (
            "触发后先等这么久，期间的新消息合并进同一轮。0 = 立即请求。"
        ),
        "default": 0, "min": 0, "max": 30, "group": "group_debounce",
    },
    "message_debounce_max_wait": {
        "type": "number", "label": "抖动最长等待(秒)",
        "description": (
            "连续刷屏时的强制放行上限，应明显大于消息抖动秒数。"
        ),
        "default": 15, "min": 1, "max": 120, "group": "group_debounce",
    },

    # ==================== 用户信息感知（LLM 增强注入；无总开关，按子项生效） ====================
    "include_time": {
        "type": "boolean", "label": "包含当前时间",
        "description": "在本轮用户消息开头插入当前时间。",
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
        "type": "select", "label": "响应权限档位", "description": "Agent 可响应的事件权限角色（按角色高低判定：拥有者 > 管理者 > 群主 > 群管理 > 成员）",
        "default": "member", "group": "group_permission",
        "options": {
            "everyone": "所有人",
            "member": "普通成员及以上",
            "group_admin": "群管理/群主",
            "group_owner": "仅群主",
            "manager": "管理者和拥有者",
            "owner": "仅拥有者",
        },
    },
    "agent_owner_list": {
        "type": "string_list", "label": "拥有者（QQ号）",
        "description": "每行一个 QQ 号，享有最高权限。留空则没有人拥有该权限。",
        "default": [], "placeholder": "每行一个 QQ 号", "group": "group_permission",
    },
    "agent_manager_list": {
        "type": "string_list", "label": "管理者（QQ号）",
        "description": "每行一个 QQ 号。管理者高于群主但低于拥有者，适合授予「帮我调教 Bot」的人",
        "default": [], "placeholder": "每行一个 QQ 号", "group": "group_permission",
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
        "type": "number", "label": "未回复上限", "description": "连续无人回应达到此数即停止主动发言。0 = 不限。",
        "default": 3, "min": 0, "max": 20, "group": "group_proactive",
    },
    "proactive_quiet_hours_start": {
        "type": "number", "label": "免打扰开始(时)", "description": "免打扰开始时刻，取值 0–23 时。",
        "default": 1, "min": 0, "max": 23, "group": "group_proactive",
    },
    "proactive_quiet_hours_end": {
        "type": "number", "label": "免打扰结束(时)", "description": "免打扰结束时刻，取值 0–23 时，可跨天。",
        "default": 7, "min": 0, "max": 23, "group": "group_proactive",
    },
    "proactive_group_idle_minutes": {
        "type": "number", "label": "群聊沉默触发(分钟)", "description": "群聊沉默多久后主动开口",
        "default": 10, "min": 1, "max": 1440, "group": "group_proactive",
    },
    "proactive_prompt": {
        "type": "textarea", "label": "主动发言提示词", "description": "可插入占位符：未回复次数、当前时间（写法见文档）。",
        "default": "你在群聊/私聊中发起主动消息，像真人一样自然开口。当前时间：{{current_time}}；之前已主动发言但无人接话的次数：{{unanswered_count}}。结合最近对话，自然地说一句适合此刻的话。",
        "rows": 5, "group": "group_proactive",
    },
    "proactive_prompt_enable": {
        "type": "boolean", "label": "工具主动性提示", "description": "注入主动性提示，说明何时该查记录、取环境信息。",
        "default": True, "group": "group_proactive",
    },
    "proactive_history_intent_nudge": {
        "type": "boolean", "label": "历史意图补强", "description": "用户追问旧话题时，提示模型先核实聊天记录。",
        "default": True, "group": "group_proactive",
    },
    "proactive_env_prompt_enable": {
        "type": "boolean", "label": "环境感知提示", "description": "提示模型何时该主动获取群名、成员等环境信息。",
        "default": True, "group": "group_proactive",
    },
    "proactive_unresolved_prompt_enable": {
        "type": "boolean", "label": "未展开标记说明", "description": "解释【未展开】标记，并提示先展开再回答。",
        "default": True, "group": "group_proactive",
    },
    "proactive_env_intent_nudge": {
        "type": "boolean", "label": "环境意图补强", "description": "用户问群/成员/某条消息相关信息时，在同一块里补一句「必须先取得实际环境数据再回答」",
        "default": True, "group": "group_proactive",
    },
    "referent_prompt_enable": {
        "type": "boolean", "label": "指代解析提示",
        "description": "说明如何读对话焦点、回指该指向谁、已展开内容不必重复取。",
        "default": True, "group": "group_proactive",
    },
    "outbound_directive_enable": {
        "type": "boolean", "label": "引用/@ 输出通道", "description": "允许模型在回复开头用 [reply] 引用当前消息、用 [@QQ] 真实 @ 某人；标记会被解析成消息段并从正文剥离",
        "default": True, "group": "group_proactive",
    },
    "outbound_directive_max_at": {
        "type": "number", "label": "单条最多 @ 人数", "description": "一条回复里 [@QQ] 的数量上限，避免刷屏",
        "default": 3, "min": 0, "max": 5, "group": "group_proactive",
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
        "type": "textarea", "label": "定时触发提示词", "description": "定时触发时给模型的指令，可插入触发内容与当前时间。",
        "default": "你被一个定时任务唤醒，这不是一次用户对话。\n规则：\n1. 这不是聊天轮次：不要打招呼，不要反问用户。\n2. 结合最近的历史对话理解与用户的关系和上下文，用符合你人设的语气自然开口。\n3. 自然地说明你联系的原因，参考任务内容即可，不要提及\"定时任务\"\"工具\"等技术细节。\n4. 当前时间：{{current_time}}；需要完成的事情：{{content}}。\n任务信息：{{job_json}}",
        "rows": 7, "group": "group_schedule",
    },

    # ==================== 长期记忆（实验性） ====================
    "experimental_long_term_memory": {
        "type": "boolean", "label": "启用长期记忆实验方案", "description": "已废弃，请改用长期记忆模块。",
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
        "type": "boolean", "label": "“记住…”确定性兜底", "description": "检测到「记住…」类指令时直接入库，不依赖模型调工具。",
        "default": True, "group": "group_memory",
    },
    "memory_extract_enable": {
        "type": "boolean", "label": "自动蒸馏", "description": "对话结束后自动提炼值得记住的事实，会带来少量额外调用。",
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
        "type": "boolean", "label": "事件记录", "description": "记录记忆的写入/读取/删除/注入事件（#llm memory audit 查看）",
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
        "type": "number", "label": "记忆最大龄(天)", "description": "超过该天数的记忆不再注入。0 = 不限。",
        "default": 180, "min": 0, "max": 3650, "group": "group_memory",
    },
    "memory_on_reset": {
        "type": "select", "label": "会话重置时记忆", "description": "#llm new/exit/stop 或 memory reset 时如何处置记忆",
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
        "type": "boolean", "label": "启用知识库", "description": "开启后模型可调用知识库的检索与写入工具。",
        "default": False, "group": "group_knowledge",
    },
    "knowledge_embedding_model_id": {
        "type": "resource", "resource": "provider_model", "filters": {"provider_type": "embedding"},
        "label": "Embedding 模型实例", "description": "下拉直接选模型实例（只列能力类型为 Embedding 的）；留空自动选择第一个启用且能力类型为 Embedding 的模型实例",
        "default": "", "group": "group_knowledge",
    },
    "knowledge_recall_limit": {
        "type": "number", "label": "默认检索条数", "description": "检索工具默认返回的片段数量。",
        "default": 5, "min": 1, "max": 20, "group": "group_knowledge",
    },

    # ==================== Tavily 联网搜索 ====================
            # 已废弃：三套 per-tool map 收敛为 tools_disabled（迁移脚本会合并后删除）
        # 已废弃 / 归模块：仅保留定义供迁移脚本取类型，界面不渲染
    "onebot_tool_overrides": {
        "type": "dynamic", "label": "OneBot 工具权限覆盖", "default": {},
        "description": "按工具名覆盖权限与适用范围。",
    },
        "authority_type": {
        "type": "string", "label": "（已废弃）权限类型", "default": "",
        "description": "已废弃。",
    },
    "stream_proactive_scheduled_enabled": {
        "type": "boolean", "label": "（已废弃）主动与定时共用流式", "default": False,
        "description": "已废弃，拆分为两项独立开关。",
    },
"knowledge_recall_max_chars": {
        "type": "integer", "label": "（已废弃）检索字符上限", "default": 2000,
        "description": "已废弃。",
    },
    "debug_prompt": {
        "type": "boolean", "label": "（已废弃）提示词调试", "default": False,
        "description": "已废弃，调试开关在增强模块里。",
    },
    "api_base": {
        "type": "string", "label": "（已废弃）api_base", "default": "",
        "description": "已废弃。",
    },
    "api_key": {
        "type": "string", "label": "（已废弃）api_key", "default": "",
        "description": "已废弃。",
    },
    "provider": {
        "type": "string", "label": "（已废弃）provider", "default": "",
        "description": "已废弃。",
    },
    "retry_attempts": {
        "type": "string", "label": "（已废弃）retry_attempts", "default": "",
        "description": "已废弃。",
    },
    "fallback_model_ids": {
        "type": "string", "label": "（已废弃）fallback_model_ids", "default": "",
        "description": "已废弃。",
    },
    "provider_model_id": {
        "type": "string", "label": "（已废弃）provider_model_id", "default": "",
        "description": "已废弃。",
    },
    "provider_model_pool": {
        "type": "string", "label": "（已废弃）provider_model_pool", "default": "",
        "description": "已废弃。",
    },
    "provider_preset_id": {
        "type": "string", "label": "（已废弃）provider_preset_id", "default": "",
        "description": "已废弃。",
    },
    "napcat_tools_enable": {
        "type": "string", "label": "（已废弃）napcat_tools_enable", "default": "",
        "description": "已废弃。",
    },
    "napcat_tools_allowed": {
        "type": "string", "label": "（已废弃）napcat_tools_allowed", "default": "",
        "description": "已废弃。",
    },
    "napcat_tools_denied": {
        "type": "string", "label": "（已废弃）napcat_tools_denied", "default": "",
        "description": "已废弃。",
    },
    "napcat_tools_debug": {
        "type": "string", "label": "（已废弃）napcat_tools_debug", "default": "",
        "description": "已废弃。",
    },
    "napcat_tools_max_result": {
        "type": "string", "label": "（已废弃）napcat_tools_max_result", "default": "",
        "description": "已废弃。",
    },
    "napcat_tools_log_enabled": {
        "type": "string", "label": "（已废弃）napcat_tools_log_enabled", "default": "",
        "description": "已废弃。",
    },
    "napcat_tool_overrides": {
        "type": "string", "label": "（已废弃）napcat_tool_overrides", "default": "",
        "description": "已废弃。",
    },
"system_tools_enabled": {
        "type": "dynamic", "label": "（已废弃）系统工具开关", "default": {},
        "description": "已废弃，请改用工具总览页统一关闭。",
    },
    "module_tools_enabled": {
        "type": "dynamic", "label": "（已废弃）模块工具开关", "default": {},
        "description": "已废弃，请改用工具总览页统一关闭。",
    },
    "mcp_tools_enabled": {
        "type": "dynamic", "label": "（已废弃）MCP 工具开关", "default": {},
        "description": "已废弃，请改用工具总览页统一关闭。",
    },
"system_tools_log_enabled": {
        "type": "boolean", "label": "记录系统工具调用", "default": True,
        "description": "在日志里记录系统工具的调用与结果。",
    },
    "module_tools_log_enabled": {
        "type": "boolean", "label": "记录模块工具调用", "default": True,
        "description": "在日志里记录模块工具的调用与结果。",
    },
    "mcp_tools_log_enabled": {
        "type": "boolean", "label": "记录 MCP 工具调用", "default": True,
        "description": "在日志里记录 MCP 工具的调用与结果。",
    },
    "onebot_tools_log_enabled": {
        "type": "boolean", "label": "记录 OneBot 工具调用", "default": True,
        "description": "在日志里记录 OneBot 工具的调用与结果。",
    },
"tavily_enable": {
        "type": "boolean", "label": "启用 Tavily 搜索", "description": "开启后模型可联网搜索。",
        "default": False, "group": "group_tavily",
    },
    "tavily_api_key": {
        "type": "password", "label": "Tavily API Key", "description": "在 https://app.tavily.com 获取，格式 tvly-xxx",
        "default": "", "group": "group_tavily",
    },
    "tavily_max_results": {
        "type": "number", "label": "默认搜索条数", "description": "搜索工具默认返回的结果数量。",
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
        "type": "textarea", "label": "MCP Servers (JSON 数组)", "description": "MCP 服务列表（JSON 数组），每项含名称、启动命令、参数与环境变量。",
        "default": "[]", "rows": 5, "group": "group_mcp",
    },

    # ==================== 工具调用 ====================
    "max_tool_rounds": {
        "type": "number", "label": "工具调用轮数上限",
        "description": "一轮回复中「调工具、回填、再判断」的最大轮数。",
        "default": 5, "min": 1, "max": 20, "group": "group_tools",
    },
    "empty_reply_retries": {
        "type": "number", "label": "空回复重试次数",
        "description": "模型返回空内容时的重试次数。0 = 不重试。",
        "default": 1, "min": 0, "max": 3, "group": "group_tools",
    },
    "reasoning_overflow_retries": {
        "type": "number", "label": "思考吃满预算的收敛重试",
        "description": "思考占满输出、正文为空时的收敛重试次数。0 = 不重试。",
        "default": 1, "min": 0, "max": 2, "group": "group_tools",
    },
    # P3：工具执行契约（并发池大小与单次超时）。工具自己可以用 ToolSpec.timeout_s
    # 声明更严/更宽的上限，这里给的是没声明时的全局默认。
    "tool_parallel_max": {
        "type": "number", "label": "工具并发上限",
        "description": "同一时刻最多几个「可并行」工具一起跑。1 = 全串行；独占工具始终单独执行。",
        "default": 8, "min": 1, "max": 32, "group": "group_tools",
    },
    "tool_timeout_default_s": {
        "type": "number", "label": "工具默认超时（秒）",
        "description": "工具没声明自己的超时就用这个值。超时只把结果换成失败回执，工具协程仍会跑完。",
        "default": 20, "min": 1, "max": 600, "group": "group_tools",
    },
    # P5：超大工具结果落盘（消息里只留预览 + 取回路径）
    "tool_spill_enable": {
        "type": "boolean", "label": "超大结果落盘",
        "description": "工具结果超过阈值时把全文落盘、消息里只留预览与路径，模型可再调用取回工具读回全文。",
        "default": True, "group": "group_tools",
    },
    "tool_spill_max_bytes": {
        "type": "number", "label": "落盘阈值（字符）",
        "description": "工具结果超过多少字符就落盘。默认 50000。",
        "default": 50000, "min": 1024, "max": 2000000, "group": "group_tools",
    },
    # P6：高危工具人工审批（fail-closed）
    "tool_approval_enable": {
        "type": "boolean", "label": "高危工具需人工审批",
        "description": "OneBot 的高风险工具执行前先请拥有者确认；没有可用审批通道时一律拒绝。",
        "default": False, "group": "group_permission",
    },
    "retry_profile": {
        "type": "select", "label": "重试档位",
        "description": "一轮对话的请求次数与输入 token 上限。抗压档更耐故障，消耗也更大。",
        "default": "balanced", "group": "group_tools",
        "options": {
            "thrifty": "节流（省钱优先）",
            "balanced": "均衡（推荐）",
            "resilient": "抗压（可用性优先）",
        },
    },
    "turn_token_budget": {
        "type": "number", "label": "单轮输入 Token 上限",
        "description": "一轮对话累计输入 token 上限。0 = 不限制。",
        "default": 0, "min": 0, "max": 2000000, "group": "group_tools",
    },
    "image_understanding_enable": {
        "type": "boolean", "label": "图片理解（多模态同传）",
        "description": "把本轮图片直接传给模型，仅对支持图片的模型生效。",
        "default": True, "group": "group_tools",
    },
    "image_max_count": {
        "type": "number", "label": "单轮最多图片数",
        "description": "一次请求最多附带几张图，超出部分不再传给模型（避免 token 与带宽失控）",
        "default": 4, "min": 1, "max": 10, "group": "group_tools",
    },
    "image_max_mb": {
        "type": "number", "label": "单张图片体积上限(MB)",
        "description": "超过该体积的图片跳过传输并记录日志（base64 会让请求体膨胀约 1/3，过大易被网关拒绝）",
        "default": 10, "min": 1, "max": 50, "group": "group_tools",
    },
    "gif_understanding_enable": {
        "type": "boolean", "label": "动图/表情包拆帧理解",
        "description": "把动图拆成多帧一起传给模型，否则只能看到第一帧。",
        "default": True, "group": "group_tools",
    },
    "gif_max_frames": {
        "type": "number", "label": "动图最多拆几帧",
        "description": "单条消息内所有动图共享的拆帧数，帧数越多消耗越大。",
        "default": 6, "min": 1, "max": 16, "group": "group_tools",
    },
    "gif_frame_max_edge": {
        "type": "number", "label": "动图帧长边上限(像素)",
        "description": "每帧缩放后的最长边。512 兼顾清晰度与体积；"
                       "调小可显著省 token（384 左右对表情包语义几乎无损）",
        "default": 512, "min": 256, "max": 1024, "group": "group_tools",
    },
    "gif_probe_all_images": {
        "type": "boolean", "label": "所有图片都探测动图",
        "description": "对每张图都探测是否为动图。动图识别不到时可开启，代价是多一次下载。",
        "default": False, "group": "group_tools",
    },
    "history_background_enable": {
        "type": "boolean", "label": "注入在线聊天环境背景",
        "description": "额外注入最近群聊记录作为背景。",
        "default": True, "group": "group_tools",
    },
    "tool_result_directive_enable": {
        "type": "boolean", "label": "工具结果附带回应要求",
        "description": "在工具结果后追加一句回应要求，避免模型写成长篇汇报。",
        "default": True, "group": "group_tools",
    },
    "tool_result_directive": {
        "type": "textarea", "label": "回应要求文本",
        "description": "留空=使用内置默认。可改成更贴合角色语气的说法（例如“用你平时的语气说一两句就够了”）",
        "default": "", "rows": 3, "group": "group_tools",
    },

    # ==================== OneBot 工具 ====================
    "onebot_tools_enable": {
        "type": "boolean", "label": "启用 OneBot 工具", "description": "把 OneBot API 暴露给 LLM 作为 function calling 工具",
        "default": False, "group": "group_onebot",
    },
    "onebot_tools_allowed": {
        "type": "string_list", "label": "允许的工具（白名单）", "description": "留空表示允许全部；非空时只允许列出的工具",
        "default": [], "group": "group_onebot",
    },
    "onebot_tools_denied": {
        "type": "string_list", "label": "禁用的工具（黑名单）", "description": "黑名单优先于白名单，可在此关闭敏感工具",
        "default": [], "group": "group_onebot",
    },
    "onebot_tools_max_result": {
        "type": "number", "label": "返回结果截断长度", "description": "API 返回内容超过该长度后截断，防止撑爆上下文",
        "default": 2000, "min": 100, "max": 20000, "group": "group_onebot",
    },
    "onebot_tools_debug": {
        "type": "boolean", "label": "OneBot 调试日志", "description": "开启后完整记录每个 OneBot 工具调用的请求参数与响应内容",
        "default": False, "group": "group_onebot",
    },
    "poke_cooldown_seconds": {
        "type": "number", "label": "戳一戳节流(秒)", "description": "同一会话对同一人的戳一戳冷却。0 = 不节流。",
        "default": 20, "min": 0, "max": 600, "group": "group_onebot",
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
        "type": "boolean", "label": "句子型昵称脱敏", "description": "把像句子或超长的昵称替换为「用户+QQ号」。",
        "default": True, "group": "group_memory",
    },
}

# ==================== 条件显隐（showIf） ====================
# 目标：把「只在某个功能开启后才有意义」的调参项收起来，界面上不生一堆无用开关。
#
# 条件文法（前后端共用，前端 config/utils 的 isVisible 实现同一套语义）：
#   {"key": k, "value": v}      —— 相等
#   {"key": k, "in": [...]}     —— 取值在集合内（多选一）
#   {"key": k, "notIn": [...]}  —— 取值不在集合内
#   {"key": k, "gt": n}         —— 数值大于 n
#   {"key": k, "notEmpty": true}—— 非空（字符串 / 列表）
#   {"allOf": [...]} / {"anyOf": [...]}
# 未列出的字段一律常显示。

# 「哪个字段出现在哪个前端分区」不在这里：唯一权威是
# app/webui/dashboard/src/agent/sections.json；tests/test_agent_section_contract.py 校验覆盖一致性。


def _on(key: str) -> dict:
    """布尔型父开关：``key`` 为真时才显示。"""
    return {"key": key, "value": True}


def _gate(fields: list[str], cond: dict) -> None:
    """给 ``fields`` 追加一个显隐条件；已有条件则合成为 allOf（全部满足才显示）。"""
    for key in fields:
        definition = SCHEMA.get(key)
        if not isinstance(definition, dict):
            continue
        existing = definition.get("showIf")
        if not existing:
            definition["showIf"] = cond
        elif isinstance(existing, dict) and "allOf" in existing:
            existing["allOf"].append(cond)
        else:
            definition["showIf"] = {"allOf": [existing, cond]}


def _group_fields(group: str, *, exclude: tuple[str, ...] = ()) -> list[str]:
    return [
        key
        for key, definition in SCHEMA.items()
        if isinstance(definition, dict)
        and definition.get("group") == group
        and key not in exclude
    ]


# ── 长期记忆：两级门（实验开关 → 记忆总开关 → 其余细调） ──────────────
_gate(["memory_enable"], _on("experimental_long_term_memory"))
_gate(
    _group_fields("group_memory", exclude=("experimental_long_term_memory", "memory_enable")),
    _on("memory_enable"),
)
_gate(_group_fields("group_memory", exclude=("experimental_long_term_memory",)), _on("experimental_long_term_memory"))

# ── 流式回复：除总开关外，所有节奏参数都只在流式开启后才有意义 ────────
_gate(_group_fields("group_stream", exclude=("stream_output",)), _on("stream_output"))

# 间隔参数只在「固定间隔 / 按字数曲线」下有效
_gate(
    ["stream_send_interval_base_ms", "stream_send_interval_min_ms", "stream_send_interval_max_ms"],
    {"key": "stream_send_interval_mode", "in": ["fixed", "length_curve"]},
)
# 曲线形状与系数只在「按字数曲线」下有效
_gate(["stream_send_curve", "stream_send_curve_k"], {"key": "stream_send_interval_mode", "in": ["length_curve"]})
# 短/长句延迟只属于 short_long 曲线
_gate(
    ["stream_short_message_length", "stream_short_message_delay_ms", "stream_long_message_delay_ms"],
    {"key": "stream_send_curve", "value": "short_long"},
)
# 队列参数只在消息池开启后有效
_gate(["stream_send_max_queue", "stream_queue_full_policy"], _on("stream_send_pool_enabled"))
# 复用流式的两处开关也依赖流式总开关
_gate(["stream_proactive_enabled", "stream_scheduled_enabled"], _on("stream_output"))

# ── 上下文：群聊记录窗口 / 指代消解 / 压缩 / token 预算 ──────────────
_gate(["group_log_window_minutes", "group_log_window_limit", "group_log_max_chars"], _on("group_log_enable"))
_gate(
    ["referent_prefetch_enable", "referent_ambiguous_policy", "referent_prefetch_max", "referent_focus_ttl", "referent_focus_max"],
    _on("referent_resolve_enable"),
)
_gate(["context_compress_prompt"], _on("context_compress_enable"))
_gate(["context_token_chars_per_token"], {"key": "context_token_budget", "gt": 0})

# ── 消息抖动 ──────────────────────────────────────────────────────
_gate(["message_debounce_max_wait"], {"key": "message_debounce_seconds", "gt": 0})

# ── 能力开关：各自只在启用后展开参数 ────────────────────────────────
_gate(_group_fields("group_tavily", exclude=("tavily_enable",)), _on("tavily_enable"))
_gate(_group_fields("group_knowledge", exclude=("knowledge_enable",)), _on("knowledge_enable"))
_gate(_group_fields("group_onebot", exclude=("onebot_tools_enable",)), _on("onebot_tools_enable"))
_gate(["image_max_count", "image_max_mb"], _on("image_understanding_enable"))
_gate(["gif_max_frames", "gif_frame_max_edge", "gif_probe_all_images"], _on("gif_understanding_enable"))
_gate(["schedule_prompt"], _on("schedule_enable"))

# ── 主动消息：私聊 / 群聊各自展开 ──────────────────────────────────
_gate(
    [
        "proactive_friend_sessions",
        "proactive_min_interval_minutes",
        "proactive_max_interval_minutes",
        "proactive_max_unanswered",
        "proactive_quiet_hours_start",
        "proactive_quiet_hours_end",
        "proactive_prompt",
    ],
    _on("proactive_friend_enable"),
)
_gate(["proactive_group_sessions", "proactive_group_idle_minutes"], _on("proactive_group_enable"))

# ==================== 前端重要性元数据 ====================
# field 级（不再按组统一）：只有「用户真的会去改」的项标 basic，
# 其余标 advanced —— 前端「只看基础」用它筛选，避免 145 项平铺。
_BASIC_FIELDS = {
    # 基础配置
    "group_enable", "private_enable",
    # 注意：max_tokens 不在这里 —— 它只在模型链全空时兜底，值得改它的地方是
    # 「Provider 预设 → 模型」页，摆在 Agent 首页只会继续误导
    "model", "temperature",
    "system_prompt", "persona_preset_id",
    "assembly_profile", "behavior_rules_enable", "participation", "emoji_react_level",
    # 权限
    "permission", "agent_owner_list", "agent_manager_list",
    # 会话管理
    "session_timeout", "history_rounds", "max_message_length",
    "referent_resolve_enable", "context_compress_enable", "context_token_budget",
    # 对话行为
    "trigger_at", "trigger_keyword", "message_debounce_seconds", "reply_cooldown",
    "interrupt_enable",
    # 流式输出
    "stream_output", "stream_send_by_sentence", "stream_send_interval_mode",
    "stream_send_interval_base_ms",
    # 主动消息 / 定时任务
    "proactive_friend_enable", "proactive_group_enable",
    "proactive_friend_sessions", "proactive_group_sessions",
    "proactive_min_interval_minutes", "proactive_max_interval_minutes",
    "proactive_max_unanswered", "proactive_quiet_hours_start", "proactive_quiet_hours_end",
    "proactive_group_idle_minutes", "schedule_enable",
    # 能力与成本
    "max_tool_rounds", "empty_reply_retries", "reasoning_overflow_retries",
    "memory_enable", "memory_extract_enable",
}

for _key, _def in SCHEMA.items():
    if isinstance(_def, dict) and _def.get("group"):
        _def["importance"] = "basic" if _key in _BASIC_FIELDS else "advanced"

# 契约测试用：声明这些 id 一定会被填充（避免上面拼错 key 却静默无效果）
assert _BASIC_FIELDS <= {k for k, d in SCHEMA.items() if isinstance(d, dict)}, (
    "importance 名单里存在 schema 中不存在的 key"
)

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


# ==================== v3 收敛：本体只剩 5 页 / 43 键 ====================
# 上面 160 个键里有 108 个已迁到 9 个功能模块（见 app/agent/config_partition.py
# 的 `MODULE_KEYS`），另有 9 个废弃键。**导出的 `SCHEMA` 只保留本体 5 页的 43 键**，
# 于是：
#   - Agent 配置页不再出现任何"其实归模块管"的开关；
#   - `LEGACY_SCHEMA` 保留完整旧定义，供迁移脚本按旧键取类型/标签/默认值；
#   - `DEFAULT_LLM_CONFIG`（app/agent/config.py）仍保留这些键的兜底默认值，
#     模块未安装时框架回退到默认值，行为可预期。
from app.agent.config_partition import CORE_KEYS as _CORE_KEYS  # noqa: E402

#: 迁移脚本与对账用：完整的旧 schema（含已迁移/已废弃的键）
LEGACY_SCHEMA: dict = dict(SCHEMA)

#: 模块接管后**不再属于 agent 配置**的键（迁移 + 废弃）
MIGRATED_OR_RETIRED_KEYS: tuple = tuple(
    key for key in LEGACY_SCHEMA if key not in set(_CORE_KEYS)
)

SCHEMA = {key: value for key, value in SCHEMA.items() if key in set(_CORE_KEYS)}
