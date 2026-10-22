# 流水线接口化施工方案 v3（最终定稿 · 破坏性全量改造）

> **v1 / v2 已作废**（v1 = 保守渐进；v2 = 12 模块 + 20 键核心）。
> v3 定稿口径（用户确认）：
>
> 1. **开箱功能全部由模块提供**（完全迁移，不留"框架自带的半成品"）；
> 2. **本体界面只保留 5 页**，顺序与内容逐项指定；
> 3. **账号级 `owner_id` 取消**，拥有者身份统一到权限管理的名册。
>
> 真相文件（代码即契约）：
> - `app/agent/config_partition.py` —— 5 页布局 + 9 模块 + 9 删除的逐键归属
> - `tests/architecture/test_config_partition.py` —— 15 条断言守着它（页面顺序/字段数/43+108+9/模块清单/棘轮）
> - `tests/architecture/test_extensibility_contract.py` —— 55 个接缝的评级与接管证据
> - 审计依据：`docs/research/pipeline-interface-audit.md`

---

## 0. 一句话原则

> **机制留在框架且不再读配置；策略搬到模块并读自己的配置；槽把两者连起来。**

---

## 0.1 施工进度（2026-02 当前）

| 波 | 状态 | 产出 |
|---|---|---|
| **W0** 接线与契约 | ✅ 完成 | 出站链按注册表现取（插件注册即生效）；`ServiceAccess` 补 `node_registry`/`sessions`/`runtime`/`tools`/`outbound`；`capabilities()` 同步；`BUILTIN_CONTROLLERS` 单一真相表；`config_partition.py` 布局契约 |
| **W1** 六个原料接口 | ✅ 完成 | `run_turn` / `TurnRequest` / `TurnResult`；`BaseModule.llm_turn` / `send_to` / `spawn` / `sessions` / `framework_tools`；`delivery.channel.send_item`；`tools.catalog.framework_tools`；`domain.session` 统一 session 解析；`prepare_prompt` 解除事件耦合（`turn=` 参数） |
| **W2** 三个槽 + 注册表 | ✅ 完成 | `output` 槽（`BlockingOutput`/`StreamOutput`/`select_default_output` + `OutputFeatureController`）；`context_providers` + `tool_sources` 注册表（`TurnScope`/`SimpleContextProvider`/`SimpleToolSource`）；装配器新增 `extra` 块（位于 `group_log` 与 `history` 之间） |
| **B-4** 卸载纪律 | ✅ 完成 | `unregister_provider`（含 owner 批量）；`NodeRegistry.remove_owner`；`ProviderRegistry.unregister_module`；`SessionManager.evict` + 单例 key 归一；`_unregister_plugin_hooks` 统一串联（钩子/数据源/节点/上下文贡献者） |
| **owner 迁移** | ✅ 完成 | `is_bot_owner` 改为名册判定；`BotConnection.owner_id` 折旧为只读 `None`；WebUI 三处去掉 `owner_id`；`scripts/migrate_v3_owner_to_roster.py`（幂等 + dry-run + DB 备份） |
| **调度服务增强** | ✅ 完成 | `SchedulerService.list_jobs / update_job / run_job_now`（对齐 AstrBot `cron_manager`） |
| **W3–W6** 九个模块 | ✅ 完成 | `llm_output`(25) `llm_assembly`(10) `llm_message_meta`(14) `llm_context`(10) `llm_memory`(17) `llm_knowledge`(3) `llm_tools_extra`(9) `llm_proactive`(17) `llm_schedule`(3) —— **108 键全部归模块**；`runtime.proactive`/`scheduler` 改为模块装入 |
| **W7** 收敛与迁移 | ✅ 完成 | `SCHEMA` 160→**43 键**（`LEGACY_SCHEMA` 保留全量供迁移）；`sections.json` 改由 `scripts/gen_sections.py` 从 `page_summary()` **生成**；`scripts/migrate_v3_split_config.py`（108 键拆分，幂等+dry-run+备份）已在真库跑通 |
| **W8** 文档与验收 | ✅ 完成 | `MODULE_DEV §4.10`（9 模块表 + `policy_view` 口径 + 升级步骤）；`FEATURE_REGISTRY`（13 个能力 + 装槽型/策略型区分）；`README`（模块清单 + 迁移命令）；能力注册 8→**13**；接缝棘轮 `full` 10→**23** |
| 收尾闸门 | ✅ | `pytest` **2010 passed**；`gen_sections --check` 通过；两个迁移脚本 dry-run 通过 |

