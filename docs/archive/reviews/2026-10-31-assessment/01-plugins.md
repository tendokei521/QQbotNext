# 01 · 插件侧底账：`app/plugins/llm_*` 九个插件

> 盘点员：plugin-inventory　｜　范围：`app/plugins/llm_assembly` `llm_output` `llm_message_meta` `llm_context` `llm_memory` `llm_proactive` `llm_schedule` `llm_tools_extra` `llm_enhance` + `app/plugins/README.md` + `app/agent/config_partition.py`
> 只读盘点，未改动任何 `.py`。所有判定都来自"键名的真实读取点"，**不采信 `config_schema` 的 description**。

## 0. 判定口径（怎么算"生效"）

1. 用键名全仓 `grep`，把**只出现在** `config_schema` / `default_config` / `config_partition` / `config.py` 默认值 / 测试 / 文档 / 日志里的命中全部剔除；剩下能追溯到执行路径的，才算"真实读取点"。
2. 读取点还要分清它读的是**哪一个 config 对象**，这是本仓库最大的坑：
   - `runtime.<x>_options`：模块在 `on_load` 里挂的 `ConfigView`（模块值优先、agent 兜底）→ 这才是"模块开关生效"的通道；
   - `runtime.config`：`AgentConfig`。**它对模块键只返回 `DEFAULT_LLM_CONFIG` 里的硬编码默认值**（`app/agent/config.py:366-378`），且 WebUI 写 agent 配置时模块键会被 400 拒绝（`app/webui/api/agent.py:175-191`）→ 直接读 `runtime.config` 的模块键 = 用户怎么改都不生效；
   - `policy_config(runtime, kind)` / `policy_get(...)`：取某一槽的视图（`app/agent/policy.py:98-124`）；
   - `policy_view(runtime)`：把所有已接管视图**串成链**再兜底 agent 配置（`app/agent/policy.py:34-85`），顺序 = output → assembly → meta → context → memory → knowledge → tools → proactive → schedule（`policy.py:36-46`）。**同一键被两个模块持有时，靠前那个赢**。
3. 判定标签：✅生效 ／ ⚠️条件生效（要别的开关配合）／ 🟡仅兼容迁移 ／ 🔴无人读取（僵尸） ／ 🚫被覆写或读错来源。
4. 每个键在 `config_schema` 里的 **description 原文**集中列在**附录 A**（便于对照"界面怎么说的 vs 实际怎么做的"）。它是被审查对象，**不是证据**。

## 1. 一页结论（先看这六条）

1. **9 个插件 schema 共 109 个键**（明细见 §4），其中**明确"开关不按预期工作"的 12 个**：僵尸 3、被旁路 2、选项词表失配 4、读错来源 1、条件不可达 2。另有 2 个"仅兼容"（`speech_via_tool`、`stream_output`）。
   - 🔴 僵尸（全仓 0 读取）：`stream_send_by_sentence`、`stream_keep_order`（`llm_output`）、`meta_mask_nickname`（`llm_message_meta`，本插件 0 读取）。
   - 🚫 被旁路：`user_message_format`、`prompt_time_position`（`llm_assembly`）——开关在组装模块，**真实渲染读 `runtime.config`**（`app/plugins/llm_message_meta/src/service.py:82,95-96`），模块里选什么都不影响本轮 user 文本。
   - 词表失配：`meta_sender_style`、`meta_sent_style`、`meta_instruction_mode`、`stream_send_curve` 的 `linear`。
2. **提示词组装事实上横跨 3 个插件**：`llm_assembly`（行为规则/参与度/表情/请求落盘）+ `llm_message_meta`（本轮消息渲染 14 键）+ `llm_proactive` 的 5 个 `proactive_*_prompt*`（其实全是 `app/agent/assembly/prompt.py` 的块开关）。同一段 user 文本被两个插件用两套词表、两个 config 源各自表达一次。
3. **"3 条件耦合"让一大片开关看着有、实际沉默**：`llm_output` 的 9 个发送节奏键只在 `mode != text` **且** `stream_send_pool_enabled=True` 时才被读到（池只在 `app/agent/delivery/output.py:156-177` 创建）；`speech_*` 4 键还要 `mode=speech` **且**模型支持 tool_use（`app/agent/loop/assistant_output.py:47-73`）。默认 `pool_enabled=False`，所以默认状态下这 9 个键全是装饰。
4. **`mode` 是唯一起作用的输出开关，但它不在归属表里**：`llm_output` 的 `OWNED_KEYS`（`main.py:35-60`）和 `MODULE_KEYS["llm_output"]`（`config_partition.py:224-249`）都只有旧键 `speech_via_tool`，没有 `mode`；反过来 schema/默认值里有 `mode`（`config_schema.py:4-10`、`main.py:77`）。已有评审记录同一漂移：`docs/reviews/2026-10-30-builtin-modules.md:150`。
5. **`provides`/`supersedes` 声明是装饰性的**：`llm_output`/`llm_assembly` 声明接管 `output`/`assembly` 槽，但 FeatureRegistry 接管要求 owner 自身具备 `respond()`/`build()`（`app/plugin/features/controllers.py:261`、`:221`），`Module` 类没有这两个方法 → 接管被**拒绝并只打 warning**（`controllers.py:183-188`），真正生效的是 `on_load` 里手工赋值。后果：WebUI 的"功能接管"状态与实际不符，释放路径靠各模块自己的 `on_unload` 兜底。
6. **默认值在迁移中被悄悄改掉**（不是"迁移零变化"）：`speech_max_bubbles` 4→5、`speech_max_bubble_chars` 300→200、`speech_reminder_rounds` 3→2、`emoji_react_level` 1→0、`include_private_qq` True→False、`meta_mask_nickname` True→False、`stream_send_interval_base_ms` 600→0、`min` 100→0、`max` 3000→0、`curve` sqrt→none、`curve_k` 200→1.0、`short_delay` 1200→300、`long_delay` 400→1200、`memory_recall_max_chars` 600→1200、`memory_max_per_owner` 300→200、`memory_max_age_days` 180→0、`onebot_tools_enable` **False→True（安全相关）**。

## 2. 总表：九个插件

