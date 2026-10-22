# 流水线接口化施工清单 v2（破坏性全量改造）

> **v1 作废**。v1 的口径是"配置保留 + 标注可被谁接管"，属于渐进路线。
> v2 口径（用户 2026-02 定）：**配置只有三种命运 —— 保留在框架核心 / 迁移到对应模块 / 删除。不做"保留 + 标注"。**
>
> 配套：`docs/research/pipeline-interface-audit.md`（55 个接缝的评级与证据）、
> `tests/architecture/test_extensibility_contract.py`（可执行的接缝契约与棘轮）。

---

## 0. 核心原则（一句话）

> **机制留在框架且不再读配置；策略搬到模块并读自己的配置；槽把两者连起来。**

推论：

1. 框架里每一个 `config.get("xxx_策略")` 都是待迁移项 —— 它证明"这个决策不该由框架做"。
2. 机制函数搬家后**删掉一切配置读取**，改为**显式入参**（由调用方=模块/run_turn 传入）。
3. 迁移完，`app/agent/config_schema.py` 从 **160 键 → 20 键**。

---

## 1. 目标形态

```
框架核心（不可下沉的物理层）
├── 连接与协议      onebot gateway / codec / 去重
├── 配置与权限      ConfigService / authority / owner+manager 名册
├── 会话存储        SessionManager（不加策略，只存取）
├── 事件分发        NodeRegistry 入站链 + dispatcher
├── provider        PROVIDERS / 预设 / 模型池 / 回退链 / 单次 HTTP
├── Agent 骨架      一轮对话的**机制**：取值→组装→请求→（可选）发送
└── 模块运行时      loader / registry / features 租约 / 钩子注册表

模块（12 个，全部读自己的 default_config + config_schema）
├── llm_output            输出策略（三形态 text/stream/speech）  supersedes=("output","output_style")
├── llm_reply_policy      触发/防抖/打断       supersedes=("trigger","debounce","interrupt")
├── llm_context          指代/展开/群记录/预历史 supersedes=("context",)
├── llm_message_meta     消息元信息渲染       supersedes=("message_meta",)
├── llm_compress         上下文压缩          supersedes=("compress",)
├── llm_media            图片/动图理解        supersedes=("media",)
├── llm_memory           长期记忆            supersedes=("memory",)
├── llm_knowledge        知识库              supersedes=("knowledge",)
├── llm_tools_extra      OneBot+Tavily+MCP   supersedes=("onebot_tools","web_search","mcp")
├── llm_assembly         组装档位/行为规则    supersedes=("assembly",)
├── llm_proactive        主动消息（纯业务）    supersedes=("proactive",)
└── llm_schedule         定时任务（纯业务）    supersedes=("schedule",)
```

> **为什么 `stream` 与 `speech` 合一个模块**：二者都在同一时刻争夺同一个 `output` 槽，
> 拆成两个模块会让行为依赖加载顺序。合成一个模块、用 `mode` 三选一（`text` / `stream` / `speech`）
> 语义更硬，也顺手消掉了 `speech_via_tool` 那个"三条件耦合"的老坑。详见 §7 W3。
> 分区表 `app/agent/config_partition.py::MODULE_KEYS["llm_output"]` 里 25 个键 = 19 流式 + 6 发言通道。

**`feature_id` 从 8 → 20**：`output` `output_style` `trigger` `debounce` `interrupt` `context`
`message_meta` `compress` `media` `memory` `knowledge` `onebot_tools` `web_search` `mcp`
`assembly` `provider_invoker` `agent` `proactive` `schedule` `turn_kernel`

> 最后两个是"纯业务型"能力（模块自建，不需要框架槽）与"一轮对话内核"（`run_turn` 本身可被整体接管）。

---

## 2. 配置总账：160 键的完整命运

**分区已用脚本校验：20 + 11 + 129 = 160，无重复、无遗漏。**

### 2.1 保留在框架核心（20 键）