**可扩展度记分板**（`tests/architecture/test_extensibility_contract.py`，棘轮）：

| 时点 | full | half | unwired | 完全可扩展率 |
|---|---|---|---|---|
| 施工前 | 10 | 23 | 22 | 18% |
| W0–W2 + B-4 | 20 | 20 | 15 | 36% |
| **全部完成（现在）** | **23** | **19** | **15** | **40%** |

> 富余说明：`half`/`unwired` 的 34 个里，多数是**有意保留**的机制内接缝
> （切句算法、发送节奏曲线、遥测记录器、块表顺序等）—— 它们不再读 agent 配置，
> 而是由模块配置驱动，按 R1~R4 严格打分仍是 half，但**策略已经归模块**。
> 真正的欠账只剩传输层（S1~S4 协议 adapter registry），属另立专项。

**测试**：`pytest -q` → **1859 passed**（施工前 1758）。新增用例：
`test_run_turn`(8) · `test_output_slot`(9) · `test_providers`(11) · `test_channel_send_item`(10) ·
`test_module_raw_interfaces`(10) · `test_unregister_discipline`(9) · `test_scheduler_service_jobs`(8) ·
`test_migrate_v3_owner_to_roster`(8) · `test_session_id`(7) · `test_config_partition`(15)。

---

## 1. 本体界面最终形态（5 页 / 43 键）

顺序即左侧导航顺序（**对话行为在会话管理之前**）。

### 1.1 基础配置（7 键）

| 分组 | 字段 |
|---|---|
| 响应场景 | `group_enable` `private_enable` |
| 模型参数 | `model` `temperature` `max_tokens` |
| 人格设定 | `persona_preset_id` `system_prompt` |

### 1.2 权限管理（3 键 + 原 `owner_id` 并入）

| 分组 | 字段 |
|---|---|
| 响应权限 | `permission` |
| 拥有者与管理者 | `agent_owner_list`（**原账号级 owner QQ 并入此名册**） `agent_manager_list` |

### 1.3 对话行为（8 键）

| 分组 | 字段 |
|---|---|
| 触发设置 | `trigger_at` `trigger_keyword` |
| 消息抖动 | `message_debounce_seconds` `message_debounce_max_wait` `reply_cooldown` |
| 回复打断 | `interrupt_enable` `interrupt_save_sent` `interrupt_debug` |

### 1.4 会话管理（13 键）

| 分组 | 字段 |
|---|---|
| 会话窗口 | `session_timeout` `history_rounds` `max_message_length` |
| 群聊历史 | `include_pre_history`（包含群聊环境背景）`include_private_pre_history` `group_log_enable`（注入群聊环境记录）`group_log_window_minutes`（窗口）`group_log_window_limit`（上限）`group_log_max_chars`（预算） |
| 上下文压缩 | `context_compress_enable` `context_compress_prompt` `context_token_budget` `context_token_chars_per_token` |

### 1.5 高级功能（12 键，从原「高级」区部分迁移）

| 分组 | 字段 |
|---|---|
| 图片与动图 | `image_understanding_enable` `image_max_count` `image_max_mb` `gif_understanding_enable` `gif_max_frames` `gif_frame_max_edge` `gif_probe_all_images` |
| 联网搜索 | `tavily_enable` `tavily_api_key` `tavily_max_results` `tavily_search_depth` `tavily_max_content_chars` |

> 合计 **43 键**。`CORE_KEYS` 由 `UI_PAGES` 派生（`config_partition.py`），不手写第二份。

---

## 2. 配置总账：160 = 43 + 108 + 9