| 插件 | 显示名（module.json） | provides / supersedes | schema 键数 | 实际持有（OWNED_KEYS） | 挂的钩子 / 接缝 | 关闭或卸载后具体缺什么 |
|---|---|---|---|---|---|---|
| `llm_output` | 流式输出 | `provides=("output",)` / `supersedes=("output",)`（`main.py:72-73`） | 24 | 24（含 `speech_via_tool`，**不含 `mode`**，`main.py:35-60`） | 无 LLM 钩子；`on_load` 写 `runtime.output` + `runtime.output_options` + 声明输出能力（`main.py:105-132`），`on_unload` 还原（`:134-149`） | `runtime.output=None` → 框架按 agent `stream_output=False` 选 `BlockingOutput`（`app/agent/delivery/output.py:241-253`），永远"一次性文本"，流式/发言通道全部消失。**注意**：`speech_via_tool` 不再被 `OutputOptionsView` 覆写 → 回落 agent 默认 `True`（`app/agent/config.py:51`），提示词层仍按"文本=思考"建发言通道（`app/agent/loop/prompt_prep.py:247`），与实际 `BlockingOutput` 冲突（`:263-271` 的告警只在 `speech is None` 时才会打，这里不打）。 |
| `llm_assembly` | 组装策略 | `provides=("assembly",)` / `supersedes=("assembly",)`（`main.py:46-47`） | 7 | 7（`main.py:27-35`） | 无 LLM 钩子；`on_load` 把 `AssemblyAdapter` 装进 `runtime.assembler`，并把模块配置视图挂 `runtime.assembly_options`（`main.py:59-66`）；`on_unload` 还原 `PromptAssembler`（`:68-77`） | 装配器回到框架默认，`req.config` 不再叠本模块 7 键 → 行为规则覆盖、参与度、表情活跃度、`prompt_dump_*` 全部回落到 agent 默认（规则覆盖为空、参与度 medium、表情 1、落盘关）。 |
| `llm_message_meta` | 消息元信息 | 无槽（`main.py:9-12` 明确"不需要新槽"） | 14 | 14（`main.py:28-43`） | `pre_request` ×2：order -100 `collect_user_context`、order 20 `format_user_context`（`main.py:80-86`）；写 `runtime.meta_options`（`:78`） | 本轮 user 文本退回"裸正文"：没有时间行/发送者/群号/@/引用；历史清洗开关回落 agent 默认 True（`app/agent/loop/assistant_output.py:36`）。**不会报错**，只是模型突然"看不见是谁说的"。 |
| `llm_context` | 上下文增强 | 无槽；声明工具能力 `context_expand`（`main.py:85`） | 10 | 10（`main.py:31-42`） | `context_provider` order 40（指代块）、`tool_source` order 40（`expand_*` 工具）、`pre_request` order -50（焦点衰减）（`main.py:86-95`）；写 `runtime.context_options`（`:78-80`） | 无指代消解/焦点行/预取；`expand_recent`/`expand_message`/`expand_user` 三个工具消失；群聊"未展开"标记的渲染标志回落（`app/agent/loop/prompt_prep.py:439`）。工具前置判定会退化为读 agent 的 `context_expand_enable=True`（`app/agent/tools/policy.py:79-86`），即"配置说能用、工具源却没了"——静默。 |
| `llm_memory` | 长期记忆 | 无槽（接缝：context_provider / tool_source / 两个钩子） | 23（`src/schemas.py:10-188`，无 `config_schema.py`） | 23（`main.py:31` 由 schema 派生） | `context_provider`（记忆块）、`tool_source`（memory_* 工具）、`pre_request` order 60、`turn_completed` order 60（`main.py:61-71`）；`runtime.memory_service`（`:60`）、`runtime.memory_options`（`:56-58`） | 无回忆/无写入/无画像；`memory_*` 五个工具被前置判定过滤（`app/agent/tools/policy.py:74-78` 读 `runtime.memory_service.enabled()`，为 None 时回落 `memory_enable=False`）；`/memory` 全部子命令失效。 |
| `llm_proactive` | 主动消息 | 无槽（接缝：两个自定义钩子 + 生命周期） | 17 | 17（`main.py:34-52`） | `message_observed` order 80、`bot_sent` order 80（`main.py:102-109`）；`on_online` 启动计时器 / `on_offline` 停止（`:111-119`）；`runtime.proactive_service`（`:100`）、`runtime.proactive_options`（`:95-97`） | 主动消息完全不发；`proactive_prompt*` 5 键的提示块内容仍会被 `app/agent/assembly/prompt.py:227-282` 读到，但只能读到 agent 默认（视图没了），实际等于"一直开着/一直默认"。 |
| `llm_schedule` | 定时任务 | 无槽（接缝：tool_source + 生命周期） | 3 | 3（`main.py:25-29`） | `tool_source` order 70（`schedule` 工具）；`on_online` 启动 / `on_offline` 停止（`main.py:69-77`）；`runtime.scheduler`（`:67`）、`runtime.schedule_options`（`:58-60`） | 不能建定时任务、已存任务不再触发；`schedule_task` 工具消失（工具源未注册）。 |
| `llm_tools_extra` | 工具扩展 | 无槽 | 5（另有 5 键"有意不在插件页渲染"，`config_schema.py:4-5`） | 10（`main.py:27-38`） | `tool_source` ×2：OneBot order 60、MCP order 65（`main.py:76-81`）；`runtime.tools_options`（`:73-75`）、`runtime.mcp_service`（`:80`） | OneBot 工具与 MCP 工具全部消失；工具结果末尾的"回应要求"不再追加（回落 agent `tool_result_directive_enable`，仍为 True，`app/agent/tools/spec.py:402-407`）。 |
| `llm_enhance` | LLM调试 | 无槽 | 1 | **未声明 `OWNED_KEYS`**（全仓无该符号） | `@llm_hook("pre_request", order=30)`（`main.py:30-41`），只置 `ctx.state["debug_prompt"]`；真正打印在 `app/agent/loop/turn_metrics.py:326-348` | 不再打印本轮完整 prompt。注意它的唯一键 `debug_prompt` 在归属表里被列为**已删除**（`config_partition.py:407`）。 |

## 3. 三份名单对不上（这就是"每个键是谁的"说不清的直接原因）

三处各自声明"谁持有哪些键"，互不一致：

| 名单 | 位置 | 内容 |
|---|---|---|
| 归属表 | `config_partition.py:222-358` | 9 个模块名（含 **`llm_knowledge`**，但 `app/plugins/` 下没有该目录） |
| 插件自述 | 各 `main.py::OWNED_KEYS` | 8 个插件有；`llm_enhance` 没有 |
| 表单 | 各 `config_schema.py` / `src/schemas.py` | 9 个插件共 109 键 |

**逐项差异：**

| 差异 | 键 | 证据 | 后果 |
|---|---|---|---|
| schema 有、归属表没有 | `mode` | `llm_output/config_schema.py:4-10`、`main.py:77` vs `config_partition.py:224-249` | `mode` 是全模块唯一真开关，却不在"键的命运"表里；`duplicate/stale` 校验（`config_partition.py:472-487`）看不见它；测试还把差异固化（`tests/plugins/test_llm_output.py:5` 断言 OWNED_KEYS == MODULE_KEYS） |
| 归属表有、schema 没有 | `speech_via_tool` | `config_partition.py:244`、`main.py:55,97`；`config_schema.py:137-139` 注释明说"不再出现在设置页" | 用户改不到，只在老配置迁移时参与推导（`src/output_mode.py:51-56`） |
| 归属表有、schema 故意隐藏 | `onebot_tools_enable/allowed/denied/debug/overrides` | `llm_tools_extra/config_schema.py:4-5`（注释称"见 `SCHEMA_HIDDEN_KEYS`"，**该符号全仓不存在**，只在测试里本地定义：`tests/plugins/test_llm_tools_extra.py:21`） | 键在"高级工具 → OneBot 工具"页改（`app/webui/dashboard/src/views/OnebotToolsPage.vue:119-136`），插件页看不到、文档指错符号 |
| 归属表有、没有持有者 | `knowledge_enable` / `knowledge_embedding_model_id` / `knowledge_recall_limit` | `config_partition.py:306-310`；`app/plugins/` 无 `llm_knowledge` | WebUI 写 agent 会被拒（模块键）、又没有模块可写 → 知识库工具**永久不可用**（`app/agent/tools/policy.py:47-49` 读到 agent 默认 False） |
| 插件在用、归属表判死 | `debug_prompt` | `llm_enhance/main.py:26,38` vs `config_partition.py:404-409`（`DELETED_KEYS`） | 迁移脚本会把用户旧值当"废弃"删掉并只记 json（`scripts/migrate_v3_split_config.py:70-71,79-80`），模块侧没有接值 → 升级后调试开关静默回默认 False |
| 键数口径 | 归属表实际 **111** 键 | 逐模块相加（24+10+14+23+3+10+17+3+7），而 `config_partition.py:221` 写"107 键"、`scripts/migrate_v3_split_config.py:1` 写"108 个键" | 文档漂移，验收线（`TARGET_CORE_KEY_COUNT`）无法据以核对 |

## 4. 逐插件全键清单（109 键）

> 列"真实读取点"只写执行路径。`ConfigView` 的含义：模块值优先、缺省回落 agent 配置（`app/agent/delivery/output.py:256-278`）。

### 4.1 `llm_output`（24 键，`config_schema.py` L4-L163；默认值 `main.py:74-102`）

