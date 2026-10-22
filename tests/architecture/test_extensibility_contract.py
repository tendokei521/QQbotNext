"""可扩展性契约：把「流水线有多少接缝、各自通到哪一步」写成可执行断言。

## 这份文件是什么

它不是测某个函数，而是**测框架的可扩展度本身**——把人工审计得到的
55 个「接缝（seam）」固化下来，并强制三条纪律：

1. **评级必须有依据**：每条接缝要写清位置（`ref`）；标 `half`/`unwired` 的必须写明缺哪条
   （R1 可替换 / R2 可观测 / R3 可组合 / R4 生命周期闭环）。
2. **`full` 必须有证据**：标 `full` 的接缝必须引用一个「装替身 → 断言接管 → 卸下 → 断言还原」
   的测试；引用不存在的用例直接 fail。这样"完全可扩展"从形容词变成可验证的事实。
3. **不许静默倒退**：`FULL_BASELINE` 是棘轮——任何改动让 `full` 数量下降都会 fail；
   新增流水线功能必须在本清单里登记一行（否则接缝总数不增，审计会失明）。

## 为什么值得单独存在

历史上"这个功能能不能被插件替换"只能靠读代码回答，答案还会漂移
（例：`register_outbound()` 写了但从没人读，属于"文档说有、实际没有"）。
有了本清单，这个问题每次 `pytest` 都会重新回答一次。

## 评级口径

- `full`   = R1+R2+R3+R4 全中
- `half`   = 只满足 1~3 条
- `unwired`= 一条都不满足（只能改配置或改源码）
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from pathlib import Path

FULL = "full"
HALF = "half"
UNWIRED = "unwired"
RATINGS = (FULL, HALF, UNWIRED)

#: 接缝总数下限（棘轮：只许增，不许减）
BASELINE_TOTAL = 57
#: 完全可扩展接缝数下限（棘轮：只许增，不许减）
#: 从 10 起步：W0-W2 提到 16（出站/入站链、输出策略、输出指令、发送通道、run_turn），
#: B-4 再提到 20（provider 适配器、数据源、会话存储、定时任务服务），
#: W3–W6 模块落地后提到 23（新增 2 个策略型接缝 + 发言通道转 full）。
BASELINE_FULL = 23
#: 半通下限（防止把 half 悄悄改成 full 而不补证据）
BASELINE_HALF = 19

#: LLM 流水线合法的钩子阶段（新增阶段必须同时改这里与 docs）
DECLARED_STAGES = frozenset(
    {"pre_request", "post_assemble", "post_response", "pre_send", "post_send", "post_stream"}
)

#: `ServiceAccess` 上不算"服务"的字段（不进 capabilities 清单是合理的）
SERVICE_FIELD_ALLOWLIST = {
    "providers": "data_sources 的历史别名（同一对象）",
    "capabilities": "callable()，是自检入口而非服务",
    "extra": "自由扩展槽，无固定语义",
}

TESTS_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TESTS_ROOT.parent / "app"


@dataclass(frozen=True)
class Seam:
    """流水线上的一个决策点。"""

    id: str
    stage: str
    seam: str
    rating: str
    ref: str
    #: 证明"能接管且能还原"的测试（`tests/xxx.py::test_yyy`）
    evidence: tuple[str, ...] = ()
    #: `half`/`unwired` 必须写：缺哪条 R
    gap: str = ""


SEAMS: tuple[Seam, ...] = (
    # ── 传输与事件 ──────────────────────────────────────────────
    Seam("S1", "传输", "协议接入（连接/换号）", UNWIRED, "app/infrastructure/onebot/gateway.py",
         gap="R1/R2/R3/R4：只有 OneBot 一种实现，无 adapter registry"),
    Seam("S2", "传输", "上报解码", UNWIRED, "app/infrastructure/onebot/codec.py:178",
         gap="R1：`decode()` 是硬 if 链，新协议字段只能改源码"),
    Seam("S3", "传输", "事件类型与显示名", UNWIRED, "app/infrastructure/onebot/codec.py:193",
         gap="R1：`EVENT_NAMES` 为固定 dict，新增事件类型需改源码"),
    Seam("S4", "传输", "连接去重/生命周期", UNWIRED, "app/infrastructure/onebot/gateway.py",
         gap="R1/R2：去重与心跳在网关内部，无钩子无开关"),
    Seam("S5", "传输", "入站节点链", FULL, "app/plugin/dispatcher.py:61",
         evidence=(
             "tests/plugin/test_dispatcher.py::test_subscribed_module_only",
             "tests/plugin/test_nodes.py::test_outbound_pipeline_reads_registry_live",
         )),
    Seam("S6", "传输", "出站节点链", FULL, "app/plugin/nodes/outbound.py",
         evidence=(
             "tests/plugin/test_nodes.py::test_outbound_pipeline_reads_registry_live",
             "tests/plugin/test_nodes.py::test_outbound_pipeline_always_has_a_send_terminal",
         )),

    # ── 触发与准入 ──────────────────────────────────────────────
    Seam("S7", "触发", "触发判定（@/关键词）", UNWIRED, "app/agent/loop/pipeline.py:217",
         gap="R1/R2：`check_trigger` 只有 `trigger_at`/`trigger_keyword` 两个配置键"),
    Seam("S8", "触发", "场景开关/冷却", HALF, "app/agent/loop/pipeline.py:251",
         gap="R1：只能配置，不能替换判定"),
    Seam("S9", "触发", "防抖合并", HALF, "app/agent/loop/pool.py",
         gap="R1/R2：`LlmPool` 只读配置，替换策略无槽"),
    Seam("S10", "触发", "回复打断（代际）", HALF, "app/agent/loop/pipeline.py:74",
         gap="R1：只读 `interrupt_enable`，代际逻辑不可替换"),
    Seam("S11", "触发", "`#llm` 指令表", UNWIRED, "app/agent/commands.py",
         gap="R1：`COMMAND_GROUPS` 静态表（有测试锁，但仍不可扩展）"),

    # ── 上下文取值 ──────────────────────────────────────────────
    Seam("S12", "取值", "会话/历史存储", FULL, "app/agent/session/manager.py:SessionManager",
         evidence=(
             "tests/plugin/test_unregister_discipline.py::test_session_manager_singleton_normalises_bot_id_type",
             "tests/plugin/test_unregister_discipline.py::test_session_manager_evict_removes_instance",
         )),
    Seam("S13", "取值", "群聊在线历史 pre_history", UNWIRED, "app/agent/loop/prompt_prep.py:155",
         gap="R1/R2：硬编码调用，无 provider 注册"),
    Seam("S14", "取值", "群聊环境记录 group_log", HALF, "app/agent/loop/context_blocks.py:233",
         gap="R1：模块可通过 `context_providers` 追加块（R3 成立），但框架自身的 group_log 调用位置仍硬编码",
         evidence=("tests/agent/loop/test_providers.py::test_extra_blocks_render_between_group_log_and_history",)),
    Seam("S15", "取值", "指代/焦点", UNWIRED, "app/agent/loop/context_blocks.py:130",
         gap="R1：只有 `referent_*` 配置簇"),
    Seam("S16", "取值", "记忆召回", HALF, "app/agent/loop/context_blocks.py:199",
         gap="R1：直调 `runtime.memory`，仅能整体 disable"),
    Seam("S17", "取值", "知识库", HALF, "app/agent/loop/tool_source.py:130",
         gap="R1：直调 `runtime.knowledge`，仅能整体 disable"),
    Seam("S18", "取值", "技能注入", FULL, "app/agent/persona/skills.py:74",
         evidence=("tests/agent/assembly/test_llm_assembly.py::test_equivalent_with_all_blocks_present",)),
    Seam("S19", "取值", "工具收集（8 段 if）", HALF, "app/agent/loop/tool_source.py:72",
         gap="R1 打折：`tool_sources` 注册表已可追加工具（R3 成立），但框架自身的 8 段 if 仍是硬编码",
         evidence=("tests/agent/loop/test_providers.py::test_tool_sources_contribute_specs",)),
    Seam("S20", "取值", "图片解析", HALF, "app/agent/loop/pipeline.py:138",
         gap="R1：只读 `image_understanding_enable` 等配置"),
    Seam("S21", "组装", "块表（12 块）", HALF, "app/agent/assembly/assembler.py:496",
         gap="R3：可整体替换（`assembly` 槽）但排他，块不可单独注册"),
    Seam("S22", "组装", "post_assemble", FULL, "app/agent/assembly/assembler.py:736",
         evidence=(
             "tests/agent/test_llm_post_assemble.py::test_post_assemble_can_rewrite_messages",
             "tests/agent/test_llm_post_assemble.py::test_post_assemble_empty_messages_aborts_request",
         )),
    Seam("S23", "组装", "token 预算裁剪", UNWIRED, "app/agent/assembly/token_budget.py:184",
         gap="R1：`apply_request_policy` 只读 `context_token_budget`"),
    Seam("S24", "组装", "请求落盘 dump", HALF, "app/agent/assembly/dump.py",
         gap="R1：只能看，不能替换格式或目标"),

    # ── 请求 ────────────────────────────────────────────────────
    Seam("S25", "请求", "provider 链解析", UNWIRED, "app/agent/runtime.py:227",
         gap="R1/R2：`provider_chain()` 无钩子，模型选择不可介入"),
    Seam("S26", "请求", "参数规划（max_tokens 三源合并）", UNWIRED, "app/agent/providers/policy.py",
         gap="R1：`merge_config_layers`/`plan_max_tokens` 是硬编码管线"),
    Seam("S27", "请求", "provider 适配器类", FULL, "app/agent/providers/__init__.py:register_provider",
         evidence=(
             "tests/plugin/test_unregister_discipline.py::test_register_and_unregister_provider_roundtrip",
             "tests/plugin/test_unregister_discipline.py::test_unregister_provider_by_owner",
         )),
    Seam("S28", "请求", "调用策略", FULL, "app/agent/providers/invoker.py:57",
         evidence=(
             "tests/agent/providers/test_llm_provider_invoker.py::test_invoker_feature_swaps_and_restores",
             "tests/agent/providers/test_llm_provider_invoker.py::test_invoker_feature_handover_between_owners",
         )),
    Seam("S29", "请求", "工具循环实现", UNWIRED, "app/agent/providers/openai_compat.py:382",
         gap="R1/R3：双实现——非流式循环在 provider 内(`openai_compat:382`)，流式循环在 `chat.stream_response` 内"),
    Seam("S30", "请求", "重试/空回复策略", HALF, "app/agent/providers/policy.py",
         gap="R1：`empty_reply_retries`/`retry_profile` 等只读配置"),

    # ── 输出 ────────────────────────────────────────────────────
    Seam("S31", "输出", "流式 vs 非流式", FULL, "app/agent/delivery/output.py",
         evidence=(
             "tests/agent/delivery/test_output_slot.py::test_custom_output_strategy_drives_sending",
             "tests/agent/delivery/test_output_slot.py::test_output_feature_swaps_and_restores",
         )),
    Seam("S32", "输出", "切句", HALF, "app/domain/splitter.py:22",
         gap="R1：`split_sentences` 是纯函数，只能整体替换调用方（经 output 槽），不能单独注册切句策略",
         evidence=("tests/domain/test_stream_splitter.py::test_split_sentences_basic",)),
    Seam("S33", "输出", "输出指令 `[reply]`/`[@qq]`", FULL, "app/agent/delivery/channel.py",
         evidence=(
             "tests/agent/delivery/test_channel_send_item.py::test_send_item_parses_reply_directive",
             "tests/agent/delivery/test_channel_send_item.py::test_send_item_parses_at_directive_only_in_group",
         )),
    Seam("S34", "输出", "发送节奏", HALF, "app/domain/pace.py:9",
         gap="R1：`SendPolicy` 只读 `stream_send_*` 配置；节奏在 output 槽内部，未单独成接缝"),
    Seam("S35", "输出", "消息池", HALF, "app/agent/delivery/pool.py:21",
         gap="R1：`StreamSendPool` 由 `StreamOutput` 内部创建，模块换掉 output 槽即可绕过（R1 打折：不能只换池）"),
    Seam("S36", "输出", "发言通道（文本=思考）", FULL, "app/plugins/llm_output/module.py",
         evidence=(
             "tests/plugins/test_llm_output.py::test_config_view_prefers_module_then_falls_back",
             "tests/plugins/test_llm_output.py::test_mode_selects_matching_framework_strategy",
         )),
    Seam("S37", "输出", "发送通道", FULL, "app/agent/delivery/channel.py:send_item",
         evidence=(
             "tests/agent/delivery/test_channel_send_item.py::test_send_item_fires_pre_send_and_post_send_hooks",
             "tests/agent/delivery/test_channel_send_item.py::test_send_item_survives_bot_exception",
         )),
    Seam("S38", "输出", "发送前/后 + api/生命周期/事件完成钩子", FULL,
         "app/infrastructure/onebot/client.py:254",
         evidence=(
             "tests/plugin/test_hooks_pool.py::test_bot_connection_send_triggers_registry",
             "tests/plugin/test_hooks_pool.py::test_bot_connection_before_send_and_api_hooks",
         )),
    Seam("S39", "输出", "历史写入", HALF, "app/agent/loop/turn.py:_write_history",
         gap="R3：统一入口已有（`run_turn` 内一处），但 chat 流式路径仍走 `stream_response` 自己的 upsert"),
    Seam("S40", "输出", "遥测", HALF, "app/agent/telemetry.py",
         gap="R1：只能读，不能接自定义记录器"),

    # ── 驱动 ────────────────────────────────────────────────────
    Seam("S41", "驱动", "消息触发", FULL, "app/plugin/nodes/inbound.py:169",
         evidence=("tests/plugin/test_dispatcher.py::test_bot_id_filter",)),
    Seam("S42", "驱动", "主动消息", UNWIRED, "app/agent/runtime.py:106",
         gap="R1/R2：`runtime.proactive` 在 `__init__` 硬 new，`pipeline.py:567` 硬调"),
    Seam("S43", "驱动", "定时任务（框架服务）", FULL, "app/application/cron_service.py:SchedulerService",
         evidence=(
             "tests/application/test_scheduler_service_jobs.py::test_list_jobs_filters_by_prefix",
             "tests/application/test_scheduler_service_jobs.py::test_update_job_reschedules_and_keeps_handler",
             "tests/application/test_scheduler_service_jobs.py::test_run_job_now_does_not_remove_schedule",
         )),
    Seam("S44", "驱动", "模块发起一轮对话", FULL, "app/agent/loop/turn.py:run_turn",
         evidence=(
             "tests/agent/loop/test_run_turn.py::test_run_turn_non_stream_returns_text_without_event",
             "tests/agent/loop/test_run_turn.py::test_run_turn_stream_calls_on_sentence_in_order",
             "tests/plugin/test_module_raw_interfaces.py::test_llm_turn_delegates_to_run_turn",
         )),
    Seam("S45", "驱动", "外部事件注入（emit）", UNWIRED, "app/core/event_bus.py",
         gap="R1：`event_bus`/`ModuleDispatcher` 都不在 `ServiceAccess`"),

    # ── 模块系统 ────────────────────────────────────────────────
    Seam("S46", "模块", "加载/热重载", FULL, "app/plugin/registry.py:139",
         evidence=("tests/plugin/test_registry.py::test_reload_reimports",)),
    Seam("S47", "模块", "权限/启停/单一服务", FULL, "app/plugin/authority.py",
         evidence=("tests/plugin/test_authority.py::test_group_admin_blocks_member",)),
    Seam("S48", "模块", "配置 schema + WebUI 表单", FULL, "app/plugin/schema_utils.py",
         evidence=("tests/plugin/test_schema_utils.py::test_known_types_have_no_issues",)),
    Seam("S49", "模块", "资源引用（resource）", FULL, "app/plugin/features/registry.py:43",
         evidence=("tests/plugin/test_resource_registry.py::test_register_overrides_and_unregister",)),
    Seam("S50", "模块", "数据源 list/dynamic", FULL, "app/application/provider_service.py:ProviderRegistry",
         evidence=("tests/plugin/test_unregister_discipline.py::test_data_source_registry_unregister_module",)),
    Seam("S51", "模块", "模块自建 HTTP 路由", UNWIRED, "app/webui/api/modules.py:377",
         gap="R1：只有静态 `pages/index.html`（对比 AstrBot `Context.register_web_api`）"),
    Seam("S52", "模块", "模块自定义页面", HALF, "app/webui/api/modules.py:377",
         gap="R1 打折：能放静态页，但没有自己的数据接口"),
    Seam("S53", "模块", "状态持久化", FULL, "app/plugin/api.py:58",
         evidence=("tests/plugin/test_registry.py::test_load_single",)),
    Seam("S54", "模块", "能力接管（8 feature）", HALF, "app/plugin/features/controllers.py:216",
         gap="R1 打折：租约/交棒/还原完备（R4 成立），但 8 个里 5 个只是配置 toggle",
         evidence=("tests/plugin/test_features.py::test_suppress_release_restores",)),
    Seam("S55", "模块", "capabilities 自检", HALF, "app/application/capabilities.py:18",
         gap="R2 打折：清单不含 node/delivery 类服务，模块自检看不全",
         evidence=("tests/plugin/test_framework_capabilities.py::test_capabilities_with_real_container",)),
    # ── v3 新增：策略型能力（配置归模块，机制留框架）────────────────
    Seam("S56", "取值", "消息元信息渲染", FULL, "app/plugins/llm_message_meta/module.py",
         evidence=(
             "tests/plugins/test_llm_message_meta.py::test_enhance_reads_module_meta_config",
             "tests/plugins/test_llm_message_meta.py::test_history_cleaner_uses_module_config",
         )),
    Seam("S57", "取值", "上下文增强（指代/展开/历史查询）", FULL,
         "app/plugins/llm_context/module.py",
         evidence=(
             "tests/plugins/test_llm_context.py::test_referent_reads_module_config",
             "tests/plugins/test_llm_context.py::test_tool_source_expand_gate_follows_module",
         )),
)


# ==================== 清单自洽性 ====================


def test_manifest_ids_are_unique_and_well_formed():
    ids = [s.id for s in SEAMS]
    assert len(ids) == len(set(ids)), "接缝 id 重复"
    assert all(re.fullmatch(r"S\d+", i) for i in ids), "id 必须是 S<数字>"


def test_manifest_ratings_are_known():
    bad = [s.id for s in SEAMS if s.rating not in RATINGS]
    assert not bad, f"未知评级: {bad}"


def test_every_seam_names_its_location():
    bad = [s.id for s in SEAMS if not s.ref.strip()]
    assert not bad, f"没写位置的接缝: {bad}"


def test_unwired_and_half_seams_declare_the_missing_requirement():
    """`half`/`unwired` 必须点名缺哪条 R —— 否则它只是"我觉得不行"，不是可审计的结论。"""
    bad = [
        s.id
        for s in SEAMS
        if s.rating != FULL and not re.search(r"\bR[1-4]\b", s.gap)
    ]
    assert not bad, f"未说明缺哪条 R 的接缝: {bad}"


def test_full_seams_cite_a_takeover_test():
    """`full` 的证据必须存在：装替身 → 断言接管 → 卸下 → 断言还原。"""
    bad = [s.id for s in SEAMS if s.rating == FULL and not s.evidence]
    assert not bad, f"标 full 却没有接管测试证据: {bad}"


def test_cited_evidence_tests_exist():
    missing: list[str] = []
    for seam in SEAMS:
        for cite in seam.evidence:
            path_part, _, func = cite.partition("::")
            path = TESTS_ROOT.parent / path_part
            if not path.is_file():
                missing.append(f"{seam.id}: 文件不存在 {path_part}")
                continue
            text = path.read_text(encoding="utf-8")
            if not re.search(rf"^\s*(async )?def {re.escape(func)}\b", text, re.MULTILINE):
                missing.append(f"{seam.id}: 用例不存在 {cite}")
    assert not missing, "引用了不存在的证据:\n" + "\n".join(missing)


# ==================== 棘轮：不许静默倒退 ====================


def test_seam_count_does_not_shrink():
    """新增流水线功能必须登记一行；否则审计失明。"""
    assert len(SEAMS) >= BASELINE_TOTAL, (
        f"接缝数从 {BASELINE_TOTAL} 降到 {len(SEAMS)}：删接缝前请先确认真的是收敛而非失明"
    )


def test_fully_extensible_count_does_not_regress():
    full = [s for s in SEAMS if s.rating == FULL]
    assert len(full) >= BASELINE_FULL, (
        f"完全可扩展接缝从 {BASELINE_FULL} 降到 {len(full)}："
        f"若有接缝被降级，请同步下调 BASELINE_FULL 并说明原因"
    )


def test_scoreboard_is_printed_on_demand():
    """给审计用的记分板（失败信息里能看到全貌；正常时只断言数学自洽）。"""
    full = sum(1 for s in SEAMS if s.rating == FULL)
    half = sum(1 for s in SEAMS if s.rating == HALF)
    unwired = sum(1 for s in SEAMS if s.rating == UNWIRED)
    board = (
        f"\n可扩展度记分板：full={full} / half={half} / unwired={unwired} "
        f"/ total={len(SEAMS)} → 完全可扩展 {full / len(SEAMS):.0%}\n"
    )
    assert full + half + unwired == len(SEAMS), board
    assert half >= BASELINE_HALF or full > BASELINE_FULL, board + "half 变少必须伴随 full 变多"


# ==================== 跨面一致性（防止"新设施绕开清单"）====================

_STAGE_PATTERNS = (
    re.compile(r"_run_stage\(\s*\"([a-z_]+)\""),
    re.compile(r"run_stage\([^,]+,\s*\"([a-z_]+)\""),
    re.compile(r"@llm_hook\(\s*\"([a-z_]+)\""),
    re.compile(r"stage\s*=\s*\"([a-z_]+)\""),
)


def test_every_llm_hook_stage_is_declared():
    """代码里出现的每个钩子阶段都必须已在 `DECLARED_STAGES` 登记。

    拼错的阶段名此前会静默失效（注册了但永不触发），这条断言把它变成加载期错误。
    """
    used: set[str] = set()
    for path in APP_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in _STAGE_PATTERNS:
            used.update(pattern.findall(text))
    undeclared = sorted(used - DECLARED_STAGES)
    assert not undeclared, f"未登记的 LLM 钩子阶段: {undeclared}（新增阶段请同步 DECLARED_STAGES 与文档）"


def test_every_service_access_field_is_declared_in_capabilities():
    """`ServiceAccess` 上新增服务字段时，必须同时在 capabilities 清单里登记。

    否则模块 `requires = ("xxx",)` 的自检永远说自己"缺"——因为它查的是 capabilities。
    """
    from app.application.capabilities import SERVICE_FIELDS
    from app.plugin.base import ServiceAccess

    fields = {f.name for f in dataclasses.fields(ServiceAccess)}
    declared = set(SERVICE_FIELDS) | set(SERVICE_FIELD_ALLOWLIST)
    missing = sorted(fields - declared)
    assert not missing, (
        f"ServiceAccess 字段未进 capabilities: {missing} —— "
        f"请加进 app/application/capabilities.py 的 SERVICE_FIELDS（或写进本文件的 allowlist 并说明理由）"
    )


def test_every_feature_id_has_a_seam():
    """每个可接管能力都必须在清单里能找到对应接缝（否则"能接管"是口头承诺）。

    feature 清单来自 `BUILTIN_CONTROLLERS` 单一真相表，新增能力只需在
    `expected` 里补一行映射；漏了会在这里立刻失败。
    """
    from app.plugin.features import FeatureRegistry
    from app.plugin.features.builtin import register_builtin_controllers

    registry = FeatureRegistry()
    feature_ids = register_builtin_controllers(registry, None)
    seam_text = " ".join(
        f"{s.id} {s.stage} {s.seam} {s.ref} {s.gap}" for s in SEAMS
    ).lower()
    # feature_id 到接缝名的自然语言映射（新增 feature 时必须在此补充）
    expected = {
        "proactive": "主动消息",
        "schedule": "定时任务",
        "memory": "记忆召回",
        "knowledge": "知识库",
        "onebot_tools": "工具收集",
        "agent": "模块发起一轮对话",
        "assembly": "块表",
        "provider_invoker": "调用策略",
        # v3 新增：输出策略槽（流式/非流式/发言通道可整体替换）
        "output": "流式 vs 非流式",
        # v3 新增：策略型能力（只有一组配置键，接管 = 策略归模块）
        "message_meta": "消息元信息",
        "context": "上下文增强",
        "mcp": "工具收集",
        "schedule_driver": "定时任务",
    }
    missing = [fid for fid in feature_ids if fid not in expected]
    assert not missing, f"新 feature 未在 expected 映射里登记: {missing}"
    unmapped = [
        fid for fid in feature_ids if expected[fid].lower() not in seam_text
    ]
    assert not unmapped, f"feature 找不到对应接缝: {unmapped}"


def test_seam_stages_are_known():
    known = {"传输", "触发", "取值", "组装", "请求", "输出", "驱动", "模块"}
    bad = sorted({s.stage for s in SEAMS} - known)
    assert not bad, f"未知接缝阶段: {bad}"