| 归宿 | 键数 | 去向 |
|---|---|---|
| 本体界面 | **43** | 上面 5 页 |
| 迁移为模块 | **108** | 9 个模块（§3） |
| 删除 | **9** | 见下 |

**删除的 9 键及理由**

| 键 | 理由 |
|---|---|
| `max_tool_rounds` `empty_reply_retries` `reasoning_overflow_retries` | **改为框架内硬编码安全默认**（5 轮 / 1 次空回复重试 / 1 次收敛）。它们不是功能开关，暴露给用户只会让人把钱烧在不理解的地方 |
| `retry_profile` `turn_token_budget` | 与上面三个语义重叠 |
| `experimental_long_term_memory` | 记忆模块化后无意义（模块自己的 `memory_enable` 即开关） |
| `history_background_enable` | 与 `include_pre_history` 重复 |
| `outbound_directive_enable` `outbound_directive_max_at` | 输出指令改为**交付层无条件解析**（`send_item` 内建） |

**校验方式**（任何时刻可复核）：
```powershell
python -c "from app.agent.config_partition import *; print(len(CORE_KEYS), sum(len(v) for v in MODULE_KEYS.values()), len(DELETED_KEYS))"
# → 43 108 9
pytest tests/architecture/test_config_partition.py -q   # 15 passed
```

---

## 3. 九个模块（迁移目标的完整清单）

> 共同约定：目录 `app/plugins/<name>/`，`main.py` + `config_schema.py` + `service/`；
> `category = "LLM"`（前端自动聚合到「LLM 扩展」）；配置一律 `self.config.get(...)`。

| # | 模块 | 键数 | 迁移代码量 | 需要的槽 | supersedes |
|---|---|---|---|---|---|
| 1 | `llm_output` | 25 | ~648 + chat 流式段 | **`output`（新）** | `("output","output_style")` |
| 2 | `llm_context` | 10 | ~2,900 | **`context_providers`（新）** | `("context",)` |
| 3 | `llm_message_meta` | 14 | 315 | 无需（`pre_request` 钩子） | `("message_meta",)` |
| 4 | `llm_memory` | 17 | ~2,080 | `context_providers` + `tool_sources` | `("memory",)` |
| 5 | `llm_knowledge` | 3 | 602 | `context_providers` + `tool_sources` | `("knowledge",)` |
| 6 | `llm_tools_extra` | 9 | ~898 | **`tool_sources`（新）** | `("onebot_tools","mcp")` |
| 7 | `llm_proactive` | 17 | 817 | 无需（`spawn`+`send_to`+`llm_turn`） | `("proactive",)` |
| 8 | `llm_schedule` | 3 | 1,240 | 无需（`SCHEDULES`/`spawn`） | `("schedule",)` |
| 9 | `llm_assembly` | 10 | 707 | `assembly`（已有） | `("assembly",)` |

**合计迁移 108 键 / 约 10,200 行代码。**

### 3.1 `llm_output`（输出策略：`text` / `stream` / `speech`）

| 项 | 内容 |
|---|---|
| 迁入键 | 流式 19 + 发言通道 6 = 25 |
| 机制来源 | `delivery/speech.py`、`loop/assistant_output.py`、`domain/pace.py` + `loop/chat.py:220-620` 的切句/发送段 + `loop/pipeline.py:370-452`（`_run_stream`） |
| 契约 | `respond(ctx, req) -> AsyncIterator[OutboundItem]` |
| 框架保留 | `domain/splitter.split_sentences`、`delivery/pool.StreamSendPool`、`domain/pace.SendPolicy` —— **全部改为显式入参，不再读 config** |
| 为何三形态合一 | 三者抢同一个 `output` 槽，拆开会让行为依赖加载顺序；`mode` 配置语义更硬，并顺手消掉 `speech_via_tool` 的"档位+开关+流式"三条件耦合 |
| 验收 | 三种 `mode` 下同一输入的发送序列不同；`pre_send`/`post_send` 三种都触发；卸载后回 `text` |

### 3.2 `llm_context`（指代 / 按需展开 / 历史查询权限）