| 键 | 类型 | 默认 | 真实读取点 | 判定 |
|---|---|---|---|---|
| `mode` | select(text/stream/speech) | `speech` | `src/output_mode.py:38-62`（唯一事实源）→ `service.py:43,73-79` 选实现 | ✅生效；**不在 OWNED_KEYS/归属表** |
| `stream_output` | boolean | `False` | `app/agent/loop/turn.py:307`（链视图，决定模块内轮次默认 stream）；`app/agent/capabilities.py:81-83`（无人声明时兜底） | 🟡仅兼容：`mode` 是事实源；且 `mode=speech/text` 时它被 `OutputOptionsView` 语义覆盖（`src/output_mode.py:65-76`） |
| `stream_send_by_sentence` | boolean | `True` | **无**（仅 schema `:17`、默认值 `main.py:38,80`、`config.py:78`、归属表 `:227`、旧 schema `config_schema.py:59`） | 🔴僵尸 |
| `stream_sentence_max_length` | number | `200` | `app/agent/loop/chat.py:270`、`turn.py:533`、`llm_proactive/src/stream.py:103` | ✅ |
| `stream_keep_order` | boolean | `True` | **无**（同上，仅 `:29`、`main.py:54,96`、`config.py:93`、`:243`） | 🔴僵尸 |
| `stream_send_pool_enabled` | boolean | `False` | `app/agent/delivery/output.py:130-138` | ⚠️需 `mode≠text`（`BlockingOutput` 不建池） |
| `stream_send_interval_mode` | select(none/fixed/length_curve) | `none` | `app/domain/pace.py:15-16` | ⚠️需池 |
| `stream_send_interval_base_ms` | number | `0`（旧 `600`） | `pace.py:21` | ⚠️需池；默认 0 = 等于不等待 |
| `stream_send_interval_min_ms` | number | `0`（旧 `100`） | `pace.py:43` | ⚠️需池 |
| `stream_send_interval_max_ms` | number | `0`（旧 `3000`） | `pace.py:44` | ⚠️需池 |
| `stream_send_curve` | select(none/sqrt/linear/short_long) | `none`（旧 `sqrt`） | `pace.py:22,28-41`（分支只认 `sqrt/log/inverse/short_long`，`none`/`linear` 都落到 else=sqrt） | 🚫`linear` 无实现；`none` 也不是"无曲线" |
| `stream_send_curve_k` | number | `1.0`（旧 `200`） | `pace.py:23` | ⚠️需池 |
| `stream_short_message_length` | number | `10` | `pace.py:35` | ⚠️需池 |
| `stream_short_message_delay_ms` | number | `300`（旧 `1200`） | `pace.py:37` | ⚠️需池 |
| `stream_long_message_delay_ms` | number | `1200`（旧 `400`） | `pace.py:39`（**默认值相对旧库反转**） | ⚠️需池 |
| `stream_send_prefix` | string | `""` | `app/agent/delivery/pool.py:118` | ⚠️需池 |
| `stream_send_suffix` | string | `""` | `pool.py:119` | ⚠️需池 |
| `stream_send_max_queue` | number | `20` | `pool.py:38` | ⚠️需池 |
| `stream_queue_full_policy` | select(backpressure/drop_newest/drop_oldest) | `backpressure` | `pool.py:55-68` | ⚠️需池 |
| `stream_flush_on_finish` | boolean | `False` | `pool.py:70-76` | ⚠️需池 |
| `speech_max_bubbles` | number | `5`（旧 `4`） | `app/agent/loop/assistant_output.py:59`（经 `prompt_prep.py:247` 的链视图拿到模块值） | ⚠️需 `mode=speech` + 模型支持 tool_use |
| `speech_max_bubble_chars` | number | `200`（旧 `300`） | `assistant_output.py:61` | ⚠️同上 |
| `speech_reminder_rounds` | number | `2`（旧 `3`） | `app/agent/loop/chat.py:281` | ⚠️同上 |
| `speech_force_reply_on_call` | boolean | `True` | `assistant_output.py:91` | ⚠️同上 |
| （隐藏，非 schema）`speech_via_tool` | boolean | `True` | `app/agent/delivery/speech.py:314-329`、`assistant_output.py:56`、`capabilities.py:81`、`app/agent/persona/rules.py:109-112`、`app/agent/assembly/assembler.py:470-476` | 🟡仅兼容；且被 `OutputOptionsView.get` 动态覆写为 `mode=="speech"`（`src/output_mode.py:73-75`） |

**补充事实**：`resolve_mode` 先读 `stored_config()`（用户真的存过的键，`app/plugin/base.py:55-62`）；而 `ModuleConfig.set/update` 会把 `raw_config`（含全部默认值）整份落库（`app/plugin/base.py:64-78`）→ **用户只要在插件页点过一次保存，`mode` 就被显式写死**，`speech_via_tool`/`stream_output` 的迁移推导分支（`src/output_mode.py:50-56`）从此永久走不到。

### 4.2 `llm_assembly`（7 键，`config_schema.py` L4-L52；默认值 `main.py:48-56`）

| 键 | 类型 | 默认 | 真实读取点 | 判定 |
|---|---|---|---|---|
| `behavior_rules_override` | textarea | `""` | `app/agent/assembly/builtin_sections.py:150-171`（`req.cfg`；`req.config` 被 `service.py:33-42` 换成模块视图） | ✅ |
| `participation` | select(low/normal/high) | `normal`（旧 `medium`） | `app/agent/persona/rules.py:116` → `rules.py:270-283`（只区分 `low`/`high`，其它一律"普通"） | ✅但词表双轨（`normal` 与 `medium` 同义） |
| `emoji_react_level` | number 0-3 | `0`（旧 `1`） | `builtin_sections.py:189`、`app/agent/persona/rules.py:118→370-380`（索引 0-3） | ✅默认值漂移（0=几乎不贴） |
| `user_message_format` | select(default/single_line/labeled) | `default` | 组装侧：`app/agent/assembly/prompt.py:161-164`（只认 `legacy`，其余=single_line）+ `builtin_sections.py:245`（决定"格式说明"文案）；**渲染侧**：`llm_message_meta/src/service.py:95` 读 `runtime.config` | 🚫被旁路：三个选项都等于"单行"，选不到 legacy；真正渲染本轮文本的那段代码读的是 agent 配置（agent 默认 `single_line`，`config.py:46`），模块值不参与 |
| `prompt_time_position` | select(user_head/user_tail/off) | `user_head` | `prompt.py:167-170`（只认 `head`，其余=tail）；`llm_message_meta/src/service.py:96,110,145` 读 `runtime.config` | 🚫三个选项**全部**=tail；`off` 不注入也无效。想关时间只能用 meta 的 `include_time`（见 §5-2） |
| `prompt_dump_enable` | boolean | `False` | `app/agent/assembly/dump.py:45`（config 来自 `dump.py:129-130` 的 `runtime.assembly_options`） | ✅ |
| `prompt_dump_max_entries` | number | `20` | `dump.py:51` | ✅ |

### 4.3 `llm_message_meta`（14 键，`config_schema.py` L4-L90；默认值 `main.py:54-69`）

读取点集中在 `app/plugins/llm_message_meta/src/service.py`，其 `_ctx_cfg` 经 `policy_config(runtime,"meta")` 取模块视图（`service.py:17-33`）。**但第 82 行另走一路**：`cfg = getattr(runtime,"config")`，用于格式判定。

| 键 | 类型 | 默认 | 真实读取点 | 判定 |
|---|---|---|---|---|
| `include_time` | boolean | `True` | `service.py:108-111`（位置由 `prompt_time_position` 决定，恒 tail 见上）；`:145-146` 追加末尾 | ✅这是**唯一**能关时间行的开关 |
| `include_private_qq` | boolean | `False`（旧 `True`） | `service.py:114-117` | ✅默认值反转：新装用户私聊不再注入 `(QQ: …)` |
| `include_sender` | boolean | `True` | `service.py:49-51`（收集）、`:127-134`（渲染） | ✅ |
| `include_mentioned` | boolean | `True` | `service.py:53-54`、`:135-137` | ✅ |
| `include_quote` | boolean | `True` | `service.py:56-60`、`:139-144` | ✅ |
| `include_quote_sender` | boolean | `True` | `service.py:140` | ✅ |
| `include_sent` | boolean | `True` | `service.py:149-150` | ✅ |
| `fetch_at_nickname` | boolean | `True` | 本插件：`service.py:190-194`；**框架另一条出口**：`app/agent/loop/tool_source.py:36-38`（config=`policy_config(runtime,"context")`，此键不归 context → 落 agent 默认 True）、`app/agent/tools/session_tools.py:126` | ⚠️半生效：管得住本轮 @ 反查，管不住 `get_chat_history` 与背景块的 @ 展开 |
| `fetch_quote_content` | boolean | `True` | `service.py:233` | ✅ |
| `meta_sender_style` | select(compact/verbose/name_only) | `compact` | `service.py:91` → `:129-134`（只认 `single` / `new`，其余=legacy `发送者：`）；且 `:95-98` 在 `single_line` 时**强制改写成 `single`**，而 `single_line` 恒成立 | 🚫被覆写：三个选项都不在代码词表，实际恒等于"单行内联昵称" |
| `meta_sent_style` | select(compact/quoted/fenced) | `compact` | `service.py:92` → `app/agent/session/model.py:580-602`（只认 `new`，其余=`发送了：`） | 🚫词表失配：三个选项全部等于旧格式 |
| `meta_instruction_mode` | select(auto/always/off) | `auto` | `app/agent/assembly/builtin_sections.py:239-257`：`off`→不注入；`new`→脱敏版；`auto`/`always` 都落 legacy 分支 | ⚠️只有 `off` 与 `new` 有效，`auto`/`always` 等价 |
| `meta_mask_nickname` | boolean | `False`（旧 `True`） | **本插件 0 读取**；读取点 `llm_proactive/src/manager.py:206`、`llm_schedule/src/scheduler.py:470`（都在读**自己模块**的配置，模块默认里没有此键 → False）；主聊天历史渲染恒 `True`（`app/agent/loop/turn_metrics.py:383-391` 硬编码） | 🔴僵尸（+默认值反转） |
| `clean_output_parentheses` | boolean | `True` | `app/agent/loop/assistant_output.py:36`（调用点 `:126`、`chat.py:600`、`turn.py:688`）、`pipeline.py:732-736`（均经 `policy_config(runtime,"meta")`） | ✅ |

