"""框架级 Agent 配置（默认值 + AgentConfig 门面）。

P4：配置命名空间从 llm_chat_v2 迁到 agent，首启自动迁移旧模块配置与权限；
AgentConfig 同时承载启停/权限（模块_authority，module_name="agent"），
Agent 的开关不再依赖模块。
"""

from __future__ import annotations

from typing import Any

from app.plugin.base import ModulePermission

DEFAULT_LLM_CONFIG: dict = {
    # 连接参数已抽离到 Provider 预设，Agent 只保存引用；
    # 以下 legacy 字段保留默认值用于兼容存量代码/自动迁移，WebUI 不再展示
    "provider_preset_id": "",
    "provider_model_id": "",
    "provider_model_pool": [],
    "fallback_model_ids": [],
    "api_key": "",
    "api_base": "https://api.deepseek.com",
    "provider": "openai",
    # HTTP 层瞬态重试（429/5xx/超时）次数。默认 2：这一层每次都会重发全量上下文，
    # 配合 retry_profile 的总次数闸门一起控制一轮的真实成本。
    "retry_attempts": 2,
    "system_prompt": "你是一个友好的助手。",
    # 提示词方案（Chat / 角色扮演 / 助手）：只替换**框架生成的 system 文案**
    # （行为段 / 定时协议 / 消息格式说明 / 表情提示 / 主动性行），人格块不受影响。
    # 留空 = 逐字等于改造前（行为段仍只看 assembly_profile），所以选方案是纯增量，
    # 想回退把这里清空即可。可选值见 app/agent/assembly/presets.py::PRESETS。
    "prompt_preset": "",
    # 角色预设**活引用**：填预设 ID 时，每轮组装都按 ID 实时解析预设的提示词与预设对话，
    # 因此改预设名/改提示词立刻生效（不需要"应用一次"把内容拷进 system_prompt）；
    # 留空则完全走下面的 system_prompt —— 旧行为逐字不变。
    "persona_preset_id": "",
    # 消息组装风格：blocks=框架默认（人设/协议/技能/记忆/背景各一条 system）；
    # single=把所有 system 块折成一条（对齐 AstrBot 那种「一整条 system prompt」形态）
    "system_prompt_style": "blocks",
    # 组装档位：legacy=改造前的旧行为（逐字不变）；qq_agent=注入独立的行为规则段
    # （对齐外部拟人化实现：安全/工作方式/反AI味/主体性/该说不该说/群聊节奏…）。
    # 用一个总闸而不是散开关，为的是能一键回退与 A/B。
    "assembly_profile": "legacy",
    # 行为规则细调：总开关 / 整体覆盖（安全规则与工作方式仍强制追加）
    "behavior_rules_enable": True,
    "behavior_rules_override": "",
    # 参与度（改写行为段「该说/不该说」的首行）与贴表情活跃度（0~3，改写频率行）
    "participation": "medium",
    "emoji_react_level": 1,
    # user 侧格式：single_line=本轮消息与历史条目同一形态（昵称(QQ): 正文，默认）；
    # legacy=六行元信息块（时间/群号/发送者/发送了…）。仅 qq_agent 档生效。
    "user_message_format": "single_line",
    # 时间行位置：tail=挪到消息末尾（默认，让逐轮稳定的行靠前，前缀缓存更有效）；head=旧行为
    "prompt_time_position": "tail",
    # 发言通道：True=模型输出的文本只是思考，发言必须调用 send_message（不调用=静默）。
    # 需要 assembly_profile=qq_agent 且 stream_output=True（非流式没有轮次边界可 flush）。
    "speech_via_tool": False,
    # 一轮最多发几条气泡 / 单条气泡字数上限（防复读机式刷屏）
    "speech_max_bubbles": 4,
    "speech_max_bubble_chars": 300,
    # 剩余轮次提醒：剩这么多轮还没开口就注入一条【系统提醒】催发言
    "speech_reminder_rounds": 3,
    # 被直接点名（@/私聊）却一句话没说时，允许再补一轮把话说出来
    "speech_force_reply_on_call": True,
    # 静默时是否仍补一句"抱歉我暂时无法回答"（本档位默认关闭：静默更拟人）
    "speech_fallback_enable": False,
    # 记录历史时强制清洗助手输出里的（…）/(…) 内容，避免后续回复模仿括号风格
    "clean_output_parentheses": True,
    "group_enable": False,
    "private_enable": True,
    "session_timeout": 60,
    "history_rounds": 50,
    # 上下文压缩（仿 AstrBot）：历史超过 history_rounds 时，只压缩超出的部分，保留最近 history_rounds 条原文
    "context_compress_enable": True,
    "context_compress_prompt": "请把上面的历史对话压缩成一份简洁但完整的摘要，用于后续对话无缝续接：\n1. 覆盖所有核心话题及最终结论/结果；\n2. 高亮最近的主要关注点；\n3. 如有工具调用、任务进度或待办，说明当前状态和下一步；\n4. 保留用户的重要个人信息、偏好、称呼等关键事实；\n5. 使用与对话相同的语言输出。\n只输出摘要内容，不要输出额外解释。",
    "max_message_length": 200,
    # 流式输出
    "stream_output": False,
    "stream_sentence_max_length": 200,
    # 流式回复设置（消息池 / 发送频率 / 前后缀）
    "stream_send_pool_enabled": False,
    "stream_send_by_sentence": True,
    "stream_send_interval_mode": "none",       # none / fixed / length_curve
    "stream_send_interval_base_ms": 600,
    "stream_send_interval_min_ms": 100,
    "stream_send_interval_max_ms": 3000,
    "stream_send_curve": "sqrt",               # fixed / sqrt / log / inverse / short_long
    "stream_send_curve_k": 200,
    "stream_short_message_length": 10,
    "stream_short_message_delay_ms": 1200,
    "stream_long_message_delay_ms": 400,
    "stream_send_prefix": "",
    "stream_send_suffix": "",
    "stream_send_max_queue": 20,
    "stream_queue_full_policy": "backpressure", # backpressure / drop_newest / drop_oldest
    "stream_flush_on_finish": False,
    "stream_keep_order": True,
    # 主动消息 / 定时任务也使用流式发送（与普通消息同一套流式配置）
    "stream_proactive_enabled": False,
    "stream_scheduled_enabled": False,
    "trigger_at": False,
    "trigger_keyword": [],
    # 消息抖动：等静默 N 秒再请求，把窗口内多条消息合并成同一轮（0=关闭）
    "message_debounce_seconds": 0,
    "message_debounce_max_wait": 15,
    "include_pre_history": False,
    "include_private_pre_history": "default",
    "reply_cooldown": 5,
    # 用户信息感知（LLM 增强注入：发送者 / 提到 / 引用 / 正文 / 时间；无总开关，按子项生效）
    "include_time": True,
    "include_private_qq": True,     # 私信时独立注入对方 QQ
    "include_sender": True,
    "include_mentioned": True,
    "include_quote": True,
    "include_quote_sender": True,
    "include_sent": True,
    "fetch_at_nickname": True,
    "fetch_quote_content": True,
    # 回复打断
    "interrupt_enable": False,
    "interrupt_save_sent": True,
    "interrupt_debug": False,
    # 权限：Agent 可响应的事件权限角色
    "permission": "member",
    # 主动消息
    "proactive_friend_enable": False,
    "proactive_group_enable": False,
    "proactive_friend_sessions": [],
    "proactive_group_sessions": [],
    "proactive_min_interval_minutes": 30,
    "proactive_max_interval_minutes": 900,
    "proactive_max_unanswered": 3,
    "proactive_quiet_hours_start": 1,
    "proactive_quiet_hours_end": 7,
    "proactive_group_idle_minutes": 10,
    "proactive_prompt": "你在群聊/私聊中发起主动消息，像真人一样自然开口。当前时间：{{current_time}}；之前已主动发言但无人接话的次数：{{unanswered_count}}。结合最近对话，自然地说一句适合此刻的话。",
    # 定时任务
    "schedule_enable": True,
    "schedule_prompt": "你被一个定时任务唤醒，这不是一次用户对话。\n规则：\n1. 这不是聊天轮次：不要打招呼，不要反问用户。\n2. 结合最近的历史对话理解与用户的关系和上下文，用符合你人设的语气自然开口。\n3. 自然地说明你联系的原因，参考任务内容即可，不要提及\"定时任务\"\"工具\"等技术细节。\n4. 当前时间：{{current_time}}；需要完成的事情：{{content}}。\n任务信息：{{job_json}}",
    # 长期记忆（实验性方案：需开启 experimental_long_term_memory 才生效）
    "experimental_long_term_memory": False,  # 实验性总开关：开启后才启用长期记忆 + 新版单行脱敏提示词
    "memory_enable": False,               # 总开关（默认关闭，避免影响真人感）
    "memory_private_enable": True,        # 私聊场景
    "memory_group_enable": True,          # 群聊场景
    "memory_recall_max": 8,               # 注入条数上限
    "memory_recall_max_chars": 600,       # 注入字符上限
    "memory_save_deterministic": True,    # “记住…”确定性兜底
    "memory_extract_enable": True,        # 隐式蒸馏
    "memory_extract_interval_min": 10,    # 蒸馏限频（分钟）
    "memory_max_per_owner": 300,          # 每 owner 条数上限
    "memory_user_cross_group": False,     # 跨群用户画像（默认关）
    "memory_audit_enable": True,          # 事件记录
    "memory_audit_inject": False,         # 是否记录每次注入（默认关，避免日志噪音）
    # 记忆 v2：权威降级 / 置信度 / 状态管理
    "memory_min_confidence": 0.5,         # 注入所需最低置信度（低置信默认不注入）
    "memory_inject_hedge": True,          # 中/低置信条目标“（好像）/（记不太清）”
    "memory_max_age_days": 180,           # 超过此龄不注入（数据保留）
    "memory_on_reset": "suspend",         # 会话重置时：suspend / clear / keep
    "memory_upgrade_saved_only": True,    # 重置 suspend 时只保留“已保存/已确认”型记忆
    # 感知增强提示词细调（实验性；默认全部使用旧版，保持真人感）
    "meta_sender_style": "legacy",        # 发送者标签样式：legacy=发送者： / new=发送者昵称： / single=昵称(QQ): 正文
    "meta_sent_style": "legacy",          # 正文标签样式：legacy=发送了： / new=消息正文：
    "meta_instruction_mode": "legacy",    # 消息元信息消歧说明：off=不注入 / legacy=旧版说明 / new=新版说明
    "meta_mask_nickname": True,           # 是否对句子型/超长昵称脱敏为 用户<QQ>（默认开启，防止昵称内容泄漏进正文）
    # 知识库
    "knowledge_enable": False,            # 知识库总开关
    "knowledge_embedding_model_id": "",   # 指定 embedding 模型实例 id；空则自动选第一个 embedding 模型
    "knowledge_recall_limit": 5,          # 默认检索条数
    "knowledge_recall_max_chars": 2000,   # 检索结果最大输出字符
    # Tavily 联网搜索（系统级工具，不进入 OneBot 前端清单）
    "tavily_enable": False,               # 总开关
    "tavily_api_key": "",                 # Tavily API Key（password 脱敏）
    "tavily_max_results": 5,              # 默认搜索结果条数
    "tavily_search_depth": "basic",       # basic / advanced
    "tavily_max_content_chars": 2000,     # 返回结果最大字符数
    # MCP（Model Context Protocol）stdio server 配置
    # [{"name":"filesystem","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/tmp"],"env":{},"timeout":30}]
    "mcp_servers": [],
    # 统一工具管理：四类工具各自的开关与日志输出
    "system_tools_enabled": {},        # 系统工具开关：{"tool_name": bool}
    "system_tools_log_enabled": True,  # 系统工具普通调用日志
    # 工具调用循环轮数上限（流式与非流式共用）：调大后"展开环境→再判断→再展开"这类
    # 多段链条不会在第 5 轮被硬切（此前流式硬编码 5、非流式走 Provider 默认值，两处不一致）
    "max_tool_rounds": 5,
    # 空回复重试：请求成功结束却既无文本也无工具调用（网关提前断流 / 只回思考内容）时，
    # 同一 provider 再试的次数；0 = 关闭。避免偶现空回复直接把兜底话术发给用户。
    "empty_reply_retries": 1,
    # 思考吃满输出的收敛重试：思考模型把 max_tokens 全花在 reasoning 里、正文没写出来时，
    # 追加一次「不要复述思考、直接作答」的提醒再要一枪（换问法，不是重复同一 prompt——
    # 后者必然同样打满）。只是尽力而为，根治办法是调大「模型」页的 max_tokens 或缩短上下文。
    "reasoning_overflow_retries": 1,
    # P3 工具执行契约：同一时刻最多几个「声明为可并行」的工具一起跑（1 = 全串行；
    # 独占工具永远单独执行）。池子是有界的：批量工具不会再无上限地同时打出去。
    "tool_parallel_max": 8,
    # 工具没声明 ``ToolSpec.timeout_s`` 时的墙钟上限（秒）。超时只替换**结果**
    # （TOOL_TIMEOUT），不 cancel 协程——否则会把"发了一半的消息/写了一半的文件"掐断。
    "tool_timeout_default_s": 20,
    # 超大工具结果落盘（P5）：超过 tool_spill_max_bytes 就把全文写到
    # data/llm/<bot>/spill/<session>/<sha16>.txt，消息里只留预览 + 路径，
    # 模型可用 recall_tool_result 取回。关掉 = 回到"直接截断"的老行为。
    "tool_spill_enable": True,
    "tool_spill_max_bytes": 50000,
    # 高危工具人工审批（P6）：打开后，OneBot 里 sensitivity 为 high/critical 的工具
    # 在执行前必须先问拥有者；**没有审批通道 → 拒绝**（fail-closed）。
    "tool_approval_enable": False,
    # 重试档位（thrifty / balanced / resilient）：决定**一整轮**最多能发几次请求、
    # 最多累计烧多少输入 token（见 request_policy.TurnBudget）。这是硬闸门——
    # 没有它，"HTTP 重试 × 空回复重试 × 收敛重试 × provider 数"会乘成十几次，
    # 每次都带着全量上下文（线上为此把 16.7 万 token 发了三遍）。
    "retry_profile": "balanced",
    # 一轮对话累计输入 token 上限：0 = 不设上限（只用档位的次数闸门）。
    # 超过就停止重试并如实告知，而不是继续把同样的上下文重发。
    "turn_token_budget": 0,
    # 多模态图片同传：仅对「模型配置里声明了 image 模态」的模型生效（在「模型」页勾选）。
    # 文本模型的 [图片] 占位语义完全不变。
    "image_understanding_enable": True,
    "image_max_count": 4,        # 单轮最多传几张图（超出部分只留占位）
    "image_max_mb": 10,          # 单张图体积上限（MB），超出跳过并记日志
    # 动图/表情包拆帧：视觉模型对 GIF 往往只读第一帧，框架自己拆成若干静态帧
    # 才能"看到"整段表情变化。帧数与尺寸直接决定 token，默认值偏保守。
    "gif_understanding_enable": True,
    "gif_max_frames": 6,          # 单张动图最多拆几帧（多条动图共享这份预算）
    "gif_frame_max_edge": 512,    # 每帧长边上限（像素）：512 是"看得清又不贵"的平衡点
    # 线索命中才会下载字节确认（省流量）；NapCat 之外的实现若不给 summary/后缀，
    # 打开这个开关可让所有图片都探测一次 n_frames（代价：每张图多一次下载）
    "gif_probe_all_images": False,
    # 会话历史是主历史来源；在线「聊天环境背景」默认仍注入（指代行保留）。
    # 关掉后历史只来自会话记录，避免同一批消息在两处重复出现。
    "history_background_enable": True,
    # 工具结果尾部的「回应要求」：压住"工具返回后写长串汇报"的倾向
    # （拼在结果末尾而不是新增 system —— 中途 system 在 Anthropic/Gemini 会被上提到最前）
    "tool_result_directive_enable": True,
    "tool_result_directive": "",       # 空=用内置默认（见 tool_loop.DEFAULT_REPLY_DIRECTIVE）
    # get_chat_history：本地记录少于该条数时自动补拉 QQ 聊天记录（0=总是补拉）
    "history_auto_qq_min_local": 4,
    # get_chat_history 跨会话：私聊里查询"自己也是成员"的群历史（群聊里一律拒绝）
    "history_cross_query_enable": True,
    # 上下文「骨架补全」：聊天记录里展不开的内容标成【未展开:...】并允许按需展开
    # （与 expand_context 工具同开同关；@ 昵称预展开沿用上面的 fetch_at_nickname）
    "context_expand_enable": True,
    # 指代消解（设计基准 docs/referent-resolution-design.md）
    "referent_resolve_enable": True,           # 总开关（焦点表 + 预取 + 焦点行）
    "referent_prefetch_enable": True,          # 确定性预取：有明确指向时框架直接取回
    "referent_ambiguous_policy": "fetch_all",  # fetch_all / focus_first / ask
    "referent_prefetch_max": 2,                # 一轮最多预取几个候选
    "referent_focus_ttl": 1800,                # 焦点项存活秒数
    "referent_focus_max": 12,                  # 每会话焦点项上限
    "referent_prompt_enable": True,            # 「指代解析」提示行（按能力裁剪）
    # 工具主动性提示词（唯一一块「主动性」system 块）
    "proactive_prompt_enable": True,          # 是否注入「主动性」协议块
    "proactive_history_intent_nudge": True,   # 命中历史意图时在同一块内补强
    "proactive_env_prompt_enable": True,      # 环境行：教模型何时该自己取聊天环境信息
    "proactive_unresolved_prompt_enable": True,  # 未展开标记语义行（需 expand_context 可用）
    "proactive_env_intent_nudge": True,       # 命中环境意图时在同一块内补强
    # 输出侧动作通道：模型可用 [reply] 引用当前消息、[@QQ] 真实 @ 某人
    "outbound_directive_enable": True,        # 总开关（关闭后标记不再解析，也不会教模型用）
    "outbound_directive_max_at": 3,           # 单条回复最多 @ 人数
    "module_tools_enabled": {},        # 模块工具开关：{"tool_name": bool}
    "module_tools_log_enabled": True,  # 模块工具普通调用日志
    "mcp_tools_enabled": {},           # MCP 工具开关：{"tool_name": bool}
    "mcp_tools_log_enabled": True,     # MCP 工具普通调用日志
    # OneBot 通用工具：数据驱动地暴露 OneBot API 给 LLM
    "onebot_tools_enable": False,      # 总开关
    "onebot_tools_allowed": [],        # 空=全部；非空=只允许白名单
    "onebot_tools_denied": [],         # 黑名单（优先级高于白名单）
    "onebot_tools_max_result": 2000,   # 返回结果截断长度
    "onebot_tools_debug": False,       # 调试：完整记录 OneBot 请求与响应
    "onebot_tools_log_enabled": True,  # OneBot 工具普通调用日志
    "onebot_tool_overrides": {},       # 工具权限/作用域覆盖：{"send_poke":{"permission":"member","scopes":["private"]}}
    "poke_cooldown_seconds": 20,       # 戳一戳节流：同会话同一人该秒数内不重复戳（0=不节流）
    # 群聊环境记录（由「群聊记录」模块持续写入，这里只管"一次请求塞多少"）
    "group_log_enable": True,          # 是否把群聊记录作为环境块注入（模块未装/未启用时自动为空）
    "group_log_window_minutes": 60,    # 组装窗口：最近多少分钟
    "group_log_window_limit": 50,      # 组装窗口：最多多少条
    "group_log_max_chars": 4000,       # 环境块字符预算（超预算从最旧开始丢；0=不限制）
    # 请求 messages 落盘（迁移对账用）：开启后写到 data/llm/<bot_id>/prompt_dump.jsonl，
    # 滚动保留最近 prompt_dump_max_entries 条，供与第三方框架的组装结果逐条 diff
    "prompt_dump_enable": False,       # 默认关闭：正常运行时零 IO
    "prompt_dump_max_entries": 20,     # 滚动保留条数
    # 在线不变式（P4）：每次请求前核对「事件日志派生结果」与「内存历史」是否一致，
    # 不等就落 data/llm/<bot>/prompt_dump/desync-<ts>.json 并留痕。默认关闭（有开销）。
    "prompt_dump_verify": False,
    # 上下文 token 预算（对齐 AstrBot ContextManager）：0 = 关闭（默认）。
    # 开启后超预算就从**最旧的历史消息**丢起，system 与最后一条 user 永不丢；
    # 估算按字符启发式（context_token_chars_per_token），只用于防爆、不用于计费。
    "context_token_budget": 0,
    "context_token_chars_per_token": 2.0,
}