| 键 | 为什么框架必须自己决定 |
|---|---|
| `group_enable` / `private_enable` | Agent 是否参与的总闸门。模块权限过滤共用同一套，闸门下放会让模块互相打架 |
| `reply_cooldown` | 与会话冷却强耦合（`Session.can_reply()` 是框架机制） |
| `permission` / `agent_owner_list` / `agent_manager_list` | 权限名册，模块权限与 Agent 权限同源 |
| `model` / `temperature` / `max_tokens` | provider 兜底层（`llm_chat`/`run_turn` 的入参缺省值） |
| `system_prompt` / `persona_preset_id` | 人设是内容基线；没有它机器人无法开口（迁走等于开箱即死） |
| `session_timeout` / `history_rounds` / `max_message_length` | 会话存储的物理契约（`SessionManager`/`add_message` 的边界） |
| `trigger_at` / `trigger_keyword` | **判断"这条消息算不算对 Bot 说话"是准入闸门**，与权限同级；且是 `_would_trigger_llm` 的共用判据 |
| `max_tool_rounds` / `empty_reply_retries` / `reasoning_overflow_retries` | 循环的**安全阀**（防止烧穿用户的额度），不是功能开关 |
| `clean_output_parentheses` | 写进历史的清洗规则 —— 历史由框架拥有，清洗必须同源 |

### 2.2 删除（11 键，功能随框架一并移除）

| 键 | 删除理由 |
|---|---|
| `system_prompt_style` | 迁移对账用的临时开关，v2 只保留 `blocks` 一种形态 |
| `prompt_dump_enable` / `prompt_dump_max_entries` | 诊断开关 → 改为开发者手调的 `run_turn(dump=True)` 参数，不进用户配置 |
| `context_token_budget` / `context_token_chars_per_token` | 改用"按模型窗口自动算"（`token_budget.request_limit` 已有能力），不再暴露 |
| `retry_profile` / `turn_token_budget` | 与 `empty_reply_retries`/`max_tool_rounds` 语义重叠，收敛为 3 个核心键 |
| `experimental_long_term_memory` | 记忆模块化后无意义（模块自身的 `memory_enable` 即开关） |
| `history_background_enable` | 与 `include_pre_history` 重复 |
| `outbound_directive_enable` / `outbound_directive_max_at` | 输出指令改为**框架交付层无条件解析**（`send_item` 内建），不再需要开关 |

### 2.3 迁移到 12 个模块（129 键）

> 权威表是 `app/agent/config_partition.py::MODULE_KEYS`（`tests/architecture/test_config_partition.py` 守着它与 schema 的一致性）。
> 下表与它逐键一致，键数之和 129 = 160 − 20 − 11。

