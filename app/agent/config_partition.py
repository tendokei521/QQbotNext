"""agent 配置的**最终布局**（v3）与每个键的命运 —— 全仓库唯一真相。

## 三条硬性事实

1. **本体界面只保留 5 页**（顺序即展示顺序）：
   基础配置 → 权限管理 → 对话行为 → 会话管理 → 高级功能。
   本文件里的 ``UI_PAGES`` 就是这 5 页的字段归属，前端 `sections.json` 应**由它生成**，
   不再手写。
2. **其余全部迁移为模块**（9 个），成为该模块的 ``default_config``。
3. **剩余少量键删除**（能力被别处取代，或改为框架内硬编码的安全默认）。

## 一次边界变更：``owner_id`` 并入权限管理

旧模型：``bots.owner_id``（每个连接一个）→ ``BotConnection.owner_id`` → ``event.owner_id``
→ ``authority.compute_event_permission`` 里 ``is_bot_owner``。

新模型：**不再有账号级 owner_id**，拥有者身份只来自 Agent 配置的 ``agent_owner_list``
（本来就有、且按 bot_id 存，层级与旧 owner_id 等价）。迁移脚本把旧值并进名册。

## 判定口径（新增键必须先归类）

| 归宿 | 判据 |
|---|---|
| UI | 用户"开箱就要能调"的东西：场景开关 / 权限 / 触发与抖动 / 会话窗口 / 图片与联网 |
| MODULE | 一条**策略**（"要不要/怎么做"），归某个能力模块自己决定 |
| DELETE | 能力被别处取代，或只是开发者调参，或改为硬编码安全默认 |
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UIGroup:
    """本体界面里的一页内的一个分组。"""

    id: str
    label: str
    fields: tuple[str, ...]


@dataclass(frozen=True)
class UIPage:
    """本体界面的一个页签。``order`` 即页面顺序。"""

    id: str
    label: str
    groups: tuple[UIGroup, ...]
    order: int = 0
    subtitle: str = ""

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(f for g in self.groups for f in g.fields)


# ==================== 1. 本体界面（5 页 / 51 键）====================
UI_PAGES: tuple[UIPage, ...] = (
    UIPage(
        id="basic",
        label="基础配置",
        order=10,
        subtitle="响应场景与人格设定（模型参数在「Provider 预设 → 模型」里配）",
        groups=(
            UIGroup("basic_switch", "响应场景", ("group_enable", "private_enable")),
            UIGroup(
                "basic_persona",
                "人格设定",
                ("prompt_preset", "persona_preset_id", "system_prompt"),
            ),
        ),
    ),
    UIPage(
        id="permission",
        label="权限管理",
        order=20,
        subtitle="响应权限档位、拥有者与管理者名册（群聊开关与回复规则在「权限与回复」）",
        groups=(
            UIGroup("permission_role", "响应权限", ("permission",)),
            UIGroup(
                "permission_roster",
                "拥有者与管理者",
                ("agent_owner_list", "agent_manager_list"),
            ),
            # P6：高危工具审批（fail-closed：没有通道就拒绝）
            UIGroup("permission_tool_approval", "高危工具审批", ("tool_approval_enable",)),
        ),
    ),
    UIPage(
        id="behavior",
        label="对话行为",
        order=30,
        subtitle="触发设置、消息抖动与回复打断",
        groups=(
            UIGroup("behavior_trigger", "触发设置", ("trigger_at", "trigger_keyword")),
            UIGroup(
                "behavior_debounce",
                "消息抖动",
                ("message_debounce_seconds", "message_debounce_max_wait", "reply_cooldown"),
            ),
            UIGroup(
                "behavior_interrupt",
                "回复打断",
                ("interrupt_enable", "interrupt_save_sent", "interrupt_debug"),
            ),
        ),
    ),
    UIPage(
        id="session",
        label="会话管理",
        order=40,
        subtitle="会话窗口、群聊历史注入与上下文压缩",
        groups=(
            UIGroup(
                "session_window",
                "会话窗口",
                ("session_timeout", "history_rounds", "history_mode", "history_recent_messages", "max_message_length"),
            ),
            UIGroup(
                "session_history",
                "群聊历史",
                (
                    "include_pre_history",
                    "include_private_pre_history",
                    "group_log_enable",
                    "group_log_window_minutes",
                    "group_log_window_limit",
                    "group_log_max_chars",
                ),
            ),
            UIGroup(
                "session_compress",
                "上下文压缩",
                (
                    "context_compress_enable",
                    "context_compress_prompt",
                    "context_token_budget",
                    "context_token_chars_per_token",
                    # Q1：上下文预算（token 压力触发 + 可插拔压缩后端）
                    "context_window",
                    "context_compress_trigger_ratio",
                    "context_compress_keep_ratio",
                    "context_compress_backend",
                    # P4：在线不变式（会话日志 ↔ 内存历史一致性自检）
                    "prompt_dump_verify",
                ),
            ),
        ),
    ),
    UIPage(
        id="advanced",
        label="高级功能",
        order=50,
        subtitle="图片与动图理解、联网搜索",
        groups=(
            UIGroup(
                "adv_media",
                "图片与动图",
                (
                    "image_understanding_enable",
                    "image_max_count",
                    "image_max_mb",
                    "gif_understanding_enable",
                    "gif_max_frames",
                    "gif_frame_max_edge",
                    "gif_probe_all_images",
                ),
            ),
            UIGroup(
                "adv_web_search",
                "联网搜索",
                (
                    "tavily_enable",
                    "tavily_api_key",
                    "tavily_max_results",
                    "tavily_search_depth",
                    "tavily_max_content_chars",
                ),
            ),
            UIGroup(
                "adv_tool_exec",
                "工具执行",
                (
                    "tool_parallel_max",
                    "tool_timeout_default_s",
                    "tool_spill_enable",
                    "tool_spill_max_bytes",
                ),
            ),
            UIGroup(
                "adv_tool_logs",
                "工具日志",
                (
                    "system_tools_log_enabled",
                    "module_tools_log_enabled",
                    "mcp_tools_log_enabled",
                    "onebot_tools_log_enabled",
                ),
            ),
            # Q5：提示词缓存（显式缓存标记 + 前缀稳定）；不开也能跑，只是更贵
            UIGroup("adv_model_cache", "模型缓存", ("prompt_cache_enable",)),
        ),
    ),
)

#: 本体界面保留的全部键（由 UI_PAGES 派生，避免两处漂移）
CORE_KEYS: tuple[str, ...] = tuple(f for page in UI_PAGES for f in page.fields)

# ==================== 2. 迁移为模块（9 个 / 108 键）====================
MODULE_KEYS: dict[str, tuple[str, ...]] = {
    # 输出策略：流式 / 发言通道（共用 output 槽，故同一模块用 mode 三选一）
    "llm_output": (
        "stream_output",
        "stream_send_pool_enabled",
        "stream_send_by_sentence",
        "stream_sentence_max_length",
        "stream_send_interval_mode",
        "stream_send_interval_base_ms",
        "stream_send_interval_min_ms",
        "stream_send_interval_max_ms",
        "stream_send_curve",
        "stream_send_curve_k",
        "stream_short_message_length",
        "stream_short_message_delay_ms",
        "stream_long_message_delay_ms",
        "stream_send_prefix",
        "stream_send_suffix",
        "stream_send_max_queue",
        "stream_queue_full_policy",
        "stream_flush_on_finish",
        "stream_keep_order",
        "speech_via_tool",
        "speech_max_bubbles",
        "speech_max_bubble_chars",
        "speech_reminder_rounds",
        "speech_force_reply_on_call",
        "speech_fallback_enable",
    ),
    # 上下文增强：指代消解 / 按需展开 / 历史查询权限
    "llm_context": (
        "context_expand_enable",
        "referent_resolve_enable",
        "referent_prefetch_enable",
        "referent_ambiguous_policy",
        "referent_prefetch_max",
        "referent_focus_ttl",
        "referent_focus_max",
        "referent_prompt_enable",
        "history_auto_qq_min_local",
        "history_cross_query_enable",
    ),
    # 消息元信息渲染（含历史清洗）
    "llm_message_meta": (
        "include_time",
        "include_private_qq",
        "include_sender",
        "include_mentioned",
        "include_quote",
        "include_quote_sender",
        "include_sent",
        "fetch_at_nickname",
        "fetch_quote_content",
        "meta_sender_style",
        "meta_sent_style",
        "meta_instruction_mode",
        "meta_mask_nickname",
        "clean_output_parentheses",
    ),
    "llm_memory": (
        "memory_enable",
        "memory_private_enable",
        "memory_group_enable",
        "memory_recall_max",
        "memory_recall_max_chars",
        "memory_save_deterministic",
        "memory_extract_enable",
        "memory_extract_interval_min",
        "memory_max_per_owner",
        "memory_user_cross_group",
        "memory_audit_enable",
        "memory_audit_inject",
        "memory_min_confidence",
        "memory_inject_hedge",
        "memory_max_age_days",
        "memory_on_reset",
        "memory_upgrade_saved_only",
    ),
    "llm_knowledge": (
        "knowledge_enable",
        "knowledge_embedding_model_id",
        "knowledge_recall_limit",
    ),
    # 工具扩展：OneBot 工具 / MCP / 工具结果回应要求（联网搜索留在本体高级功能）
    "llm_tools_extra": (
        "onebot_tools_enable",
        "onebot_tools_allowed",
        "onebot_tools_denied",
        "onebot_tools_max_result",
        "onebot_tools_debug",
        "onebot_tool_overrides",
        "poke_cooldown_seconds",
        "mcp_servers",
        "tool_result_directive_enable",
        "tool_result_directive",
    ),
    "llm_proactive": (
        "proactive_friend_enable",
        "proactive_group_enable",
        "stream_proactive_enabled",
        "proactive_friend_sessions",
        "proactive_group_sessions",
        "proactive_min_interval_minutes",
        "proactive_max_interval_minutes",
        "proactive_max_unanswered",
        "proactive_quiet_hours_start",
        "proactive_quiet_hours_end",
        "proactive_group_idle_minutes",
        "proactive_prompt",
        "proactive_prompt_enable",
        "proactive_history_intent_nudge",
        "proactive_env_prompt_enable",
        "proactive_unresolved_prompt_enable",
        "proactive_env_intent_nudge",
    ),
    "llm_schedule": (
        "schedule_enable",
        "schedule_prompt",
        "stream_scheduled_enabled",
    ),
    # 组装档位 / 行为规则 / 组装对账诊断
    "llm_assembly": (
        "assembly_profile",
        "behavior_rules_enable",
        "behavior_rules_override",
        "participation",
        "emoji_react_level",
        "user_message_format",
        "prompt_time_position",
        "system_prompt_style",
        "prompt_dump_enable",
        "prompt_dump_max_entries",
    ),
}

# ==================== 3. 删除（9 键）====================
DELETED_KEYS: tuple[str, ...] = (
    # 循环安全阀：改为框架内硬编码安全默认（5 轮 / 1 次空回复重试 / 1 次收敛）
    # —— 它们不是"功能开关"，暴露给用户只会让用户把钱烧在不理解的地方
    "max_tool_rounds",
    "empty_reply_retries",
    "reasoning_overflow_retries",
    # 与上面三个语义重叠的旧键
    "retry_profile",
    "turn_token_budget",
    # 记忆模块化后无意义（模块自己的 memory_enable 即开关）
    "experimental_long_term_memory",
    # 与 include_pre_history 重复
    "history_background_enable",
    # 输出指令改为交付层无条件解析，不再需要开关
    "outbound_directive_enable",
    "outbound_directive_max_at",
    # v3.2：模型参数归「Provider 预设 → 模型」，agent 页不再重复提供
    "model",
    "temperature",
    "max_tokens",
    # v3.3：三套 per-tool map 收敛为一份 `tools_disabled` 名单
    "system_tools_enabled",
    "module_tools_enabled",
    "mcp_tools_enabled",
    # v3.3：Provider 预设接管连接配置，agent 行的旧连接字段作废
    "api_base",
    "api_key",
    "provider",
    "retry_attempts",
    "fallback_model_ids",
    "provider_model_id",
    "provider_model_pool",
    "provider_preset_id",
    # 旧命名（onebot_* 的前身），无人引用
    "napcat_tools_enable",
    "napcat_tools_allowed",
    "napcat_tools_denied",
    "napcat_tools_debug",
    "napcat_tools_max_result",
    "napcat_tools_log_enabled",
    "napcat_tool_overrides",
    # 无人读取、无处渲染的僵尸键
    "knowledge_recall_max_chars",
    "debug_prompt",
    "authority_type",
    "stream_proactive_scheduled_enabled",
)

#: 迁移完成后的目标键数（W7 验收线）
TARGET_CORE_KEY_COUNT = len(CORE_KEYS)

#: 拥有者身份的**唯一来源**（原账号级 owner_id 已并入此名册）
OWNER_SOURCE_KEY = "agent_owner_list"


# ==================== 查询助手 ====================
def ui_page_of(key: str) -> str | None:
    """键属于本体界面的哪一页；不在本体返回 None。"""
    for page in UI_PAGES:
        if key in page.fields:
            return page.id
    return None


def ui_group_of(key: str) -> str | None:
    """键属于本体界面的哪个分组。"""
    for page in UI_PAGES:
        for group in page.groups:
            if key in group.fields:
                return group.id
    return None


def module_of(key: str) -> str | None:
    for module_name, keys in MODULE_KEYS.items():
        if key in keys:
            return module_name
    return None


def fate_of(key: str) -> str:
    """``ui:<page>`` / ``deleted`` / ``module:<name>`` / ``unknown``。"""
    page = ui_page_of(key)
    if page:
        return f"ui:{page}"
    if key in DELETED_KEYS:
        return "deleted"
    module_name = module_of(key)
    return f"module:{module_name}" if module_name else "unknown"


def all_partitioned_keys() -> tuple[str, ...]:
    flat: list[str] = list(CORE_KEYS) + list(DELETED_KEYS)
    for keys in MODULE_KEYS.values():
        flat.extend(keys)
    return tuple(flat)


def unknown_keys(schema_keys) -> list[str]:
    """schema 里存在、但布局表没给它归宿的键（v3 的硬性遗漏）。"""
    return sorted(set(schema_keys) - set(all_partitioned_keys()))


def duplicate_keys() -> list[str]:
    """在多个归宿里重复出现的键（迁移会写两遍 / UI 会渲染两次）。"""
    seen: dict[str, int] = {}
    for key in all_partitioned_keys():
        seen[key] = seen.get(key, 0) + 1
    return sorted(k for k, n in seen.items() if n > 1)


def stale_keys(schema_keys) -> list[str]:
    """布局表里有、schema 里已不存在的键。"""
    return sorted(set(all_partitioned_keys()) - set(schema_keys))


def page_summary() -> list[dict]:
    """本体界面摘要（供前端生成 sections.json 与文档核对）。"""
    return [
        {
            "id": page.id,
            "label": page.label,
            "subtitle": page.subtitle,
            "order": page.order,
            "fields": len(page.fields),
            "groups": [{"id": g.id, "label": g.label, "fields": list(g.fields)} for g in page.groups],
        }
        for page in sorted(UI_PAGES, key=lambda p: p.order)
    ]
