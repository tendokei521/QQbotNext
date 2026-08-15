"""框架级 Agent 配置（默认值 + AgentConfig 门面）。

P4：配置命名空间从 llm_chat_v2 迁到 agent，首启自动迁移旧模块配置与权限；
AgentConfig 同时承载启停/权限（模块_authority，module_name="agent"），
Agent 的开关不再依赖模块。
"""

from __future__ import annotations

from typing import Any

from app.modules.base import ModulePermission

DEFAULT_LLM_CONFIG: dict = {
    "api_key": "",
    "api_base": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "provider": "openai",
    "retry_attempts": 3,
    "system_prompt": "你是一个友好的助手。",
    "max_tokens": 1024,
    "temperature": 0.7,
    "group_enable": False,
    "private_enable": True,
    "session_timeout": 60,
    "history_rounds": 50,
    "max_message_length": 50,
    # 流式输出
    "stream_output": False,
    "stream_sentence_max_length": 50,
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
    "stream_flush_on_finish": True,
    "stream_keep_order": True,
    # 主动消息 / 定时任务也使用流式发送（与普通消息同一套流式配置）
    "stream_proactive_enabled": False,
    "stream_scheduled_enabled": False,
    "trigger_at": False,
    "trigger_keyword": [],
    "include_pre_history": False,
    "include_private_pre_history": "default",
    "reply_cooldown": 5,
    # 权限：Agent 可响应的事件权限角色
    "permission": "member",
    # 调试：开启后打印本轮 prompt
    "debug_prompt": False,
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
}

# 框架级 Agent 配置/权限存储的 module_name
AGENT_CONFIG_MODULE = "agent"
# 旧模块（迁移源）
LEGACY_CONFIG_MODULE = "llm_chat_v2"

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

    # ── 配置读写 ─────────────────────────────────────────
    def get(self, key: str, default: Any = None) -> Any:
        self._ensure_migrated()
        data = self._service.get_module_config(AGENT_CONFIG_MODULE, self._bot) or {}
        if key in data and data[key] is not None:
            return data[key]
        if key in DEFAULT_LLM_CONFIG and DEFAULT_LLM_CONFIG[key] is not None:
            return DEFAULT_LLM_CONFIG[key]
        return default

    @property
    def raw_config(self) -> dict:
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

    def set(self, key: str, value: Any, auto_save: bool = True) -> None:
        self._ensure_migrated()
        data = dict(self.raw_config)
        data[key] = value
        self._service.set_module_config(AGENT_CONFIG_MODULE, self._bot, data, persist=auto_save)

    def save(self) -> None:
        self._service.set_module_config(AGENT_CONFIG_MODULE, self._bot, dict(self.raw_config), persist=True)

    async def save_async(self) -> None:
        await self._service.save_module_config(AGENT_CONFIG_MODULE, self._bot, dict(self.raw_config))

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