### 4.4 `llm_context`（10 键，`config_schema.py` L4-L68；默认值 `main.py:53-64`）

| 键 | 类型 | 默认 | 真实读取点 | 判定 |
|---|---|---|---|---|
| `context_expand_enable` | boolean | `True` | 模块侧 `llm_context/src/service.py:86-89`（决定是否注册 `expand_*` 工具）；框架侧 `app/agent/loop/prompt_prep.py:386-390`+`tool_source.py:36-38`、`session_tools.py:127`、`app/agent/tools/policy.py:79-86`（能力优先）；**另有两处读错来源**：`llm_proactive/src/manager.py:214`、`llm_schedule/src/scheduler.py:478` | ✅生效，但同一概念有 6 个读取点、2 个读的是自己模块配置 |
| `referent_resolve_enable` | boolean | `True` | `llm_context/src/referent.py:180-185,221`；`app/agent/assembly/prompt.py:263` | ✅ |
| `referent_prefetch_enable` | boolean | `True` | `referent.py:253-259` | ✅ |
| `referent_ambiguous_policy` | select(fetch_all/focus_first/ask) | `fetch_all` | `referent.py:261,266-270` | ✅ |
| `referent_prefetch_max` | number | `2` | `referent.py:242,262` | ✅ |
| `referent_focus_ttl` | number | `1800` | `referent.py:226` | ✅ |
| `referent_focus_max` | number | `12` | `referent.py:227` | ✅ |
| `referent_prompt_enable` | boolean | `True` | `app/agent/assembly/prompt.py:262-263` | ✅ |
| `history_auto_qq_min_local` | number | `4` | `app/agent/tools/session_tools.py:374` | ✅ |
| `history_cross_query_enable` | boolean | `True` | `app/agent/tools/session_tools.py:305-306` | ✅ |
| （隐藏，非 schema）`referent_prefetch_item_chars` | number | — | `referent.py:281`（不在 schema/OWNED/归属表任何一处；经 context 视图取不到 → `0`） | ⚠️孤儿键：代码里有、配置里没有 |

### 4.5 `llm_memory`（23 键，`src/schemas.py` L10-L188；`default_config` 由 schema 派生 `main.py:43`）

读取点集中在 `src/manager.py`（`_get` → `policy_get(runtime,"memory",…)`，`manager.py:119-135`）与 `src/consolidate.py`。

| 键 | 类型 | 默认（旧 agent 默认） | 真实读取点 | 判定 |
|---|---|---|---|---|
| `memory_enable` | boolean | False(False) | `manager.py:141-142`；`main.py:114-115`；工具前置 `app/agent/tools/policy.py:74-78`；`src/tool.py:119`；`src/commands.py:139` | ✅唯一总开关 |
| `memory_private_enable` | boolean | True(True) | `manager.py:144-150` | ✅ |
| `memory_group_enable` | boolean | True(True) | `manager.py:144-150` | ✅ |
| `memory_profile_enable` | boolean | True(—) | `manager.py:202` | ✅ |
| `memory_recall_mode` | select(hybrid/keyword) | hybrid(—) | `manager.py:158-160` | ✅ |
| `memory_embedding_model_id` | resource | ""(—) | `manager.py:163-174` | ✅ |
| `memory_recall_max` | number | 8(8) | `manager.py:273` | ✅ |
| `memory_recall_max_chars` | number | 1200(**600**) | `manager.py:275` | ✅默认值翻倍 |
| `memory_profile_max_chars` | number | 800(—) | `manager.py:228` | ✅ |
| `memory_save_deterministic` | boolean | True(True) | `manager.py:465` | ✅ |
| `memory_extract_enable` | boolean | True(True) | `consolidate.py:110`；`manager.py:595` | ✅ |
| `memory_extract_interval_min` | number | 10(10) | `consolidate.py:114` | ✅ |
| `memory_consolidate_batch` | number | 20(—) | `consolidate.py:216`；`manager.py:535` | ✅ |
| `memory_consolidate_min_chars` | number | 30(—) | `consolidate.py:238` | ✅ |
| `memory_max_per_owner` | number | 200(**300**) | `manager.py:441` | ✅默认值收紧 |
| `memory_user_cross_group` | boolean | False(False) | `manager.py`（工具路径）`src/tool.py:138` | ✅ |
| `memory_audit_enable` | boolean | True(True) | `manager.py:339,442,743,750` | ✅ |
| `memory_audit_inject` | boolean | False(False) | `manager.py:341` | ✅ |
| `memory_min_confidence` | number | 0.5(0.5) | `manager.py:251` | ✅ |
| `memory_inject_hedge` | boolean | True(True) | `manager.py:255` | ✅ |
| `memory_max_age_days` | number | 0(**180**) | `manager.py:252` | ✅**语义反转**：0=不限，旧默认 180=半年过期 |
| `memory_on_reset` | select(suspend/clear/keep) | suspend(suspend) | `manager.py:719` | ✅ |
| `memory_upgrade_saved_only` | boolean | True(True) | `manager.py:254` | ✅ |

> 结论：记忆插件是全仓**最干净**的一个——23 键全部有真实读取点，且都走 `policy_get(runtime,"memory")`。问题只在默认值漂移（`recall_max_chars`/`max_per_owner`/`max_age_days`）。

### 4.6 `llm_proactive`（17 键，`config_schema.py` L4-L105；默认值 `main.py:63-81`）

| 键 | 类型 | 默认 | 真实读取点 | 判定 |
|---|---|---|---|---|
| `proactive_friend_enable` | boolean | False | `src/manager.py:88-96`（`_cfg` 直接读模块配置） | ✅ |
| `proactive_group_enable` | boolean | False | `manager.py:88-96` | ✅ |
| `proactive_friend_sessions` | string_list | [] | `manager.py:94,487` | ✅ |
| `proactive_group_sessions` | string_list | [] | `manager.py:94,488` | ✅ |
| `proactive_min_interval_minutes` | number | 30 | `manager.py:138-140` | ✅ |
| `proactive_max_interval_minutes` | number | 900 | `manager.py:139` | ✅ |
| `proactive_max_unanswered` | number | 3 | `manager.py:186-188` | ✅ |
| `proactive_quiet_hours_start` | number | 1 | `manager.py:99-106` | ✅ |
| `proactive_quiet_hours_end` | number | 7 | `manager.py:99-106` | ✅ |
| `proactive_group_idle_minutes` | number | 10 | `manager.py:153` | ✅ |
| `proactive_prompt` | textarea | ""（旧=以内置模板原文为默认） | `manager.py:197-198` | ✅默认值语义变了：旧默认是完整模板文本，新默认空串再靠 `DEFAULT_PROACTIVE_PROMPT` 兜底 |
| `proactive_prompt_enable` | boolean | True | `app/agent/assembly/prompt.py:227`（经链视图 → proactive_options） | ✅（属"装配"概念，却归主动消息） |
| `proactive_history_intent_nudge` | boolean | True | `prompt.py:243,252` | ✅同上 |
| `proactive_env_prompt_enable` | boolean | True | `prompt.py:257` | ✅同上 |
| `proactive_unresolved_prompt_enable` | boolean | True | `prompt.py:258` | ✅同上 |
| `proactive_env_intent_nudge` | boolean | True | `prompt.py:282` | ✅同上 |
| `stream_proactive_enabled` | boolean | False | `src/manager.py:322`：`config.get("stream_output") and config.get("stream_proactive_enabled")`，其中 `config = self.module.config`（`:238`）= 只含 proactive 键的门面视图 | 🚫条件不可达：`stream_output` 属 `llm_output`，写 agent 被 400 拒（`webui/api/agent.py:175-191`）→ 恒 False。**打开本开关不会让主动消息走流式** |

**同插件内的跨插件读键（读自己的 config → 落 agent 默认）**：`manager.py:201-217` 读 `include_pre_history`、`experimental_long_term_memory`、`meta_mask_nickname`、`history_rounds`、`fetch_at_nickname`、`context_expand_enable`。