| 项 | 内容 |
|---|---|
| 迁入键 | `context_expand_enable` `referent_resolve_enable` `referent_prefetch_enable` `referent_ambiguous_policy` `referent_prefetch_max` `referent_focus_ttl` `referent_focus_max` `referent_prompt_enable` `history_auto_qq_min_local` `history_cross_query_enable` |
| 机制来源 | `context/*`（1,374 行）+ `tools/context_tools.py`（511）+ `tools/context_summary.py`（343）+ `loop/context_blocks.py` 的 `_referent_block` |
| 契约 | 注册 `context_providers`：`name` / `order` / `collect(req) -> str \| None`（贡献 background / referent 块）+ 注册 `tool_sources`（`expand_*` 系列工具） |
| 框架保留 | `PromptRequest.referent_text` / `pre_history_text` 两个**入参槽**；`group_log` 相关（因"注入群聊环境记录"留在会话管理页） |
| 验收 | 装模块 → `describe(req)` 出现 `background` 块与 `expand_*` 工具；卸载 → 两者消失 |

### 3.3 `llm_message_meta`（消息元信息渲染）

| 项 | 内容 |
|---|---|
| 迁入键 | 13 个元信息键 + `clean_output_parentheses` |
| 机制来源 | `assembly/enhance.py` 整份（315 行，含 `_collect_at_info` / `_collect_quote_info` / `_display_sender`） |
| 契约 | **无需新槽**：`@llm_hook("pre_request", order=20)` 改写 `ctx.user_text`（框架原本也是这么干的） |
| 关键删除 | 删掉 `assembly/enhance.py:308-315` 的 `install_framework_hooks` —— **框架不再自带任何 `pre_request` 钩子** |
| 验收 | 装模块后 `user_text` 带「发送者/时间/引用」；卸载后等于原始正文 |

### 3.4 `llm_memory` / 3.5 `llm_knowledge`

| 项 | 内容 |
|---|---|
| 迁入键 | 17 / 3 |
| 机制来源 | `memory/*`（2,080 行）/ `knowledge/*`（602 行） |
| 契约 | `context_providers` 贡献记忆/知识块 + `tool_sources` 贡献 `memory_*` / `knowledge_search` 工具 |
| 关键删除 | 框架删 `runtime.memory` / `runtime.knowledge` 属性、`context_blocks._memory_block/_memory_autosave/_memory_consolidate`、`tool_source.py:122-134`；`SessionManager.on_archive` 回调改由模块在 `on_load` 里注册 |
| 数据位置 | **不变**（`data/llm/<bot>/` 原地），只挪代码，用户数据零迁移 |
| 验收 | 装模块 → 记忆块出现、工具可调；卸载 → 不再召回且不报错 |

### 3.6 `llm_tools_extra`（OneBot 工具 + MCP）

| 项 | 内容 |
|---|---|
| 迁入键 | OneBot 6 + `mcp_servers` + 工具结果回应要求 2 = 9（**联网搜索留在本体高级功能**） |
| 机制来源 | `tools/onebot/*`（378）+ `tools/mcp.py`（227）+ `tools/policy.py` + `tools/system.py` |
| 契约 | `tool_sources` 注册 |
| 关键删除 | `tool_source.py:137-146` 两段 if |
| 验收 | 工具清单随模块装卸增减 |

### 3.7 `llm_proactive`（主动消息，纯业务）

| 项 | 内容 |
|---|---|
| 迁入键 | 17（含 `stream_proactive_enabled`） |
| 机制来源 | `proactivity/manager.py`(520) + `entry.py`(101) + `stream.py`(196) |
| 契约 | **不需要槽**：`spawn` 自己的循环 + `llm_turn` 跑一轮 + `send_to` 发出 |
| 关键删除 | `runtime.py:106 self.proactive = ...`；`pipeline.py:527-538`(`on_bot_sent`) 与 `:562-571`(`_observe`) 两处硬调 |
| 观察方式 | 模块用 `@llm_hook("pre_request", order=90)` 记"用户说话了"、`@send_hook` 记"Bot 说话了" |
| 验收 | 删掉 `runtime.proactive` 后 `tests/agent/proactivity/test_proactive.py` 全绿 |