| 模块 | 键数 | 迁入的键（**全量列出，逐个可查**） |
|---|---|---|
| `llm_output` | 25 | **流式 19**：`stream_output` `stream_send_pool_enabled` `stream_send_by_sentence` `stream_sentence_max_length` `stream_send_interval_mode` `stream_send_interval_base_ms` `stream_send_interval_min_ms` `stream_send_interval_max_ms` `stream_send_curve` `stream_send_curve_k` `stream_short_message_length` `stream_short_message_delay_ms` `stream_long_message_delay_ms` `stream_send_prefix` `stream_send_suffix` `stream_send_max_queue` `stream_queue_full_policy` `stream_flush_on_finish` `stream_keep_order`<br>**发言通道 6**：`speech_via_tool` `speech_max_bubbles` `speech_max_bubble_chars` `speech_reminder_rounds` `speech_force_reply_on_call` `speech_fallback_enable` |
| `llm_reply_policy` | 5 | `message_debounce_seconds` `message_debounce_max_wait` `interrupt_enable` `interrupt_save_sent` `interrupt_debug` |
| `llm_context` | 16 | `include_pre_history` `include_private_pre_history` `group_log_enable` `group_log_window_minutes` `group_log_window_limit` `group_log_max_chars` `context_expand_enable` `referent_resolve_enable` `referent_prefetch_enable` `referent_ambiguous_policy` `referent_prefetch_max` `referent_focus_ttl` `referent_focus_max` `referent_prompt_enable` `history_auto_qq_min_local` `history_cross_query_enable` |
| `llm_compress` | 2 | `context_compress_enable` `context_compress_prompt` |
| `llm_media` | 7 | `image_understanding_enable` `image_max_count` `image_max_mb` `gif_understanding_enable` `gif_max_frames` `gif_frame_max_edge` `gif_probe_all_images` |
| `llm_message_meta` | 13 | `include_time` `include_private_qq` `include_sender` `include_mentioned` `include_quote` `include_quote_sender` `include_sent` `fetch_at_nickname` `fetch_quote_content` `meta_sender_style` `meta_sent_style` `meta_instruction_mode` `meta_mask_nickname` |
| `llm_memory` | 17 | `memory_enable` `memory_private_enable` `memory_group_enable` `memory_recall_max` `memory_recall_max_chars` `memory_save_deterministic` `memory_extract_enable` `memory_extract_interval_min` `memory_max_per_owner` `memory_user_cross_group` `memory_audit_enable` `memory_audit_inject` `memory_min_confidence` `memory_inject_hedge` `memory_max_age_days` `memory_on_reset` `memory_upgrade_saved_only` |
| `llm_knowledge` | 3 | `knowledge_enable` `knowledge_embedding_model_id` `knowledge_recall_limit` |
| `llm_tools_extra` | 14 | `onebot_tools_enable` `onebot_tools_allowed` `onebot_tools_denied` `onebot_tools_max_result` `onebot_tools_debug` `poke_cooldown_seconds` `mcp_servers` `tavily_enable` `tavily_api_key` `tavily_max_results` `tavily_search_depth` `tavily_max_content_chars` `tool_result_directive_enable` `tool_result_directive` |
| `llm_proactive` | 17 | `proactive_friend_enable` `proactive_group_enable` `proactive_friend_sessions` `proactive_group_sessions` `proactive_min_interval_minutes` `proactive_max_interval_minutes` `proactive_max_unanswered` `proactive_quiet_hours_start` `proactive_quiet_hours_end` `proactive_group_idle_minutes` `proactive_prompt` `proactive_prompt_enable` `proactive_history_intent_nudge` `proactive_env_prompt_enable` `proactive_unresolved_prompt_enable` `proactive_env_intent_nudge` `stream_proactive_enabled` |
| `llm_schedule` | 3 | `schedule_enable` `schedule_prompt` `stream_scheduled_enabled` |
| `llm_assembly` | 7 | `assembly_profile` `behavior_rules_enable` `behavior_rules_override` `participation` `emoji_react_level` `user_message_format` `prompt_time_position` |

> 校验脚本产物留在 `logs/_cfg_disp.json`（可直接 `python -c` 复核 20/11/129 的分区）。

---

## 3. 模块清单与实现要点

> 每个模块 = `app/plugins/<dir>/` 下 `module.py` + `config_schema.py` + `service/`。
> 共同约定：`permission = "everyone"`、`subscribe = ("message_group","message_private")`（若需观察）、
> `category = "LLM"`、读配置一律 `self.config.get(...)`。

### 3.1 `llm_output`（输出策略）★ 需要新的 `output` 槽

一个模块，三种 `mode`：`text`（框架默认的非流式一次出）/ `stream`（按句流式）/ `speech`（文本=思考，发言走工具）。

| 项 | 内容 |
|---|---|
| 接管 | `supersedes = ("output", "output_style")`；实现 `respond(ctx, req) -> AsyncIterator[OutboundItem]` |
| 机制来源 | **stream**：`loop/pipeline.py:370-452`（`_run_stream`）+ `loop/chat.py:220-620` 的切句/发送段<br>**speech**：`delivery/speech.py`（`SpeechOutbox`/`build_speech_tools`）+ `loop/assistant_output.py` 的 `_flush_speech`/`_force_speak_round` |
| 策略来源 | 25 键（流式 19 + 发言通道 6），见 `config_partition.MODULE_KEYS["llm_output"]` |
| 框架侧保留 | `domain/splitter.split_sentences`、`delivery/pool.StreamSendPool`、`domain/pace.SendPolicy` —— **全部改为显式入参，不再读 config** |
| 关键改动 | `StreamSendPool(config)` → `StreamSendPool(max_queue=, full_policy=, flush_on_finish=, policy=)`；`SendPolicy(config)` → `SendPolicy(mode=, base_ms=, ...)`；删掉 `speech_via_tool(config)` 的三条件耦合（档位 + 开关 + 流式），改由 `mode="speech"` 直接表达 |
| 测试 | `tests/plugins/test_llm_output.py`：三种 `mode` 下同一输入的**发送序列**不同；`pre_send`/`post_send` 三种模式都触发；卸载后回 `text` 默认 |