### 4.7 `llm_schedule`（3 键，`config_schema.py` L4-L21；默认值 `main.py:40-44`）

| 键 | 类型 | 默认 | 真实读取点 | 判定 |
|---|---|---|---|---|
| `schedule_enable` | boolean | True | `src/scheduler.py:99`（`self.module.config`）；工具源开关 `service.py:28-31`；另 `app/agent/loop/prompt_prep.py:232-234`（`policy_get(runtime,"schedule",…)`） | ✅ |
| `schedule_prompt` | textarea | ""（旧=内置长模板为默认） | `src/scheduler.py:457-462` | ✅ |
| `stream_scheduled_enabled` | boolean | False | `scheduler.py:291-293`，与 agent `stream_output` 相与（同上一模一样的死条件） | 🚫条件不可达 |

**附带发现（同文件）**：`scheduler.py:336-338`、`:598-600`、`:620-622` 用 `config.get("model"|"temperature"|"max_tokens", 默认值)` 取模型参数，但这三个键已在 v3 删除（`config_partition.py:378-380`，`AgentConfig` 里也不再有默认）→ 定时任务/主动消息的流式请求**恒定使用硬编码 `deepseek-chat` / 0.7 / 1024，绕过 Provider 预设**。同一模式见 `llm_proactive/src/manager.py:332-338`。

### 4.8 `llm_tools_extra`（schema 5 键 / 持有 10 键，`config_schema.py` L8-L38；默认值 `main.py:49-60`）

| 键 | 类型 | 默认 | 真实读取点 | 判定 |
|---|---|---|---|---|
| `onebot_tools_enable` | boolean | **True**（旧 False） | 模块 `src/service.py:47-50`；`src/onebot/security.py:33-36` | ✅但**默认值反转**：装上模块即开放 OneBot 工具 |
| `onebot_tools_allowed` | list | [] | `security.py:37-38,62-64` | ✅（插件页不可见） |
| `onebot_tools_denied` | list | [] | `security.py:36-37,59-60` | ✅（同上） |
| `onebot_tools_max_result` | number | 2000 | `src/onebot/executor.py:146-152`：`getattr(runtime,"config").get("onebot_tools_max_result", …)` —— **读 agent 配置** | 🚫读错来源：这是插件页唯一暴露的 OneBot 数值项，改了不生效（agent 默认恒 2000） |
| `onebot_tools_debug` | boolean | False | `executor.py:121-130`（`policy_get`） | ✅（插件页不可见） |
| `onebot_tool_overrides` | map | {} | `security.py:8-21,44-50` | ✅（插件页不可见） |
| `poke_cooldown_seconds` | number | 20 | `executor.py:64-72`（`policy_get`） | ✅ |
| `mcp_servers` | textarea(JSON) | [] | `src/mcp.py:161-190`（`policy_get(...,"mcp_servers")` + `_decode_servers` 兼容字符串） | ✅ |
| `tool_result_directive_enable` | boolean | True | `app/agent/loop/tool_loop.py:56-69` ← 调用点 `app/agent/tools/spec.py:402-407`（`policy_config(runtime,"tools")`） | ✅ |
| `tool_result_directive` | textarea | "" | 同 `tool_loop.py:63-69` | ✅ |

### 4.9 `llm_enhance`（1 键，`config_schema.py` L6-L16；默认值 `main.py:25-27`）

| 键 | 类型 | 默认 | 真实读取点 | 判定 |
|---|---|---|---|---|
| `debug_prompt` | boolean | False | `main.py:38-39`（`self.config` → 置 `ctx.state["debug_prompt"]`）→ 消费点 `app/agent/loop/turn_metrics.py:326-348`（`_log_debug_prompt`） | ✅生效；但归属表判它"已删除"（`config_partition.py:407`），迁移会丢掉 agent 旧值（见 §3） |

## 5. 重叠矩阵：同一件事被 ≥2 个插件分别表达

| # | 概念 | 来源 A | 来源 B（及更多） | 实际谁赢 |
|---|---|---|---|---|
| 1 | **输出形态**（文本即回复 / 流式 / 发言通道） | `llm_output.mode`（`config_schema.py:4-10`，`src/output_mode.py:38-62` 读取） | ①`llm_output.stream_output`（`turn.py:307`、`capabilities.py:81-83`）；②隐藏键 `llm_output.speech_via_tool`（`speech.py:314-329`，被 `OutputOptionsView` 覆写成 `mode=="speech"`）；③agent 旧键 `stream_output`/`speech_via_tool` 仍在 `DEFAULT_LLM_CONFIG`（`config.py:51,74`）被直接读点当真值用 | `mode`（唯一）；但**关掉 llm_output 后 `speech_via_tool` 回 agent 默认 True**，提示词层与输出层会打架（见 §2 第 1 行"关闭后缺什么"） |
| 2 | **时间行** | `llm_assembly.prompt_time_position`（head/tail/off） | `llm_message_meta.include_time`（显示/隐藏）；`llm_message_meta` 侧位置判定读的是 `runtime.config`（`service.py:96,110,145`） | `include_time` 管"有没有"；位置**恒 tail**（`prompt.py:167-170` 只认 `head`，模块值 `user_head` 不匹配）。"off 不注入"完全无效 |
| 3 | **本轮消息格式** | `llm_assembly.user_message_format`（default/single_line/labeled） | `llm_message_meta.meta_sender_style`（compact/verbose/name_only）、`meta_sent_style`（compact/quoted/fenced） | `user_message_format` 恒 single_line（且模块选不到 legacy）→ 反过来把 `meta_sender_style` 强改成 `single`（`service.py:95-98`）；`meta_sent_style` 恒旧格式。**三个开关同时存在、词表互相不认** |
| 4 | **"元信息不是用户说的话"说明块** | 开关 `llm_message_meta.meta_instruction_mode` / `meta_sender_style`（`config_schema.py:72-78`） | 生成逻辑 `app/agent/assembly/builtin_sections.py:226-262`（在组装路径，且只在 `ctx.state["message_meta_injected"]` 为真时注入） | 双方各握一半：开关在 meta 插件、文案在 assembly 路径；`auto/always` 无效 |
| 5 | **"记录本轮请求"（调试）** | `llm_assembly.prompt_dump_enable` + `prompt_dump_max_entries`（落文件，`assembly/dump.py:45,51`；WebUI `app/webui/api/prompt_dump.py`） | `llm_enhance.debug_prompt`（落日志，`turn_metrics.py:326-348`） | 两个开关、两个输出、两个插件；用户以为是一个"记录 prompt"功能 |
| 6 | **流式发送节奏** | `llm_output` 的 11 个 `stream_send_*` | ①`app/agent/config.py:77-93` 的旧默认（值不同）；②`app/domain/pace.py:21-44` 的代码内 fallback（又一套：600/100/3000/sqrt/200/1200/400）；③`app/agent/config_schema.py:1160-1202` 的"流式预设"三档一次性写多个键 | 三层默认值不一致 + 预设档会整体覆盖；调试时无法判断"这个延迟是哪来的" |
| 7 | **历史渲染的标志位** | `llm_message_meta.fetch_at_nickname` / `meta_mask_nickname` | `llm_context.history_*`；`llm_proactive/src/manager.py:204-217`、`llm_schedule/src/scheduler.py:468-478`（读自己模块配置）；主聊天历史 `turn_metrics.py:383-391` 硬编码 True | 主聊天：硬编码 + meta 视图；主动/定时：读 agent 默认。**同一份历史在三条路径上脱敏/时间口径不同** |
| 8 | **"上下文增强/展开"能力** | `llm_context.context_expand_enable`（10 键之一） | 能力声明 `capabilities.declare_tool(CAP_CONTEXT_EXPAND)`（`llm_context/main.py:83-85`，消费点 `app/agent/tools/policy.py:79-86`） | 两者都算：能力声明优先、配置作偏好；但 `llm_proactive`/`llm_schedule` 又各自读自己 config 的同名键（永远读不到） |
| 9 | **"主动消息走流式"** | `llm_proactive.stream_proactive_enabled`（`manager.py:322`） | `llm_output.mode/stream_output`；agent `stream_output`（`config.py:74`） | 三者相与 → 恒 False（`stream_output` 已归模块且 agent 写不进）。同构问题在 `llm_schedule.stream_scheduled_enabled`（`scheduler.py:293`） |
| 10 | **记忆总开关** | `llm_memory.memory_enable` | agent `experimental_long_term_memory`（`config.py:137`，已列入删除，仍被 3 处读：`builtin_sections.py:255`、`llm_proactive/manager.py:205`、`llm_schedule/scheduler.py:469`、`turn_metrics.py:389`）；`tools/policy.py:74-78` 走 `memory_service.enabled()` | 三套口径并存；`experimental_long_term_memory` 是"删了但还在读"的活键 |
| 11 | **Prompt 段开关归属** | `llm_proactive` 的 5 个 `proactive_*_prompt*`（控制 `app/agent/assembly/prompt.py` 的块内容） | `llm_assembly` 的 `behavior_rules_override`/`participation`/`emoji_react_level`（同类"提示词内容"开关） | 都在给组装器喂料，却分属两个插件、两套命名 |