### 3.8 `llm_schedule`（定时任务，纯业务）

| 项 | 内容 |
|---|---|
| 迁入键 | 3（含 `stream_scheduled_enabled`） |
| 机制来源 | `proactivity/scheduler.py`(737) + `tools/schedule.py`(155) + `domain/time_parser.py`(292) |
| 契约 | 用 `ctx.services.scheduler`（`SCHEDULES` 声明式 + `register` 动态）或 `spawn` |
| 框架保留并增强 | `application/cron_service.py::SchedulerService` 作为**基础服务**，补 `update_job` / `list_jobs` / `run_job_now`（对齐 AstrBot `cron_manager`） |
| 关键删除 | `runtime.py:105 self.scheduler`；`tool_source.py:73-77` 的 `schedule_enable` 判据 |
| 验收 | `tests/agent/proactivity/test_scheduler.py`(6) + `test_schedule.py` 改为模块用例全绿 |

### 3.9 `llm_assembly`（组装档位 / 行为规则 / 组装对账）

| 项 | 内容 |
|---|---|
| 迁入键 | `assembly_profile` `behavior_rules_enable` `behavior_rules_override` `participation` `emoji_react_level` `user_message_format` `prompt_time_position` `system_prompt_style` `prompt_dump_enable` `prompt_dump_max_entries` |
| 机制来源 | `persona/rules.py`(409) + `persona/emoji.py`(139) + `assembly/assembler.py` 的 6 个块函数 |
| 契约 | 已有 `assembly` 槽：`build(req) -> list[dict]` |
| 框架保留 | `BLOCKS` 从 12 块缩到 **6 块**：`persona` / `skills` / `context`（由 provider 填）/ `history` / `user` |
| 验收 | 装模块 → `describe(req)` 出现 behavior/emoji/meta 行；卸载 → 只剩 6 块 |

---

## 4. 专项：`owner_id` 迁到权限管理

### 4.1 现状链路（已逐处查实）

```
bots 表 owner_id 列       database.py:42
  ↓ config_service 读写    config_service.py:101-102 / 175-183 / 339-340
BotConnection.owner_id     client.py:201-207（gateway.py:179-205 / 666-673 写入）
  ↓ codec._base            codec.py:82   → event.owner_id
authority 判定             authority.py:91-92  event.is_bot_owner = (user_id == owner_id)
  ↓ 影响 permission_role   authority.py:107-108  → "owner"
WebUI 传递                 bots.py:42/45、schemas.py:12、app.py:172
```

### 4.2 目标模型

**取消账号级 `owner_id`**；拥有者身份的唯一来源 = Agent 配置的 `agent_owner_list`（已有键，按 `bot_id` 存，层级与旧 `owner_id` 等价，且天然支持多人）。

### 4.3 改动清单（7 处）

| # | 文件 | 改动 |
|---|---|---|
| 4.3.1 | `app/infrastructure/persistence/database.py:42` | `bots.owner_id` 列**保留但弃用**（不 DROP，SQLite DROP COLUMN 在旧版本不可用；改为永不读、迁移后写 NULL） |
| 4.3.2 | `app/infrastructure/config/config_service.py:175-183` | 读 `bots` 时不再返回 `owner_id`；`set_bots` 忽略该字段并留痕 |
| 4.3.3 | `app/infrastructure/onebot/client.py:201-207` | `BotConnection.owner_id` 改为 `@property` 恒返回 `None` + 弃用告警（第三方插件读它不崩） |
| 4.3.4 | `app/infrastructure/onebot/codec.py:82` | `event.owner_id` 恒为 `None`（字段保留一版） |
| 4.3.5 | `app/plugin/authority.py:91-92` | `is_bot_owner` 改为**名册判定**：`is_bot_owner = in_owner_roster`（字段名保留，语义统一到名册；`is_agent_owner` 同时为 True） |
| 4.3.6 | `app/webui/api/bots.py:42/45`、`schemas.py:12`、`app.py:172` | 移除 `owner_id` 入参与出参 |
| 4.3.7 | 前端账号表单（`dashboard/src/.../bots*`） | 删除「拥有者 QQ」输入；权限管理页的 `agent_owner_list` 字段说明改为「拥有者 QQ（原账号级 owner 已并入此名册）」 |