# 框架级 Agent 配置/权限存储的 module_name
AGENT_CONFIG_MODULE = "agent"
# 旧模块（迁移源）
LEGACY_CONFIG_MODULE = "llm_chat_v2"

# 旧的内联连接字段：已由 Provider 预设取代，接口返回时需过滤，避免泄露密钥
LEGACY_LLM_CONNECTION_KEYS = ("api_key", "api_base", "provider", "retry_attempts")

# 默认权限（黑名单 + 空列表 = 放行所有）
_DEFAULT_AUTHORITY: dict = {
    "enabled": True,
    "group_mode": "blacklist",
    "group_list": [],
    "user_mode": "blacklist",
    "user_list": [],
}


def legacy_key_of(key: str) -> str:
    """新键 → 旧键（``onebot_tools_*`` → ``napcat_tools_*``）。

    「NapCat 工具」这套配置在 2026-09 改名成「OneBot 工具」（协议名字才是 OneBot），
    但用户库里存的还是旧键。没有这层映射，升级后读不到旧键就会回落到默认值——
    ``onebot_tools_enable`` 默认 False，等于把用户已经打开的开关**静默关掉**。
    """
    text = str(key or "")
    if text.startswith("onebot_"):
        return "napcat_" + text[len("onebot_"):]
    return ""