## 6. 依赖方向（跨插件隐式耦合）

### 6.1 `runtime.<x>_options` 槽的有向边（写 → 读）

| 写方（插件 on_load） | 槽 | 读方（file:line） |
|---|---|---|
| `llm_output`（`main.py:115-116`） | `runtime.output_options` | `app/agent/policy.py:36-52`（链）、`llm_output/service.py:56-60`、`app/agent/loop/turn.py:307`、`prompt_prep.py:247`、`chat.py:281` |
| `llm_assembly`（`main.py:66`） | `runtime.assembly_options` | `app/agent/assembly/assembler.py:722-724`、`app/agent/assembly/dump.py:129-130` |
| `llm_message_meta`（`main.py:78`） | `runtime.meta_options` | `llm_message_meta/src/service.py:22`、`app/agent/loop/assistant_output.py`（经 `chat.py:262`/`turn.py:688`）、`pipeline.py:732` |
| `llm_context`（`main.py:78-80`） | `runtime.context_options` | `llm_context/src/referent.py:185`、`app/agent/tools/session_tools.py:60`、`prompt_prep.py:386-390`、`app/agent/tools/policy.py:79-86`（能力优先） |
| `llm_memory`（`main.py:56-58`） | `runtime.memory_options` + `runtime.memory_service`（`:60`） | `llm_memory/src/manager.py:119-135`、`app/agent/tools/policy.py:74-78`、`app/webui/api/agent.py`（状态页） |
| `llm_tools_extra`（`main.py:73-80`） | `runtime.tools_options` + `runtime.mcp_service` | `llm_tools_extra/src/service.py:49`、`src/onebot/security.py:15,35`、`src/onebot/executor.py:69,124`、`src/mcp.py:185`、`app/agent/tools/spec.py:407` |
| `llm_proactive`（`main.py:95-100`） | `runtime.proactive_options` + `runtime.proactive_service` | `app/agent/assembly/prompt.py`（经链）、`app/webui/api/agent.py:666,684` |
| `llm_schedule`（`main.py:58-67`） | `runtime.schedule_options` + `runtime.scheduler` | `app/agent/loop/prompt_prep.py:232-234` |

**链顺序即优先级**（`policy.py:36-46`）：`output > assembly > meta > context > memory > knowledge > tools > proactive > schedule`。任何两个插件持有同名键，靠前者静默胜出——目前靠"没人重复持有"维持和平，没有校验。

### 6.2 代码级依赖（`app/plugins/README.md:48-49` 明确禁止"插件之间互相 import"）

| 边 | 位置 | 备注 |
|---|---|---|
| `llm_schedule` → `llm_proactive` | `llm_schedule/src/scheduler.py:303`（`from app.plugins.llm_proactive.src.stream import stream_send_initiative`） | 定时任务复用主动消息的流式发送器 |
| `llm_schedule` → `llm_context` | `scheduler.py:308-310`（`focus_only_block`） | 拿焦点行 |
| `llm_proactive` → `llm_context` | `llm_proactive/src/manager.py:259-261`（`focus_only_block`） | 同上 |

→ 卸载 `llm_context` 或 `llm_proactive` 会**直接 ImportError** 打穿另外两个插件的触发路径（`_fire`/`_check_and_chat` 外层有 try/except 记 error，见 `scheduler.py:276-279`），表面是"定时任务突然不发"。

### 6.3 "读错来源"的耦合（模块键被写在别的 config 上读）

| 键 | 归属 | 实际读取处的 config | 位置 |
|---|---|---|---|
| `onebot_tools_max_result` | `llm_tools_extra` | `runtime.config` | `src/onebot/executor.py:148` |
| `user_message_format` / `prompt_time_position` | `llm_assembly` | `runtime.config` | `llm_message_meta/src/service.py:82,95-96` |
| `meta_mask_nickname` / `fetch_at_nickname` / `context_expand_enable` / `include_pre_history` / `history_rounds` | `llm_message_meta` / `llm_context` / 本体 | 各插件自己的模块配置 | `llm_proactive/src/manager.py:201-217`、`llm_schedule/src/scheduler.py:465-478` |
| `stream_output` | `llm_output` | 各插件自己的模块配置 | `llm_proactive/src/manager.py:322`、`llm_schedule/src/scheduler.py:293` |
| `fetch_at_nickname` / `context_expand_enable` | `llm_message_meta` / `llm_context` | `policy_config(runtime,"context")` | `app/agent/loop/tool_source.py:36-38`、`app/agent/tools/session_tools.py:126-127` |

## 7. module.json 与生命周期语义（`on_load` 失败会静默）

| 项 | 事实 | 证据 |
|---|---|---|
| 元数据两份 | `module.json` 只提供 `name/display_name/version/description/entry/api_version`；`provides/supersedes/category/order/default_config/config_schema` 只在类里。版本与显示名以清单为准 | `app/plugin/loader.py:154-158`；9 份 `module.json` 均 9-10 行 |
| 清单缺失 | 第三方缺清单=拒绝加载；内置缺清单=WARN + 类属性兜底 | `loader.py:69-92`、`app/plugin/manifest.py:125-165` |
| `on_load` 时机 | 账号实例装配时调用；**账号被禁用（`authority.enabled=False`）时直接跳过 `on_load`** | `loader.py:339-366`（`:348-354` 跳过分支） |
| `on_load` 异常 | **只 `log.warning`，模块照样"已加载/已启用"**；它的槽没被设置，键全部静默失效 | `loader.py:357-361` |
| `runtime` 取不到 | 9 个插件的 `on_load` 开头都是 `if runtime is None: return`（如 `llm_output/main.py:107-109`、`llm_message_meta/main.py:73-75`）→ **不重试、不留痕**，模块显示启用但从未接管 | 各 `main.py` |
| 启用/禁用联动 | 禁用会调 `on_offline` → `on_unload` → 释放 feature → 取消 `module:<name>:<bot>` 后台任务 → 注销模块定时任务；每步异常都只 warning | `app/webui/api/modules.py:167-241` |
| `provides/supersedes` | 接管需 owner 自身具备 `respond()`/`build()`；`llm_output.Module`/`llm_assembly.Module` 都没有 → 租约被拒，只有一条 warning，真正接管发生在 `on_load` | `app/plugin/features/controllers.py:171-192`、`:261`、`:221` |
| `llm_enhance` | 唯一"类名与清单名不一致"的插件（历史改名，README 仍写"防抖合并 + 群聊用户信息感知"） | `llm_enhance/module.json:3`、`app/plugins/README.md:37` vs `llm_enhance/main.py:6-7` |
| `__init__.py` | 8 个插件是**纯 docstring 空壳**（1 行，如 `llm_assembly/__init__.py:1`、`llm_output/__init__.py:1`）；只有 `llm_memory/__init__.py:9-15` 真的再出口 `handle_memory_command` / `memory_manager` / `build_memory_tools`。`src/__init__.py` 全是 1 行空壳。→ 插件间 import 走的是 `app.plugins.<name>.src.xxx` 深路径（见 §6.2），而不是各插件的公开出口 | 逐文件 |
| 默认配置落库 | `ModuleConfig.set/update` 把 `raw_config`（含所有默认值）整份写回 → "用户没改过"这个信息会丢，迁移推导（如 `mode`）只在**从未保存过**时有效 | `app/plugin/base.py:55-78`、`llm_output/src/output_mode.py:23-35` |

## 8. 收敛建议（插件侧视角）

**现状**：9 个插件里，真正"提示词组装/输出"相关的是 4 个（`llm_assembly`、`llm_message_meta`、`llm_output`，加上 `llm_context` 的提示块与 `llm_proactive` 的 5 个块开关），却被切成 3 条装配路径、4 个 config 源、2 套词表。

**目标：把"组装 + 输出"收成 2 个插件，另 3 个业务插件保留 + 1 个调试并入 = 共 6 个。**

