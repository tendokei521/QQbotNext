"""框架级 Agent 配置（默认值 + AgentConfig 门面）。

P4：配置命名空间从 llm_chat_v2 迁到 agent，首启自动迁移旧模块配置与权限；
AgentConfig 同时承载启停/权限（模块_authority，module_name="agent"），
Agent 的开关不再依赖模块。
"""

from __future__ import annotations

from typing import Any

from app.modules.base import ModulePermission

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
    "retry_attempts": 3,
    "model": "deepseek-chat",
    "system_prompt": "你是一个友好的助手。",
    "max_tokens": 1024,
    "temperature": 0.7,
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
    # MCP（Model Context Protocol）stdio server 配置
    # [{"name":"filesystem","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/tmp"],"env":{},"timeout":30}]
    "mcp_servers": [],
    # NapCat / OneBot 通用工具：数据驱动地暴露 NapCat API 给 LLM
    "napcat_tools_enable": False,      # 总开关
    "napcat_tools_allowed": [],        # 空=全部；非空=只允许白名单
    "napcat_tools_denied": [],         # 黑名单（优先级高于白名单）
    "napcat_tools_max_result": 2000,   # 返回结果截断长度
    "napcat_tools_debug": False,       # 调试：完整记录 NapCat 请求与响应
    "napcat_tool_overrides": {},       # 工具权限/作用域覆盖：{"send_poke":{"permission":"member","scopes":["private"]}}
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


class AgentConfig:
    """框架级 Agent 配置门面：深合并默认值 + 已存配置，读写走 ConfigService。

    配置存 module_config("agent")；启停/黑白名单存 module_authority("agent")。
    首次访问自动从 llm_chat_v2 迁移旧配置与权限。
    """

    def __init__(self, config_service, bot_id: Any) -> None:
        self._service = config_service
        self._bot = bot_id
        self._migrated = False
        self._current_umo: str | None = None

    # ── 会话配置档案（对齐 AstrBot UMO 路由） ───────────────
    def set_session(self, umo: str | None) -> None:
        """设置当前会话的 UMO；存在路由时后续 get/raw_config 会合并对应档案。"""
        self._current_umo = str(umo) if umo else None

    def clear_session(self) -> None:
        self._current_umo = None

    def _profile_config(self) -> dict:
        if not self._current_umo:
            return {}
        routes = self._service.get_config_routes()
        profile_id = routes.get(self._current_umo)
        if not profile_id:
            return {}
        profile = self._service.get_config_profile(profile_id)
        if not profile:
            return {}
        config = profile.get("config", {}) or {}
        return config if isinstance(config, dict) else {}

    # ── 配置读写 ─────────────────────────────────────────
    def get(self, key: str, default: Any = None) -> Any:
        self._ensure_migrated()
        data = self._service.get_module_config(AGENT_CONFIG_MODULE, self._bot) or {}
        if key in data and data[key] is not None:
            return data[key]
        profile = self._profile_config()
        if key in profile and profile[key] is not None:
            return profile[key]
        if key in DEFAULT_LLM_CONFIG and DEFAULT_LLM_CONFIG[key] is not None:
            return DEFAULT_LLM_CONFIG[key]
        return default

    def _base_raw_config(self) -> dict:
        self._ensure_migrated()
        stored = self._service.get_module_config(AGENT_CONFIG_MODULE, self._bot) or {}
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

    @property
    def raw_config(self) -> dict:
        result = self._base_raw_config()
        result.update(self._profile_config())
        return result

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