> **为什么三分支合成一个模块**：三种形态争夺的是同一个 `output` 槽。拆成三个模块会让行为依赖
> 加载顺序（先到先得 + 交棒语义虽然支持，但对用户是隐形的坑）。一个模块 + `mode` 配置，
> 语义硬、可测、且顺手消掉 `speech_via_tool` 那个"三条件耦合"的老坑。
> 若将来真需要"多输出策略并存"，再拆开并显式接受顺序敏感性。

### 3.2 `llm_reply_policy`（触发/防抖/打断）

| 项 | 内容 |
|---|---|
| 接管 | `supersedes = ("trigger","debounce","interrupt")` —— 三个 feature 都指向"是否把这条消息交给 LLM" |
| **不需要新槽** | 全部用现成钩子：`@llm_hook("pre_request", order=-50)` 里 `ctx.job.skip = True`（拦截/冷却/打断），`ctx.runtime.interrupt_enabled = ...`（打断开关） |
| 机制来源 | `loop/pool.py`（`LlmPool` 防抖 + `merge_debounced_texts`）、`pipeline.py:54-100`（代际/`_would_trigger_llm`） |
| 策略来源 | 5 键 |
| 关键改动 | 框架删掉 `_run` 里的 `debounce = config.get(...)`/`interrupt_enable` 读取（`pipeline.py:270-278`、`:483`、`:489`），改为读 `runtime.<policy>`（由模块设置的属性）；`LlmPool` 改为模块持有的实例 |
| 测试 | `tests/plugins/test_llm_reply_policy.py`：连续三条消息合并成一请求；打断开启/关闭的差异；卸载后回框架默认（立即请求、不打断） |

### 3.3 `llm_context`（指代/展开/群记录/预历史）★ 需要 context provider 注册表

| 项 | 内容 |
|---|---|
| 接管 | `supersedes = ("context",)`；实现 `collect(req) -> str \| None` 注册进 `runtime.context_providers` |
| 机制来源 | `loop/context_blocks.py`（`_build_group_pre_history`/`_referent_block`/`_format_session_history`）、`context/*`（`group.py`/`referent.py`/`focus.py`/`nicknames.py`/`display_names.py`）、`tools/context_tools.py`、`group_log/*` |
| 策略来源 | 16 键 |
| 框架侧保留 | `PromptRequest.pre_history_text`/`referent_text`/`group_log_text` 三个槽位字段（**装配器只管顺序，不管来源**） |
| 关键改动 | `prompt_prep.py:155-274` 的 5 段硬调 → 遍历 `runtime.context_providers`；`tool_source.py:108-111` 的 `context_expand_enable` → 由本模块注册工具源 |
| 测试 | `tests/plugins/test_llm_context.py`：装模块后 `background`/`group_log` 块出现；卸载后消失；`describe(req)` 块行数变化可断言 |

### 3.4 `llm_message_meta`（消息元信息）

| 项 | 内容 |
|---|---|
| 接管 | `supersedes = ("message_meta",)` |
| **不需要新槽** | 用现成 `@llm_hook("pre_request", order=20)` 改写 `ctx.user_text`（框架 `assembly/enhance.py:72 format_user_context` 就是这么做的，整套逻辑连同 13 键直接搬走） |
| 机制来源 | `assembly/enhance.py` 全部（`collect_user_context`/`format_user_context`/`_collect_at_info`/`_collect_quote_info`/`_display_sender`，共 240 行） |
| 关键改动 | **删掉 `install_framework_hooks` 里的 3 个注册**（`enhance.py:313-315`），框架不再自带任何 `pre_request` 钩子 |
| 测试 | 搬移后 `tests/agent/assembly/test_llm_user_format.py` 改为"装模块后"的用例；卸载后 `user_text` 等于原始正文 |

### 3.5 `llm_compress`（上下文压缩）