| 新插件 | 吸收 | 理由（证据） |
|---|---|---|
| **`llm_prompt`（消息组装与渲染）** | `llm_assembly` 7 键 + `llm_message_meta` 14 键 + `llm_context.referent_prompt_enable` + `llm_proactive` 的 5 个 `*_prompt*` 键 | 它们全都在决定"本轮 messages 里那段 user/system 文本长什么样"：`user_message_format`（assembly）与 `meta_sender_style/meta_sent_style`（meta）本来就共同喂 `build_current_turn_text`（`session/model.py:580-602`）；`prompt_time_position` 与 `include_time` 是同一件事的两半（§5-2/5-3）。合并后**一对一**：一个"时间"开关（位置+显示合一）、一个"本轮格式"枚举（真实词表 single_line/legacy）、一个"元信息项"多选 |
| **`llm_output`（输出形态）** | 保留 `llm_output`，吸收 `llm_proactive.stream_proactive_enabled` 与 `llm_schedule.stream_scheduled_enabled` | 输出形态只该有一个事实源；现在这两个开关要"再与 agent 的旧 `stream_output` 相与"，而那个旧键已经写不进 agent（§4.6/4.7）→ 恒 False。合并后统一问 `policy_get(runtime,"output",…)` |
| 保留 `llm_memory` | 23 键不动 | 唯一全部键都有正确读取点的插件（§4.5），别动 |
| 保留 `llm_tools_extra`（+修 1 键） | 把 `onebot_tools_max_result` 的读取改到 `policy_config(runtime,"tools")` | 现在读 `runtime.config`（`executor.py:148`），是插件页唯一暴露又无效的 OneBot 数值 |
| **`llm_trigger`（非被动触发）** | `llm_proactive` + `llm_schedule` 合并 | 两者结构同构（facade + 计时器 + `on_online/offline` + 流式分支 + 同一套"读自己 config 蹭别的模块键"坏习惯）；且它们已经互相 import（`scheduler.py:303`），README 明令禁止。合并后顺带消掉"读错来源"的 6 处 |
| 并入 | `llm_enhance.debug_prompt` → `llm_prompt` 的"请求落盘/打印"组 | 与 `prompt_dump_*` 是同一需求的两个开关（§5-5） |

**若只想做最小手术（不改文件结构）**，按性价比排序：

1. 删两僵尸键 `stream_send_by_sentence`、`stream_keep_order`（涉及 schema/默认值/归属表 4 处），或给它们补上真实实现。
2. `llm_output` 把 `mode` 加进 `OWNED_KEYS` + `MODULE_KEYS`，把 `speech_via_tool` 降为纯迁移键（`config_partition.py:244`）。
3. `llm_assembly` 的两个格式键：要么删（功能并入 meta），要么把 `prompt.py:161-170` 的接受词表改成 schema 里的词（`user_head/user_tail/off`），并让 `llm_message_meta/src/service.py:82` 改用 `policy_view(runtime)`。
4. `llm_message_meta` 的 `meta_sender_style/meta_sent_style/meta_instruction_mode`：把 schema 选项改成代码真实词表（`single/new/legacy`、`off/new`），或删掉选择器只留"单行/六行"一个枚举。
5. `llm_tools_extra/executor.py:148` 改 `policy_get(runtime,"tools","onebot_tools_max_result",…)`；同时把 `onebot_tools_enable` 的模块默认改回 `False`。
6. 给 `MODULE_KEYS` 补 `mode`、把 `debug_prompt` 从 `DELETED_KEYS` 移出（或给 `llm_enhance` 声明 `OWNED_KEYS`），并让 `llm_knowledge` 的三个键有归宿（建插件或并回本体）。

## 9. 未覆盖 / 不确定

1. **没跑任何测试**（任务禁改代码、只做盘点）；§3 关于测试"固化漂移"的判断来自读断言（`tests/plugins/test_llm_output.py:5`），未实际执行。
2. `runtime.config` 是否可能被"会话级配置档案"替换成合并视图，未逐条核实（`llm_proactive/src/manager.py:235-236` 的注释提到"会话配置档案先切换"）。若确实会切换，则 §4.2/4.3/4.6 里"读 agent 默认值"的结论需按会话档案再核一遍——但即便如此，**模块键不在 agent 默认里，仍读不到模块值**这一层结论不变。
3. `llm_knowledge` 的 3 个键判为"无持有者"是基于 `app/plugins/` 下无该目录；未排查 `module/plugins/`（运行时安装目录）里是否有第三方实现。
4. 各插件 `config_schema` 在 WebUI 的实际渲染方式（分组/隐藏/是否二次过滤）未核；本报告按"schema 即表单"理解。`llm_tools_extra` 的 `SCHEMA_HIDDEN_KEYS` 在源码中不存在（只在测试里本地定义），实际隐藏方式就是"不写进 SCHEMA"。
5. `app/plugins/llm_tools_extra/src/onebot/manifest.json`（4985 行）是工具清单，未逐条核对工具名与 `onebot_tool_overrides` 的对应关系。
6. 代码级依赖只找到了 3 条 import 边（§6.2）；是否有通过 `ctx.services` 的隐性耦合未穷举（9 个 `on_load` 只用到 `runtime` / `task_manager` / `scheduler` / `features`）。

---

## 附录 A · 各键 `config_schema` 说明原文（逐字，仅作对照，不作判定依据）

> 用途：把"界面承诺"和 §4 的"实际行为"并排看。§4 的判定与这里的说法冲突时，**以 §4（读取点）为准**。

### A.1 `llm_output`（`config_schema.py` L3-L163）

| 键 | 说明原文 |
|---|---|
| `mode` | speech=文本只当思考、发言走工具（默认）；stream=按句流式；text=一次发完。 |
| `stream_output` | moved 自 agent 配置。mode=text 时仅作兼容：打开等同类 mode=stream |
| `stream_send_by_sentence` | 按句切分后逐条发送 |
| `stream_sentence_max_length` | 流式输出时每条消息的最大字符数 |
| `stream_keep_order` | 保持生成顺序逐条发送（建议开启） |
| `stream_send_pool_enabled` | 开启后流式句子进入有序消息池，按频率发送 |
| `stream_send_interval_mode` | 固定间隔 = 恒定节奏；按字数曲线 = 长句多等；不等待 = 连续发送。 |
| `stream_send_interval_base_ms` | 固定间隔或曲线的基础延迟 |
| `stream_send_interval_min_ms` | 最终延迟的下限 |
| `stream_send_interval_max_ms` | 最终延迟的上限 |
| `stream_send_curve` | 按字数计算延迟的曲线 |
| `stream_send_curve_k` | 曲线系数 k，越大延迟随字数增长越明显 |
| `stream_short_message_length` | 按字数曲线模式下判定短句的阈值。 |
| `stream_short_message_delay_ms` | 按字数曲线模式下短句的延迟。 |
| `stream_long_message_delay_ms` | 按字数曲线模式下长句的延迟。 |
| `stream_send_prefix` | 每条流式消息发送前追加的文本 |
| `stream_send_suffix` | 每条流式消息发送后追加的文本 |
| `stream_send_max_queue` | 队列满时的处理策略见下 |
| `stream_queue_full_policy` | 消息池满时的行为 |
| `stream_flush_on_finish` | 流结束后是否忽略剩余间隔立即发送剩余消息 |
| `speech_max_bubbles` | 一轮对话里最多发几个气泡 |
| `speech_max_bubble_chars` | 单个气泡的最大字符数 |
| `speech_reminder_rounds` | 剩余轮次不足该值时提醒模型尽快开口 |
| `speech_force_reply_on_call` | @ / 私聊 / 拍一拍时，若一句话没说会额外补一轮 |

（另：`:137-139` 有一条注释明说 `speech_via_tool` 不再出现在设置页、"输出形态是唯一开关"——但归属表 `config_partition.py:244` 仍把它算作模块持有键。）

### A.2 `llm_assembly`（`config_schema.py` L3-L52）

| 键 | 说明原文 |
|---|---|
| `behavior_rules_override` | 留空则用内置规则；填写后整段替换 |
| `participation` | 影响行为规则段的语气与主动程度 |
| `emoji_react_level` | 0 = 几乎不贴；数值越大越爱贴（1 每 3~5 轮、2 优先、3 能贴就贴）。 |
| `user_message_format` | 用户消息的渲染形态 |
| `prompt_time_position` | 当前时间放在用户消息头部还是尾部 |
| `prompt_dump_enable` | 把每轮最终请求写入文件，便于比对。默认关闭。 |
| `prompt_dump_max_entries` | 滚动文件最多保留多少轮请求（从最旧开始丢） |

