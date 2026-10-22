"""agent 配置键的**命运分区表**（v2 破坏性改造的唯一真相）。

破坏性 v2 的判定标准：每个配置键只有三种命运 ——

1. ``CORE_KEYS``   —— 留在框架核心（无法归属任何模块的物理层参数）；
2. ``DELETED_KEYS``—— 删除（能力随框架一并移除，或改为非用户的开发者参数）；
3. ``MODULE_KEYS`` —— 迁移到对应模块，成为该模块的 ``default_config``。

## 为什么必须做成代码常量

同一份分区要同时被三处使用：

- `scripts/migrate_v2_split_config.py`（把老配置行拆到模块行）；
- `tests/architecture/test_config_partition.py`（守护"没有键没有归宿"）；
- `docs/PIPELINE_INTERFACE_PLAN.md`（人读的账本）。

三处各写一份必然漂移，所以真相只在这里，文档从这张表生成/核对。

## 判定口径（新增键时必须先归类）

| 归宿 | 判据 |
|---|---|
| CORE | 它是**准入闸门 / 权限 / provider / 会话物理边界 / 循环安全阀**之一，没有模块可归属 |
| DELETE | 它的能力被别处取代，或它只是开发者调参（不该出现在用户配置里） |
| MODULE | 它是一条**策略**（"要不要/怎么做"），对应模块可以自己决定 |

反例（正确归 MODULE）：`stream_output` 是策略；`max_tool_rounds` 是安全阀（CORE）。
"""

from __future__ import annotations

# ── 1. 保留在框架核心（目标 20 键）──────────────────────────────
CORE_KEYS: tuple[str, ...] = (
    # 准入闸门
    "group_enable",
    "private_enable",
    "reply_cooldown",
    "trigger_at",
    "trigger_keyword",
    # 权限名册（模块权限与 Agent 权限同源）
    "permission",
    "agent_owner_list",
    "agent_manager_list",
    # provider 兜底（llm_chat / run_turn 的入参缺省值）
    "model",
    "temperature",
    "max_tokens",
    # 人设基线（没有它机器人无法开口）
    "system_prompt",
    "persona_preset_id",
    # 会话存储的物理契约
    "session_timeout",
    "history_rounds",
    "max_message_length",
    # 循环安全阀（防止烧穿用户额度）
    "max_tool_rounds",
    "empty_reply_retries",
    "reasoning_overflow_retries",
    # 写进历史的清洗规则（历史由框架拥有）
    "clean_output_parentheses",
)

# ── 2. 删除（目标 11 键）────────────────────────────────────────
DELETED_KEYS: tuple[str, ...] = (
    # 迁移对账临时开关，v2 只保留 blocks 一种形态
    "system_prompt_style",
    # 诊断开关 → 改为 run_turn(dump=True) 开发者参数
    "prompt_dump_enable",
    "prompt_dump_max_entries",
    # 改为按模型窗口自动算（token_budget.request_limit 已有能力）
    "context_token_budget",
    "context_token_chars_per_token",
    # 与 empty_reply_retries / max_tool_rounds 语义重叠
    "retry_profile",
    "turn_token_budget",
    # 记忆模块化后无意义
    "experimental_long_term_memory",
    # 与 include_pre_history 重复
    "history_background_enable",
    # 输出指令改为交付层无条件解析
    "outbound_directive_enable",
    "outbound_directive_max_at",
)

# ── 3. 迁移到模块（目标 129 键 / 13 个模块）──────────────────────
MODULE_KEYS: dict[str, tuple[str, ...]] = {
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
        # 发言通道（与流式共用一个 output 槽，故合并进同一模块，见 PLAN §7 W3）
        "speech_via_tool",
        "speech_max_bubbles",
        "speech_max_bubble_chars",
        "speech_reminder_rounds",
        "speech_force_reply_on_call",
        "speech_fallback_enable",
    ),
    "llm_reply_policy": (
        "message_debounce_seconds",
        "message_debounce_max_wait",
        "interrupt_enable",
        "interrupt_save_sent",
        "interrupt_debug",
    ),
    "llm_context": (
        "include_pre_history",
        "include_private_pre_history",
        "group_log_enable",
        "group_log_window_minutes",
        "group_log_window_limit",
        "group_log_max_chars",
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
    "llm_compress": (
        "context_compress_enable",
        "context_compress_prompt",
    ),
    "llm_media": (
        "image_understanding_enable",
        "image_max_count",
        "image_max_mb",
        "gif_understanding_enable",
        "gif_max_frames",
        "gif_frame_max_edge",
        "gif_probe_all_images",
    ),
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
    "llm_tools_extra": (
        "onebot_tools_enable",
        "onebot_tools_allowed",
        "onebot_tools_denied",
        "onebot_tools_max_result",
        "onebot_tools_debug",
        "poke_cooldown_seconds",
        "mcp_servers",
        "tavily_enable",
        "tavily_api_key",
        "tavily_max_results",
        "tavily_search_depth",
        "tavily_max_content_chars",
        "tool_result_directive_enable",
        "tool_result_directive",
    ),
    "llm_proactive": (
        "proactive_friend_enable",
        "proactive_group_enable",
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
        "stream_proactive_enabled",
    ),
    "llm_schedule": (
        "schedule_enable",
        "schedule_prompt",
        "stream_scheduled_enabled",
    ),
    "llm_assembly": (
        "assembly_profile",
        "behavior_rules_enable",
        "behavior_rules_override",
        "participation",
        "emoji_react_level",
        "user_message_format",
        "prompt_time_position",
    ),
}

#: 迁移完成后的目标键数（W7 的验收线）
TARGET_CORE_KEY_COUNT = len(CORE_KEYS)  # 20


def module_of(key: str) -> str | None:
    """某键迁往哪个模块；未迁移返回 None。"""
    for module_name, keys in MODULE_KEYS.items():
        if key in keys:
            return module_name
    return None


def fate_of(key: str) -> str:
    """某键的归宿：``core`` / ``deleted`` / ``module:<name>`` / ``unknown``。"""
    if key in CORE_KEYS:
        return "core"
    if key in DELETED_KEYS:
        return "deleted"
    module_name = module_of(key)
    return f"module:{module_name}" if module_name else "unknown"


def all_partitioned_keys() -> tuple[str, ...]:
    """分区表覆盖的全部键（应为 scheme 键集的超集或等集）。"""
    flat: list[str] = list(CORE_KEYS) + list(DELETED_KEYS)
    for keys in MODULE_KEYS.values():
        flat.extend(keys)
    return tuple(flat)


def unknown_keys(schema_keys) -> list[str]:
    """schema 里存在、但分区表没给它归宿的键（破坏性 v2 的硬性遗漏）。"""
    partitioned = set(all_partitioned_keys())
    return sorted(set(schema_keys) - partitioned)


def duplicate_keys() -> list[str]:
    """在多个归宿里重复出现的键（会导致迁移写两遍）。"""
    seen: dict[str, int] = {}
    for key in all_partitioned_keys():
        seen[key] = seen.get(key, 0) + 1
    return sorted(k for k, n in seen.items() if n > 1)


def stale_keys(schema_keys) -> list[str]:
    """分区表里有、schema 里已不存在的键（迁移脚本会写到不存在的字段）。"""
    return sorted(set(all_partitioned_keys()) - set(schema_keys))