| 项 | 内容 |
|---|---|
| 接管 | `supersedes = ("compress",)` |
| **不需要新槽** | `@llm_hook("post_assemble", order=10)` 里裁历史（`ctx.messages` 可整体改写） |
| 机制来源 | `assembly/compress.py`（92 行）+ `assembly/token_budget.py`（184 行） |
| 策略来源 | 2 键（`context_token_budget` 已删，改为模块内按模型窗口自动算） |
| 关键改动 | `assembler.apply_post_assemble` 删掉 `apply_request_policy` 调用（`:768-779`）与 `_request_limits`（`:707-733`） |
| 测试 | 长历史在装模块后被压缩成摘要块；卸载后恢复原始长历史 |

### 3.6 `llm_media`（图片/动图理解）★ 需要 context provider + 请求前置钩子

| 项 | 内容 |
|---|---|
| 接管 | `supersedes = ("media",)` |
| 机制来源 | `media/image.py`（495 行）、`media/animated.py`（拆帧）、`media/store.py`（落盘/GC） |
| 策略来源 | 7 键 |
| 关键改动 | `pipeline._resolve_images`（`:138-191`）整体搬走；框架只保留 `PromptRequest.user_images` 这个**入参槽**，装配器按模态门控（`place_images` 不动） |
| 测试 | `tests/agent/media/*`（5 个文件）改为装模块后跑；卸载后图片不随请求发送 |

### 3.7 `llm_memory` + `llm_knowledge`（记忆 / 知识库）★ 需要 context provider + tool source

| 项 | 内容 |
|---|---|
| 接管 | `supersedes = ("memory",)` / `("knowledge",)` |
| 机制来源 | `memory/*`（manager/store/recall/extract/detect/tool/commands，约 1400 行）、`knowledge/*`（约 400 行） |
| 策略来源 | 17 键 / 3 键 |
| 关键改动 | 框架删 `runtime.memory`/`runtime.knowledge` 属性与 `context_blocks._memory_block/_memory_autosave/_memory_consolidate`、`tool_source.py:122-134`；`SessionManager.on_archive` 回调改由模块在 `on_load` 里注册 |
| 迁移注意 | `memory.store` 的数据文件在 `data/llm/<bot>/`，模块化后**数据位置不变**（只挪代码），避免用户数据丢失 |
| 测试 | `tests/agent/memory/*`（10 个文件）与 `tests/agent/knowledge/*` 改为模块用例；新增"卸载后不再召回"用例 |

### 3.8 `llm_tools_extra`（OneBot + Tavily + MCP）★ 需要 tool source 注册表

| 项 | 内容 |
|---|---|
| 接管 | `supersedes = ("onebot_tools","web_search","mcp")` |
| 机制来源 | `tools/onebot/*`（executor/manifest/security，约 380 行）、`tools/web_search.py`、`tools/mcp.py`、`tools/policy.py` |
| 策略来源 | 14 键 |
| 关键改动 | `tool_source.py:114-146` 三段 if 删除；框架只保留"工具源注册表遍历 + 四类开关过滤"的骨架（且开关过滤也交给模块自己的 `enabled` map） |
| 测试 | `tests/infrastructure/test_llm_onebot_tools.py`、`tests/agent/tools/test_llm_tavily_search.py` 改为模块用例 |

### 3.9 `llm_assembly`（组装档位/行为规则）

| 项 | 内容 |
|---|---|
| 接管 | `supersedes = ("assembly",)`；实现 `build(req) -> list[dict]` |
| 机制来源 | `assembly/assembler.py` 的 `build_behavior`/`build_emoji_hint`/`message_meta_instruction`/`user_message_format` + `persona/rules.py`（409 行）+ `persona/emoji.py` |
| 策略来源 | 7 键 |
| 关键改动 | 框架 `BLOCKS` 从 12 块缩到 **6 块**：`persona` / `skills` / `context`(占位，由 provider 填) / `history` / `user`。其余 6 块随本模块走（它在 `build()` 里自己拼） |
| 测试 | 装模块后 `describe(req)` 出现 behavior/emoji/meta 行；卸载后只剩 6 块 |

### 3.10 `llm_proactive`（主动消息，纯业务）