def _pick(candidates: list[tuple[dict, str]], fallback: Any) -> Any:
    """按顺序取第一个存在的非空值（``item`` 可为 None）。"""
    for data, key in candidates:
        if not isinstance(data, dict):
            continue
        if key in data and data[key] is not None:
            return data[key]
    return fallback


def module_owned_keys() -> frozenset[str]:
    """所有已被模块接管的配置键（写进 agent 配置无效，必须写模块配置）。"""
    try:
        from app.agent.config_partition import MODULE_KEYS

        keys: set[str] = set()
        for owned in MODULE_KEYS.values():
            keys.update(owned)
        return frozenset(keys)
    except Exception as e:  # noqa: BLE001 —— 分区表读不到就不拦（保持可用）
        from app.core.logger import logger

        logger.debug(f"[Config] 读取模块键清单失败（不做拦截）: {e}")
        return frozenset()


class AgentConfig:
    """框架级 Agent 配置门面：深合并默认值 + 已存配置，读写走 ConfigService。

    配置存 module_config("agent")；启停/黑白名单存 module_authority("agent")。
    首次访问自动从 llm_chat_v2 迁移旧配置与权限。
    """

    def __init__(self, config_service, bot_id: Any) -> None:
        self._service = config_service
        self._bot = bot_id
        self._migrated = False

    # ── 配置读写 ─────────────────────────────────────────
    def get(self, key: str, default: Any = None) -> Any:
        self._ensure_migrated()
        data = self._service.get_module_config(AGENT_CONFIG_MODULE, self._bot) or {}
        legacy = legacy_key_of(key)
        candidates = [(data, key)]
        if legacy:
            candidates.append((data, legacy))
        value = _pick(candidates, None)
        if value is not None:
            return value
        if key in DEFAULT_LLM_CONFIG and DEFAULT_LLM_CONFIG[key] is not None:
            return DEFAULT_LLM_CONFIG[key]
        return default

    def _adapted_stored(self) -> dict:
        """已存配置 → 新键口径。

        只做 ``napcat_* → onebot_tools_*`` 的**内存适配**（不改用户数据）：新键存在时
        以新键为准，旧键仅在缺少新键时补位。这样「升级不丢设置」且「新键为唯一事实源」。
        """
        self._ensure_migrated()
        raw = self._service.get_module_config(AGENT_CONFIG_MODULE, self._bot) or {}
        if not isinstance(raw, dict):
            return {}
        adapted = dict(raw)
        for key in DEFAULT_LLM_CONFIG:
            legacy = legacy_key_of(key)
            if not legacy or key in adapted:
                continue
            if legacy in raw and raw[legacy] is not None:
                adapted[key] = raw[legacy]
        return adapted

    def _base_raw_config(self) -> dict:
        stored = self._adapted_stored()
        result: dict = {}
        for key, default_value in DEFAULT_LLM_CONFIG.items():
            if key in stored and stored[key] is not None:
                result[key] = stored[key]
            else:
                result[key] = default_value
        for key, value in stored.items():
            if key not in result:
                result[key] = value
        return result

    def explicit_values(self, keys: Any = None) -> dict:
        """只返回**显式设置过**的键值（供 provider 层做"值来自哪一层"的溯源）。

        ``_base_raw_config()`` 会把每个键都铺上默认值，光看它无法区分"用户设成 1024"
        和"没人动过、默认就是 1024"。这里只挑用户真的动过的键，让
        ``request_policy.merge_config_layers`` 能把来源如实标成 agent 层或内置默认。
        """
        keys = list(keys) if keys is not None else list(DEFAULT_LLM_CONFIG)
        stored = self._adapted_stored()
        result: dict = {}
        for key in keys:
            if key in stored and stored[key] is not None:
                result[key] = stored[key]
                continue
            legacy = legacy_key_of(key)
            if not legacy:
                continue
            if legacy in stored and stored[legacy] is not None:
                result[key] = stored[legacy]
        return result

    def has_explicit(self, key: str) -> bool:
        """用户是否**显式**设置过该键（而不是吃默认值）。

        用于区分「用户把 max_tokens 改成 8192」和「没人动过、拿的是默认 1024」——
        前者被 provider 层覆盖时必须告警，后者只是默认值恰好更大/更小，告警就是噪音。
        """
        stored = self._adapted_stored()
        if key in stored and stored[key] is not None:
            return True
        legacy = legacy_key_of(key)
        if legacy and legacy in stored and stored[legacy] is not None:
            return True
        return False

    @property
    def raw_config(self) -> dict:
        return self._base_raw_config()

    def set(self, key: str, value: Any, auto_save: bool = True) -> None:
        self._ensure_migrated()
        data = self._base_raw_config()
        data[key] = value
        self._service.set_module_config(AGENT_CONFIG_MODULE, self._bot, data, persist=auto_save)

    def save(self) -> None:
        self._service.set_module_config(AGENT_CONFIG_MODULE, self._bot, self._base_raw_config(), persist=True)

    async def save_async(self) -> None:
        await self._service.save_module_config(AGENT_CONFIG_MODULE, self._bot, self._base_raw_config())

    # ── 启停 / 权限（module_authority("agent")） ──────────
    def _authority(self) -> dict:
        auth = self._service.get_module_authority(AGENT_CONFIG_MODULE, self._bot) or {}
        return {**_DEFAULT_AUTHORITY, **auth}

    @property
    def enabled(self) -> bool:
        return bool(self._authority().get("enabled", True))

    @enabled.setter
    def enabled(self, value: bool) -> None:
        auth = self._authority()
        auth["enabled"] = bool(value)
        self._service.set_module_authority(AGENT_CONFIG_MODULE, self._bot, auth)

    @property
    def permission(self) -> ModulePermission:
        auth = self._authority()
        return ModulePermission(
            group_mode=auth.get("group_mode", "blacklist"),
            group_list=list(auth.get("group_list", []) or []),
            user_mode=auth.get("user_mode", "blacklist"),
            user_list=list(auth.get("user_list", []) or []),
        )

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def update_permission(self, group_mode: str, group_list, user_mode: str, user_list) -> None:
        auth = self._authority()
        auth["group_mode"] = group_mode
        auth["group_list"] = list(group_list or [])
        auth["user_mode"] = user_mode
        auth["user_list"] = list(user_list or [])
        self._service.set_module_authority(AGENT_CONFIG_MODULE, self._bot, auth)

    # ── 旧模块迁移 ───────────────────────────────────────
    def _ensure_migrated(self) -> None:
        if self._migrated:
            return
        self._migrated = True
        self.migrate_from_legacy()

    def migrate_from_legacy(self) -> bool:
        """从 llm_chat_v2 迁移配置与权限到 agent（幂等：agent 已有数据则跳过）。"""
        changed = False
        # 配置
        agent_cfg = self._service.get_module_config(AGENT_CONFIG_MODULE, self._bot) or {}
        legacy_cfg = self._service.get_module_config(LEGACY_CONFIG_MODULE, self._bot) or {}
        if not agent_cfg and legacy_cfg:
            self._service.set_module_config(AGENT_CONFIG_MODULE, self._bot, dict(legacy_cfg), persist=False)
            changed = True
        # 权限
        agent_auth = self._service.get_module_authority(AGENT_CONFIG_MODULE, self._bot) or {}
        legacy_auth = self._service.get_module_authority(LEGACY_CONFIG_MODULE, self._bot) or {}
        if "enabled" not in agent_auth and legacy_auth:
            merged = {**_DEFAULT_AUTHORITY, **legacy_auth}
            self._service.set_module_authority(AGENT_CONFIG_MODULE, self._bot, merged)
            changed = True
        if changed:
            try:
                import asyncio
                asyncio.create_task(self._persist_migrated())
            except RuntimeError:
                pass
        return changed

    async def _persist_migrated(self) -> None:
        await self._service.save_module_config(AGENT_CONFIG_MODULE, self._bot, dict(self.raw_config))