### A.3 `llm_message_meta`（`config_schema.py` L3-L90）

| 键 | 说明原文 |
|---|---|
| `include_time` | 在用户消息里带上时间，让模型知道「现在几点」 |
| `include_sender` | 带上发送者昵称与 QQ |
| `include_private_qq` | 私聊时是否把对方 QQ 作为元信息给出 |
| `include_mentioned` | 列出本条消息 @ 了谁（可反查昵称） |
| `include_quote` | 本条消息引用了哪条（内容由下面的开关决定是否拉取） |
| `include_quote_sender` | 引用行里带被引用消息的发送者 |
| `include_sent` | 附带本条消息的原始正文（便于模型区分元信息与正文） |
| `fetch_at_nickname` | 关掉则只显示 QQ 号（省一次 API 调用） |
| `fetch_quote_content` | 关掉则只显示「引用了某条」，不拉正文 |
| `meta_sender_style` | 元信息里发送者的呈现风格 |
| `meta_sent_style` | 元信息里正文的呈现风格 |
| `meta_instruction_mode` | 是否注入「这些元信息不是用户说的话」的说明 |
| `meta_mask_nickname` | 把句子型昵称替换成安全名（避免模型把昵称当指令） |
| `clean_output_parentheses` | 剥离助手输出里的（…），阻止括号风格自我强化（只影响历史，不影响对外展示） |

### A.4 `llm_context`（`config_schema.py` L3-L68）

| 键 | 说明原文 |
|---|---|
| `context_expand_enable` | 开启后聊天记录里取不到的内容会标【未展开:…】，并提供 expand_* 工具按需取回 |
| `referent_resolve_enable` | 维护会话焦点表，让「那个样子/上一条」这类回指能定位到具体消息 |
| `referent_prefetch_enable` | 判定出明确指向时，请求前直接把内容取回来（不等模型决定要不要调工具） |
| `referent_ambiguous_policy` | 候选不止一个时的策略 |
| `referent_prefetch_max` | 一轮最多预取几个候选（预算保护） |
| `referent_focus_ttl` | 焦点项多久没被关注就从焦点表淘汰 |
| `referent_focus_max` | 每会话保留的焦点项数量上限 |
| `referent_prompt_enable` | 是否把「当前对话焦点」注入请求（按本轮可用能力裁剪） |
| `history_auto_qq_min_local` | 本地记录少于该条数时自动补拉聊天记录。0 = 总是补拉。 |
| `history_cross_query_enable` | 私聊里允许查询「发起人自己也是成员」的群历史（结果只回给发起人） |

### A.5 `llm_memory`（`src/schemas.py` L10-L188）

| 键 | 说明原文 |
|---|---|
| `memory_enable` | 跨会话记住用户说过的偏好/事实，并在后续对话里召回 |
| `memory_private_enable` | 私聊里是否记录与召回 |
| `memory_group_enable` | 群聊里是否记录与召回（按群内成员隔离） |
| `memory_profile_enable` | 把姓名/身份/住址/长期偏好这类画像常驻在提示词里，不依赖检索命中 |
| `memory_recall_mode` | hybrid=关键词+向量混合（未配置 embedding 模型时自动退化为关键词）；keyword=只用关键词 |
| `memory_embedding_model_id` | 用于混合召回与相似去重；留空则自动选第一个 Embedding 模型，没有则退化为关键词召回 |
| `memory_recall_max` | 一次注入最多几条事实记忆 |
| `memory_recall_max_chars` | 事实记忆块的总字符上限 |
| `memory_profile_max_chars` | 常驻画像块的最大字符数（超出按重要度截断） |
| `memory_save_deterministic` | 用户明确说「记住…」时不等模型，直接落库 |
| `memory_extract_enable` | 对话结束后用模型从原始语料里抽取值得长期记住的事实（后台异步，不增加回合延迟） |
| `memory_extract_interval_min` | 同一会话两次自动蒸馏之间的最短间隔 |
| `memory_consolidate_batch` | 一次最多拿多少条未巩固的用户消息给模型抽取 |
| `memory_consolidate_min_chars` | 累计原始语料少于该字数时不调用模型（省 token） |
| `memory_max_per_owner` | 同一 owner 保留的 active 记忆条数上限，超出按重要度×时间权重淘汰 |
| `memory_user_cross_group` | 同一个人在不同群里共用一份画像（默认关闭，保护隐私） |
| `memory_audit_enable` | 记录写入/读取/注入/下架/清除事件（/memory audit 查看） |
| `memory_audit_inject` | 把每次记忆注入也记进审计（调试用，日志噪音较大） |
| `memory_min_confidence` | 低于该可信度的事实不注入（被点名时仍上浮供核对） |
| `memory_inject_hedge` | 中/低置信记忆加「（好像）/（记不太清）」前缀，提醒模型这些是模糊记忆 |
| `memory_max_age_days` | 超过该天数的记忆不再注入；0=不限（数据保留，可被检索到） |
| `memory_on_reset` | 显式重置（#llm new/exit/stop）时怎么处理该会话的记忆 |
| `memory_upgrade_saved_only` | 重置挂起时，仅「明确保存/已确认」的记忆继续注入，自动蒸馏的旧事实一律挂起 |

### A.6 `llm_proactive`（`config_schema.py` L3-L105）

| 键 | 说明原文 |
|---|---|
| `proactive_friend_enable` | 对下方私聊会话按随机间隔主动发言 |
| `proactive_group_enable` | 群聊沉默一段时间后主动开口 |
| `proactive_friend_sessions` | 对哪些 QQ 主动发言，每行一个 |
| `proactive_group_sessions` | 对哪些群主动发言，每行一个 |
| `proactive_min_interval_minutes` | 私聊两次主动发言之间的最短等待 |
| `proactive_max_interval_minutes` | 私聊两次主动发言之间的最长等待 |
| `proactive_max_unanswered` | 连续无人接话达到此数即停止主动发言。0 = 不限。 |
| `proactive_quiet_hours_start` | 该小时起不主动发言（0-23）；被 @ 等被动回复不受影响，其免打扰在「回复与权限 → 高级规则」 |
| `proactive_quiet_hours_end` | 该小时起恢复（可跨天）；被 @ 等被动回复不受影响，其免打扰在「回复与权限 → 高级规则」 |
| `proactive_group_idle_minutes` | 群里安静多久后主动开口 |
| `proactive_prompt` | 留空用内置模板；支持 `{{current_time}}`、`{{unanswered_count}}` 占位。 |
| `proactive_prompt_enable` | 关掉则不注入主动性提示块 |
| `proactive_history_intent_nudge` | 用户说「刚才/之前」时提示模型先查记录 |
| `proactive_env_prompt_enable` | 按本轮可用能力给出环境查询提示 |
| `proactive_unresolved_prompt_enable` | 上下文里有未展开内容时，提示模型按需展开 |
| `proactive_env_intent_nudge` | 问到群名/成员时提示可调用环境工具 |
| `stream_proactive_enabled` | 复用「流式输出」插件的流式配置 |

### A.7 `llm_schedule`（`config_schema.py` L3-L21）

| 键 | 说明原文 |
|---|---|
| `schedule_enable` | 允许模型/你创建到点触发的任务 |
| `schedule_prompt` | 留空用内置模板（让它像真人一样自然开口） |
| `stream_scheduled_enabled` | 复用「流式输出」插件的流式配置 |

### A.8 `llm_tools_extra`（`config_schema.py` L8-L38，只有 5 键进表单）

| 键 | 说明原文 |
|---|---|
| `onebot_tools_max_result` | 单个 OneBot 工具返回给模型的字符上限 |
| `poke_cooldown_seconds` | 同一目标两次戳之间的最小间隔 |
| `mcp_servers` | 每项含名称、启动命令与参数；留空表示不用外部工具，格式可参考文档示例 |
| `tool_result_directive_enable` | 在工具结果末尾追加一句「用自己的口吻回应」，压住复述倾向 |
| `tool_result_directive` | 留空用内置文案；填写后整段替换 |

（`onebot_tools_enable/allowed/denied/debug/onebot_tool_overrides` 只出现在 `main.py::OWNED_KEYS` 与默认配置里，`SCHEMA` 中**没有任何描述**——文件头注释称"见 `SCHEMA_HIDDEN_KEYS`"，该符号在源码中不存在。）

### A.9 `llm_enhance`（`config_schema.py` L6-L16）

| 键 | 说明原文 |
|---|---|
| `debug_prompt` | 开启后把本轮发给模型的完整内容写进日志 |