| 项 | 内容 |
|---|---|
| 接管 | `supersedes = ("proactive",)`；**不需要框架槽** —— 自己 `spawn` + `send_to` + `llm_turn` |
| 机制来源 | `proactivity/manager.py`（520 行）整份搬走；`proactivity/entry.py`（TaskEntry）一并 |
| 策略来源 | 17 键 |
| 关键改动 | 删 `runtime.py:106 self.proactive = ProactiveManager(self)`；删 `pipeline.py:527-538`(`on_bot_sent`) 与 `:562-571`(`_observe`) 两处硬调 —— 改为**框架派发事件**：`@llm_hook("pre_request", order=90)` 里模块自己记"用户说话了"，`@send_hook` 里记"Bot 说话了"。框架零耦合 |
| 测试 | `tests/agent/proactivity/test_proactive.py`（10 例）改为模块用例 |

### 3.11 `llm_schedule`（定时任务，纯业务）

| 项 | 内容 |
|---|---|
| 接管 | `supersedes = ("schedule",)`；用 `ctx.services.scheduler`（`SCHEDULES` + `register`）或 `spawn` |
| 机制来源 | `proactivity/scheduler.py`（737 行）+ `tools/schedule.py`（155 行）+ `domain/time_parser.py`（292 行） |
| 策略来源 | 3 键 |
| 关键改动 | 删 `runtime.py:105 self.scheduler`；`claude_tools`/`tool_source.py:73-77` 的 `schedule_enable` 判据消失；`SchedulerService`（`application/cron_service.py`）**保留为框架基础服务**（`SCHEDULES` 是模块声明面），但增强为 `update_job`/`list_jobs`/`run_job_now`（对齐 AstrBot `cron_manager`） |
| 测试 | `tests/agent/proactivity/test_scheduler.py`（6 例）+ `test_schedule.py` 改为模块用例 |

---

## 4. 框架必须先补的 3 个槽 + 6 个原料接口

> 顺序上这一节**必须在 §3 之前完成**，否则模块无处可接管。

### 4.1 三个新槽（全部复用 `RuntimeSlotFeatureController`）

| 槽 | feature_id | runtime 属性 | 契约 | 默认实现 |
|---|---|---|---|---|
| 输出策略 | `output` | `runtime.output` | `respond(ctx, req) -> AsyncIterator[OutboundItem]` | `BlockingOutput`（一次性出整段） |
| 上下文提供者注册表 | `context` | `runtime.context_providers: list` | `name` / `order` / `collect(req) -> str \| None`（**注册制，非排他**） | 空列表 |
| 工具源注册表 | `tools` | `runtime.tool_sources: list` | `name` / `collect(req) -> list[ToolSpec]`（注册制） | 只有模块 `@tool` 与系统会话工具 |

装槽型两个已有（`assembly` / `provider_invoker`）；新增 `output` 是第三个装槽型。
`context`/`tools` 是**注册表**（R3 可组合），不需要租约。

### 4.2 六个原料接口

| 接口 | 形态 | 用途 |
|---|---|---|
| `run_turn(runtime, TurnRequest, *, ctx, on_sentence)` | 函数（`loop/turn.py` 新） | 无事件的完整一轮（B-1） |
| `BaseModule.llm_turn(...)` | 模块方法 | 同上，模块侧入口 |
| `BaseModule.send_to(session_id, message)` | 模块方法 | 按会话发消息（消 5 处重复） |
| `BaseModule.spawn(coro)` | 模块方法 | 带 owner 的后台任务 |
| `delivery.send_item(...)` | 函数（`delivery/channel.py` 新） | 唯一发送出口 + 内建 `[reply]`/`[@qq]` 解析 + `pre_send/post_send` |
| `framework_tools(runtime, ...)` | 函数（`tools/catalog.py` 新） | 让模块拿到框架工具集 |

### 4.3 机制去配置化（逐处清单）

迁移的**实质**是把这些读配置的行改成入参：