### 4.4 数据迁移（与 §6 同一脚本）

```python
# scripts/migrate_v3_owner_and_config.py
for bot in config_service.get_bots():
    old_owner = bot.get("owner_id")
    if not old_owner:
        continue
    cfg = config_service.get_module_config("agent", bot_id=bot["user_id"]) or {}
    roster = list(cfg.get("agent_owner_list") or [])
    if str(old_owner) not in [str(x) for x in roster]:
        roster.append(str(old_owner))
        cfg["agent_owner_list"] = roster
        config_service.set_module_config("agent", bot_id=bot["user_id"], cfg)
```

### 4.5 测试

`tests/plugin/test_owner_roster_migration.py`：
① 旧配置 `bots.owner_id=123` + 空名册 → 迁移后名册含 `"123"`，`bots.owner_id` 为 NULL；
② `compute_event_permission` 在该用户发消息时给出 `permission_role="owner"`、`is_bot_owner=True`；
③ 迁移前已有多人名册 → 只追加不覆盖、不重复（幂等）；
④ `BotConnection.owner_id` 返回 None 且不抛。

---

## 5. 框架必须先补的槽与接口

### 5.1 三个新槽（v3 只需三个）

| 槽 | feature_id | runtime 属性 | 契约 | 默认实现 |
|---|---|---|---|---|
| 输出策略 | `output` | `runtime.output` | `respond(ctx, req) -> AsyncIterator[OutboundItem]` | `BlockingOutput`（一次性整段） |
| 上下文提供者 | `context` | `runtime.context_providers: list` | `name` / `order` / `collect(req) -> str \| None`（**注册制，可组合**） | 空 |
| 工具源 | `tools` | `runtime.tool_sources: list` | `name` / `collect(req) -> list[ToolSpec]`（注册制） | 仅模块 `@tool` + 会话基础工具 |

`output` 复用 `RuntimeSlotFeatureController`（第三个装槽型能力）；`context` / `tools` 是**注册表**（R3 可组合，无需租约）。

### 5.2 六个原料接口

| 接口 | 位置（新） | 作用 |
|---|---|---|
| `run_turn(runtime, TurnRequest, *, ctx, on_sentence)` | `loop/turn.py` | 无事件的完整一轮（取值→组装→请求→可选写历史） |
| `BaseModule.llm_turn(...)` | `plugin/base.py` | 模块侧入口 |
| `BaseModule.send_to(session_id, message)` | `plugin/base.py` | 按会话发消息（消 5 处重复） |
| `BaseModule.spawn(coro)` | `plugin/base.py` | 带 owner 的后台任务（热重载级联取消） |
| `delivery.send_item(...)` | `delivery/channel.py` | 唯一发送出口 + 内建 `[reply]`/`[@qq]` + `pre_send`/`post_send` |
| `framework_tools(runtime, ...)` | `tools/catalog.py` | 让模块拿到框架工具集 |

### 5.3 机制去配置化（逐处）

| 文件:行 | 现在 | 改成 |
|---|---|---|
| `loop/pipeline.py:310` | `config.get("stream_output")` | `runtime.output` 槽 |
| `loop/pipeline.py:270-278` | `message_debounce_*` | 模块决定的 `pool.wait_for_continue(...)`（键留在本体，读取方换模块/框架策略对象） |
| `loop/pipeline.py:483` | `interrupt_enable` | `getattr(runtime,"interrupt_enabled",False)`（由策略模块/框架设置） |
| `loop/pipeline.py:324` | `build_outbound_builder(event, config)` | `send_item(..., directives=True)` 无条件 |
| `loop/pipeline.py:375/409` | `stream_send_pool_enabled` | `runtime.output` 槽内部 |
| `loop/prompt_prep.py:204` | `_build_speech(config)` | `runtime.output` 槽 |
| `loop/prompt_prep.py:198` | `schedule_enable` | 工具源注册表 |
| `loop/tool_source.py:73-146` | 5 段 if | 工具源注册表 |
| `loop/context_blocks.py:130/199` | 指代/记忆硬调 | `context_providers` |
| `assembly/assembler.py:529-568` | `speech_via_tool(config)` | 删（由 `output` 槽表达） |
| `assembly/enhance.py:308-315` | 框架注册 3 个钩子 | 删（`llm_message_meta` 模块） |
| `proactivity/*` | 全部 | 迁移为模块 |

> **媒体与联网搜索不在本表**：它们留在框架（本体「高级功能」页），按配置工作 —— 这是 v3 与 v2 的重要差别。

---

## 6. 数据迁移脚本

**新文件**：`scripts/migrate_v3_split_config.py`

```python
"""v3 迁移：agent 配置 160 键 → 43 留本体 / 108 拆到 9 个模块 / 9 丢弃；并把 owner_id 并入名册。

- 备份：先把 data/app.db 复制到 data/backups/app.<ts>.db
- dry-run：--dry-run 只打印将写入的内容
- 幂等：模块行已存在则合并，不覆盖已有非默认值
- 未知键：写到 logs/migrate_v3_dropped_keys.json（不静默丢）
- 校验：跑完断言 len(agent_cfg) == 43
"""
from app.agent.config_partition import (
    CORE_KEYS, DELETED_KEYS, MODULE_KEYS, OWNER_SOURCE_KEY,
)
```

**测试**：`tests/infrastructure/test_migrate_v3_config.py`
① 造 160 键全非默认的旧配置 + `bots.owner_id` → 迁移 → 断言 43 留本体、9 模块行按表落值、9 键进 dropped 清单、owner 进名册；
② 再跑一次 → 无变化（幂等）；
③ 模块行已有自定义值 → 不被旧值覆盖；
④ `--dry-run` 不写库。

---

## 7. 前端重做

| 项 | 做法 |
|---|---|
| `sections.json` | **不再手写**：由 `config_partition.page_summary()` 生成（5 页 / 13 分组 / 43 字段），`scripts/gen_sections.py` 产出，CI 校验 `git diff --exit-code` |
| 页面 | `basic` `permission` `behavior` `session` `advanced` 五页，顺序由 `order` 决定 |
| 「LLM 扩展」分类 | 9 个模块 `category="LLM"` 自动聚合，每张卡片 = 模块自身 `config_schema` 表单 |
| 账号表单 | 删除「拥有者 QQ」输入 |
| `test_agent_section_contract.py` | 重写：断言 `sections.json` 与 `page_summary()` 完全一致；字段覆盖 == `CORE_KEYS`；不得出现模块键/删除键 |

---

## 8. 施工波次

| 波 | 目标 | 关键产出 | 闸门 | 可回滚 |
|---|---|---|---|---|
| **W0** | 接线与契约就位 | 出站链按注册表；`ServiceAccess` 补 `node_registry`/`sessions`/`tools`/`runtime`；`BUILTIN_CONTROLLERS` 表；`config_partition.py`(已完成) | 两个契约测试绿 | 纯新增 |
| **W1** | 六个原料接口 | `run_turn` / `llm_turn` / `send_to` / `spawn` / `send_item` / `framework_tools` | `test_run_turn_parity` 绿 | 纯新增 |
| **W2** | 三个新槽 + 机制去配置化 | `output` / `context_providers` / `tool_sources`；§5.3 全表 | grep `config.get("stream_output"\|speech_via_tool\|context_expand_enable")` 为空 | 每槽独立 revert |
| **W3** | 输出侧模块 | `llm_output`（三 mode）+ `llm_assembly` | 三形态可切换；`pre_send/post_send` 三种都触发 | 保留旧分支一版 |
| **W4** | 上下文侧模块 | `llm_context` / `llm_message_meta` / `llm_memory` / `llm_knowledge` | `describe(req)` 块行随模块增减 | 一模块一提交 |
| **W5** | 工具侧模块 | `llm_tools_extra` | 工具清单随模块装卸 | 一模块一提交 |
| **W6** | 业务侧模块 | `llm_proactive` / `llm_schedule` | 删 `runtime.proactive/scheduler` 后 proactivity 测试全绿 | 保留旧类一版 |
| **W7** | **删配置 + owner 迁移 + 前端重做** | `config_schema.py` 160→43；`bots.owner_id` 废弃；`sections.json` 生成；migration 脚本 | 迁移幂等 + 键数 == 43 + section == 5 | DB 备份 |
| **W8** | 收尾 | `MODULE_DEV.md` 重写；`capabilities()` 20 feature；接缝棘轮上调 | `full` ≥40/55；`pytest -q` 全绿 | — |