| 文件:行 | 现在 | 改成 |
|---|---|---|
| `loop/pipeline.py:310` | `config.get("stream_output")` | `runtime.output` 槽（模块决定） |
| `loop/pipeline.py:270-278` | `message_debounce_*` | 模块决定的 `pool.wait_for_continue(...)` |
| `loop/pipeline.py:483` | `interrupt_enable` | `getattr(runtime, "interrupt_enabled", False)`（模块设置） |
| `loop/pipeline.py:324` | `build_outbound_builder(event, config)` | `send_item(..., directives=True)` 无条件 |
| `loop/pipeline.py:375/409` | `stream_send_pool_enabled` | `runtime.output` 槽内部 |
| `loop/chat.py:257-261` | `stream_sentence_max_length` | `run_turn(sentence_max_length=...)` |
| `loop/prompt_prep.py:198` | `schedule_enable` | 工具源注册表 |
| `loop/prompt_prep.py:204` | `_build_speech(config)` | `runtime.output` 槽 |
| `loop/prompt_prep.py:255-274` | `image_understanding_enable` | 媒体模块的 context provider |
| `loop/tool_source.py:73-146` | 5 段 if | 工具源注册表 |
| `loop/context_blocks.py:199-246` | `runtime.memory` 直调 | context provider |
| `assembly/assembler.py:529-568` | `speech_via_tool(config)` | 删（`output_style` 由槽表达） |
| `assembly/assembler.py:768-779` | `apply_request_policy` | 压缩模块的 `post_assemble` |
| `assembly/enhance.py:308-315` | 框架注册 3 个钩子 | 删（`llm_message_meta` 模块） |
| `proactivity/*` 全部 | — | 迁移为模块 |

---

## 5. 数据迁移（破坏性，但不清空用户配置）

**新文件**：`scripts/migrate_v2_split_config.py`

```python
"""把 agent 配置行按 §2 的分区表拆到各模块配置行。

- 幂等：跑多次结果一致（模块行已存在则合并，不覆盖已有值）
- dry-run：`--dry-run` 只打印将要写入的内容
- 备份：先把 data/app.db 复制到 data/backups/app.<ts>.db
- 未知键：写到 logs/migrate_v2_dropped_keys.json 供人工确认，不静默丢
"""
CORE, DELETE, MODULES = ...   # 从 app/agent/config_schema.py 的分区常量 import
```

配套：把分区表提升为**代码常量** `app/agent/config_partition.py`（`CORE_KEYS` / `DELETED_KEYS` / `MODULE_KEYS`），
migration 脚本、schema 生成、`test_extensibility_contract` 三处共用同一份真相。

**测试**：`tests/infrastructure/test_migrate_v2_config.py`
① 造一份旧格式配置（160 键全填非默认值）→ 跑迁移 → 断言 20 键留在 agent 行、129 键按表落到 13 个模块行、11 键被记录进 dropped 清单；
② 再跑一次 → 断言无变化（幂等）；
③ 模块行已有自定义值时 → 断言不被旧值覆盖。

---

## 6. 前端重做

| 现状 | v2 |
|---|---|
| `sections.json` 有 9 个 section（4 个 agent + 5 个 advanced），承载 160 字段的显式归属 | 只剩 **2 个 section**：`/agent/basic`（20 键）+ `/agent/permission`（3 键，含在 basic? 保留独立） |
| `advanced/*` 三个页 | **整个删除** |
| 模块配置 | 既有 `/modules/<name>/config` 通用表单（由模块 `config_schema` 自动渲染）—— 13 个新模块天然接入 |
| `test_agent_section_contract.py`（20 例） | 重写为：只剩 2 个 section、字段覆盖必须等于 `CORE_KEYS`、不得出现已迁移/已删键 |
| 模块页导航 | 新增「LLM 扩展」分类（13 个模块 `category="LLM"` 自动聚合） |

---

## 7. 施工波次

> 每波独立可合并；**W0-W2 不改变任何用户可见行为**，W3 起才是破坏性。

| 波 | 目标 | 产出 | 验收 | 可回滚 |
|---|---|---|---|---|
| **W0** | 接线与机制去配置化准备 | 出站链按注册表、`ServiceAccess` 补 4 字段、`BUILTIN_CONTROLLERS` 表、`config_partition.py` | `test_extensibility_contract` 绿；`full` ≥10 | 纯新增 |
| **W1** | 六个原料接口 | `run_turn` / `llm_turn` / `send_to` / `spawn` / `send_item` / `framework_tools` | `test_run_turn_parity` 绿（与 `generate_response` messages 逐字相同） | 纯新增 |
| **W2** | 三个新槽 | `output` / `context_providers` / `tool_sources` + 机制去配置化（§4.3 全表） | 框架侧所有 `config.get("策略")` 消失（可用 grep 断言） | 每个槽独立 revert |
| **W3** | 迁移"输出侧"模块 | `llm_output`（合并 stream+speech，`mode=text\|stream\|speech`）+ `llm_assembly` | 开关模块 → 三种输出形态；卸载 → 回非流式 | 保留旧分支一版 |
| **W4** | 迁移"上下文侧"模块 | `llm_context` / `llm_message_meta` / `llm_compress` / `llm_media` / `llm_memory` / `llm_knowledge` | `describe(req)` 块行随模块增减；卸载即消失 | 一模块一提交 |
| **W5** | 迁移"工具侧" + "策略侧"模块 | `llm_tools_extra` / `llm_reply_policy` | 工具清单随模块增减；防抖/打断由模块掌控 | 一模块一提交 |
| **W6** | 迁移"业务侧"模块 | `llm_proactive` / `llm_schedule` | 删除 `runtime.proactive`/`runtime.scheduler` 后功能不变 | 保留旧类一版 |
| **W7** | **删配置 + 数据迁移 + 前端重做** | `config_schema.py` 160→20；`sections.json` 9→2；migration 脚本 | 迁移幂等测试绿；前端只剩 2 页 | DB 备份 |
| **W8** | 收尾 | 文档（MODULE_DEV 重写）、`capabilities()` 20 feature、contract 棘轮上调 | `full` ≥40；`pytest -q` 全绿 | — |

**W3 的合并决策**：`llm_output_stream` 与 `llm_speech` 抢同一个 `output` 槽会让行为依赖加载顺序，
所以 **W3 合并为一个 `llm_output` 模块**，用 `mode` 配置三选一（`text` / `stream` / `speech`）。
若将来需要"多个输出策略并存"，再拆开并依赖租约的"先到先得 + 交棒"语义（已有实现）。

---

## 8. 测试与闸门

| 闸门 | 条件 |
|---|---|
| G-W0 | `test_extensibility_contract` 绿；`ServiceAccess` 新字段已进 capabilities |
| G-W1 | 新增 6 个接口各有用例；`test_run_turn_parity` 绿；既有 1758 例全绿 |
| G-W2 | `grep -rn 'config.get("stream_output"\|speech_via_tool\|context_expand_enable\|interrupt_enable\|message_debounce' app/agent/loop app/agent/assembly` 为空 |
| G-W3 | 输出三形态可切换；`pre_send/post_send` 在三种形态下都触发 |
| G-W4 | 每个模块：装→块出现、卸→块消失（`describe(req)` 断言） |
| G-W5 | 工具清单与防抖/打断行为由模块决定 |
| G-W6 | 删 `runtime.proactive`/`runtime.scheduler` 后 `tests/agent/proactivity/*` 全绿 |
| G-W7 | `config_schema` 键数 == 20；migration 幂等；`sections.json` section 数 == 2 |
| G-W8 | `tests/architecture/test_extensibility_contract.py` 的 `BASELINE_FULL` 上调到 40+ 且绿；`pytest -q` 全绿；`ruff check .` 无**新增** |

---

## 9. 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| 用户升级后配置"消失" | 高 | migration 脚本 + DB 备份 + `dropped_keys.json` + 升级日志说明 |
| 13 个模块的加载顺序敏感（模块越少越稳） | 中 | W3 合并输出模块；其余用注册表（R3 可组合，与顺序无关） |
| 删除 `runtime.proactive/scheduler` 后旧插件崩 | 中 | 保留一版 shim（`AgentRuntime.proactive` 返回模块代理对象，`__getattr__` 转发 + 弃用告警） |
| `prompt_prep` 拆成 provider 拉取后性能回退 | 低 | provider 之间并行 `gather`，并加"本轮块耗时"到 telemetry |
| `sections.json` 重写破坏前端 | 中 | `test_agent_section_contract.py` 改写并保留"每个字段恰好归属一次"的核心断言 |
| 迁移 129 键时改错归属 | 高 | 分区表做成代码常量 + 迁移测试造全量旧配置做断言 |