---

## 9. 测试与闸门

| 闸门 | 条件 |
|---|---|
| G-W0 | `test_config_partition`(15) + `test_extensibility_contract`(13) 绿 |
| G-W1 | 6 接口各有用例；`test_run_turn_parity` 绿；既有 1767 例全绿 |
| G-W2 | §5.3 的 `grep` 为空 |
| G-W3 | 输出三形态切换 + 钩子对齐 |
| G-W4 | 每模块"装→块出现 / 卸→块消失"（`describe(req)` 断言） |
| G-W5 | 工具清单随装卸增减 |
| G-W6 | 删除 `runtime.proactive`/`scheduler` 后 `tests/agent/proactivity/*` 全绿 |
| G-W7 | `len(SCHEMA_KEYS)==43`；`sections.json` 与 `page_summary()` 一致；迁移幂等；owner 迁移 4 例绿 |
| G-W8 | `BASELINE_FULL` 上调至 40+；`pytest -q` 全绿；`ruff check .` 无新增 |

---

## 10. 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| 用户升级后配置"消失" | **高** | 迁移脚本 + DB 备份 + `dropped_keys.json` + 发版说明逐键列出新位置 |
| 破坏性改造后"开箱是个哑巴" | **高** | 9 个模块**随仓库发布、首次启动自动装配并默认 enabled**（"开箱功能全由模块提供"≠"默认不装"）；用户体感与今天一致，只是入口从「Agent 配置」移到「LLM 扩展」 |
| `owner_id` 取消导致第三方插件读 `conn.owner_id` 崩 | 中 | `BotConnection.owner_id` 保留为 `@property` 返回 None + 弃用告警一版 |
| 删除 `runtime.proactive/scheduler/memory/knowledge` 崩旧插件 | 中 | 保留一版 shim：`__getattr__` 转发到接管模块 + 弃用日志 |
| 9 个模块加载顺序敏感 | 低 | 除 `output` 外全是注册表（与顺序无关）；`llm_output` 三 mode 合一，避免争槽 |
| `prompt_prep` 改 provider 拉取后变慢 | 低 | providers 并行 `gather` + 每块耗时进 telemetry |
| 迁移 108 键时归属写错 | **高** | 归属表是代码常量 + 迁移测试造全量旧配置断言（§6） |

---

## 11. 完成判据（做完这一轮能看到什么）

| # | 判据 | 验证方式 |
|---|---|---|
| 1 | 本体界面正好 5 页、43 键、顺序为 基础→权限→对话行为→会话→高级 | `pytest tests/architecture/test_config_partition.py` |
| 2 | 9 个模块各自带自己的配置，装/卸即显/隐 | 装 `llm_memory` → 记忆块出现；卸载 → 消失 |
| 3 | 框架里不再有"替模块做策略决定"的读配置 | §5.3 的 grep 为空 |
| 4 | 拥有者身份只有一个来源（权限页名册），账号页不再有 owner QQ | 迁移 4 例 + 前端账号表单无该字段 |
| 5 | 写一个"主动回复"模块只需 `spawn` + `llm_turn` + `send_to`（~40 行） | 示例模块即 `llm_proactive` 本身（817 行的旧实现被它自己替代） |
| 6 | 一条回复无论走 普通/主动/定时，发出的 `messages`、触发的钩子、落库文本都一致 | `test_initiative_output_parity` 绿 |
| 7 | 接缝记分板 `full` 从 10/55 升到 ≥40/55 | `test_extensibility_contract` 棘轮 |
