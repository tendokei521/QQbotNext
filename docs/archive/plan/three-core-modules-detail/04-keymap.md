# 04 · 109 键 → 三个核心模块的逐键映射与新 `config_schema` 草稿

> 作者：配置迁移设计员（keymap）｜日期：2026-10-31｜**只读设计，未改任何 `.py`**
>
> 输入（以第 1 条为准，其余交叉核对）：
> - `docs/reviews/2026-10-31-assessment/01-plugins.md`（109 键逐键底账，含真实读取点与"僵尸/被旁路/词表失配"判定）
> - `docs/reviews/2026-10-31-assessment/02-framework.md` §4/§5（user 消息组装链、配置读取链、同键不同层）
> - `docs/reviews/2026-10-31-assembly-output-consolidation.md` §5（三模块 + TurnPlan 目标形态）
>
> 口径：**只采信执行路径上的读取点**（`file:line`），不采信 `config_schema` 的 `label/description`。
> 本文所有"旧默认"取自各插件 `main.py::default_config` / `src/schemas.py`，与 01 §4 已逐格对齐；
> 凡 01 的行号与当前 HEAD 有 1–3 行漂移（另一会话在改 `assembly/`），本文用**当前 HEAD 实读行号**并标注。
>
> 状态：待 Lead 拍板（§7.5 列出 5 个需拍板项）。本轮不落代码。

---

## 0. 结论一页

| 项 | 结果 |
|---|---|
| 三个核心模块键数 | **`llm_prompt` 12 / `llm_delivery` 12 / `llm_trigger` 10 = 34**（目标 ≤12/≤12/≤10，全部达标） |
| 109 键算式 | **保留 42 + 并入新键 52（→34 键）+ 退役 15 = 109**（§7.1 逐项闭合） |
| 键总数 | 109 → **76**（34 核心 + 42 能力模块；能力模块 = `llm_context` 9 + `llm_memory` 23 + `llm_tools_extra` 10） |
| 删除/退役 | **15 条**：僵尸 3 + 常量内化 9 + 重复事实源/不可达 3，**每条都有替代方案**（§4） |
| 纯新增键 | **0 个**（34 个新键每个都能追溯到 ≥1 个旧键；新增的只有 2 个"代码已支持但从未暴露"的选项和 1 个引用态，见 §7.3） |
| 图例 | 命运列：`保留`（同名迁入）/ `改名`（同义新名）/ `合并`（并入某新键）/ `删除`（退役，值作迁移输入后归档）/ `保留(不动)`（留在能力模块） |

**五条最关键的裁决**

1. **时间行**：`prompt_time_position`(head/tail/off，代码只认 `head`) + `include_time`(bool) → **一个三态 `prompt.time_position`**＝`head|tail|off`。旧行为"位置恒 tail、`off` 无效"被修正，迁移按**旧可观察行为**取值（§3.1）。
2. **本轮消息格式**：`user_message_format`(default/single_line/labeled) + `meta_sender_style`(compact/verbose/name_only) + `meta_sent_style`(compact/quoted/fenced) + `meta_instruction_mode`(auto/always/off) → **一个枚举 `prompt.user_line_format`**＝`single_line|multi_line|multi_line_verbose`（词表＝代码真实词表），图例文案由格式**推导**，不再单独开关（§3.2）。
3. **输出形态**：`llm_output.mode` 是唯一事实源；`stream_output`、`speech_via_tool` 降为**仅迁移**（只在从未保存过 `mode` 的老账号上参与推导），新 schema 只留 `delivery.mode`；`llm_proactive.stream_proactive_enabled` / `llm_schedule.stream_scheduled_enabled`（两者恒 False、条件不可达）**删除**，由 `mode` 统一管辖四个入口（§3.4/§3.5）。
4. **流式节奏 9 键**：`stream_send_by_sentence`、`stream_keep_order` 全仓 0 读取 → **删，不补实现**（切分本就恒执行 `app/agent/loop/tool_step.py:309`；池天然 FIFO 单消费者 `app/agent/delivery/pool.py:5,39,126`）；其余节奏参数压成 **`stream_pace`（真实词表 6 档）+ `base_ms` + `max_ms`** 三键，`curve_k`、`interval_min_ms`、短句阈值三项、队列三项以常量内化（§3.5 / §4）。
5. **记录本轮请求**：`prompt_dump_enable` + `prompt_dump_max_entries` + `llm_enhance.debug_prompt` → **`prompt.prompt_record` 一键四态**（`off|file|log|both`），`max_entries` 常量内化 20（§3.3）。

---

## 1. 判定口径与两处底账勘误

### 1.1 口径

1. **"生效值"取决于读的是哪个 config 对象**（02 §5.1）：`runtime.config`（AgentConfig，模块键恒返回硬编码默认，`app/agent/config.py:366-378`）/ 模块 `*_options` 视图 / `policy_view`（链式，`app/agent/policy.py:70-85`）。01 §6.3 列了 8 处"读错来源"，本文在迁移规则里按"旧代码实际读到的那个对象"取值，**不按用户以为的值**。
2. **旧默认值三处不一致**（`config.py` / 模块 `default_config` / 代码内 fallback，如 `app/domain/pace.py:21-44`）：本文"旧默认"列一律取**模块 `default_config`**（新装用户实际拿到的值），与 01 §1 第 6 条（默认值漂移清单）一致；代码 fallback 只在迁移公式里作为"兜底语义"引用。
3. **本轮只做键表与 schema，不动实现**：所有"改读点"只写成落地要求（§8），不是本文交付内容。

### 1.2 勘误 A · `llm_memory` 实为 24 键（01 表漏了 `memory_inject_recency`）

| 证据 | 结论 |
|---|---|
| `app/plugins/llm_memory/src/schemas.py:162` 有该键定义；`app/plugins/llm_memory/src/manager.py:311` 有真实读取点（`annotate_age=bool(self._get("memory_inject_recency", True))`）；`app/agent/config_partition.py:298` 也把它算作 `llm_memory` 持有 | 01 §4.5 的 23 行表**漏登 1 键**，真实口径是 110 键 |

处理：本文**主算式仍按 Lead 固定的 109 键闭合**（否则与任务给的口径打架），并在 §7.2 给出 110 键版本。
该键留在 `llm_memory`，**不影响三核心模块的任何映射**，只让"能力模块保留组"从 42 变成 43（→ 实盘 77 键）。

### 1.3 勘误 B · `speech_via_tool` 不在 109 内，但必须留在迁移表里

`speech_via_tool` 只在 `OWNED_KEYS`/`MODULE_KEYS`（`app/agent/config_partition.py:244`）与 `default_config`（`app/plugins/llm_output/main.py:97`）里，**不在任何 schema**（`llm_output/config_schema.py:137-139` 注释明说"不再出现在设置页"）。它是"仅兼容"两键之一（另一键 `stream_output` 在 109 内），本文把它单列在 §3.4 的推导输入里。

---

## 2. 逐键总表（109 行）

> 版式说明：为了可读，总表按**旧模块**切成 9 个分表（分表标题即"旧模块 + 键数"，行号 1–109 连续不重不漏）。
> **新模块**由 `新键` 列的前缀给出：`prompt.` / `delivery.` / `trigger.` = 三个核心模块；
> 写"同名（仍在 X）"= 留在能力模块、键名与默认值都不变（§2.5 / §2.8 的分表因此省略"新键/新默认"两列）；
> `—` = 该旧键退役、不产生新键（§4 给了逐条替代方案）。
>
> 列说明：`旧默认` 取模块 `default_config`（括号内为 agent 侧旧默认，若不同）；`判定` 用 01 §4 的标签（✅ 生效 / ⚠️ 条件生效 / 🟡 仅兼容 / 🔴 僵尸 / 🚫 被旁路或读错来源）；`命运` 见 §0 图例。

### 2.1 `llm_output`（24 键；`config_schema.py` L4-L163，默认 `main.py:74-102`）→ 全部进 `llm_delivery`

| # | 旧键 | 类型 | 旧默认 | 真实读取点 | 判定 | 命运 | 新键 | 新默认 | 理由 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `mode` | select | `speech` | `src/output_mode.py:38-62` → `service.py:43,73-79` | ✅唯一事实源（不在 OWNED/归属表） | 保留 | `delivery.mode` | `speech` | 唯一决定"文本算不算发言"的开关，进 schema 也进归属表 |
| 2 | `stream_output` | boolean | `False` | `app/agent/loop/turn.py:307`；`app/agent/capabilities.py:81-83` | 🟡仅兼容 | 删除（仅迁移） | — | — | 与 `mode` 语义重复（`mode=stream` 已表达）；只在老配置推导中用（`output_mode.py:50-56`） |
| 3 | `stream_send_by_sentence` | boolean | `True` | **无**（仅 schema/默认值/归属表/文档） | 🔴僵尸 | 删除 | — | — | 切分恒执行：`app/agent/loop/tool_step.py:309` `split_sentences(buffer, max_length=req.max_msg_len)`，无分支 |
| 4 | `stream_sentence_max_length` | number | `200` | `chat.py:269-273`、`turn.py:533`、`llm_proactive/src/stream.py:103` | ✅ | 保留 | `delivery.stream_sentence_max_length` | `200` | 唯一影响"一条流式消息多长"的键 |
| 5 | `stream_keep_order` | boolean | `True` | **无** | 🔴僵尸 | 删除 | — | — | 池天然有序：`pool.py:5` docstring / `:39` 单队列 / `:126` 单发送协程 |
| 6 | `stream_send_pool_enabled` | boolean | `False` | `app/agent/delivery/output.py:113-122` | ⚠️需 `mode≠text` | 保留 | `delivery.stream_send_pool_enabled` | `False` | 池的唯一总闸；默认关=9 个节奏键默认不生效（01 §1.3） |
| 7 | `stream_send_interval_mode` | select | `none` | `app/domain/pace.py:15-16` | ⚠️需池 | 合并 | `delivery.stream_pace` | `off` | 与 `curve` 是同一件事的两半（先选模式再选曲线） |
| 8 | `stream_send_interval_base_ms` | number | `0` | `pace.py:21` | ⚠️需池 | 改名 | `delivery.stream_pace_base_ms` | `0` | 基础延迟，语义不变 |
| 9 | `stream_send_interval_min_ms` | number | `0` | `pace.py:43` | ⚠️需池 | 删除 | — | — | 下限固定 0（模块默认就是 0；代码 fallback 100 是第三套默认，一并清掉） |
| 10 | `stream_send_interval_max_ms` | number | `0` | `pace.py:44` | ⚠️需池 | 改名 | `delivery.stream_pace_max_ms` | `0` | 上限（0=不限），保留用户最需要的那个夹紧值 |
| 11 | `stream_send_curve` | select | `none` | `pace.py:22,28-41` | 🚫`linear` 无实现；`none` 实际走 else=sqrt | 合并 | `delivery.stream_pace` | `off` | 真实词表只有 `sqrt/log/inverse/short_long`（`else`→sqrt），schema 里的 `none/linear` 是幽灵选项 |
| 12 | `stream_send_curve_k` | number | `1.0` | `pace.py:23` | ⚠️需池 | 删除 | — | — | 强度内化进曲线预设（k=1.0＝模块默认），避免"这个延迟是哪来的" |
| 13 | `stream_short_message_length` | number | `10` | `pace.py:35` | ⚠️需池 | 删除 | — | — | `short_long` 预置常量 10（= 模块默认） |
| 14 | `stream_short_message_delay_ms` | number | `300` | `pace.py:37` | ⚠️需池 | 删除 | — | — | `short_long` 预置常量 300 |
| 15 | `stream_long_message_delay_ms` | number | `1200` | `pace.py:39` | ⚠️需池（默认值曾与旧库反转） | 删除 | — | — | `short_long` 预置常量 1200 |
| 16 | `stream_send_prefix` | string | `""` | `pool.py:117-124` | ⚠️需池 | 保留 | `delivery.stream_send_prefix` | `""` | 仍有人会加"（续）"这类前后缀，便宜就留 |
| 17 | `stream_send_suffix` | string | `""` | `pool.py:117-124` | ⚠️需池 | 保留 | `delivery.stream_send_suffix` | `""` | 同上 |
| 18 | `stream_send_max_queue` | number | `20` | `pool.py:38` | ⚠️需池 | 删除 | — | — | 队列容量是纯工程参数，常量 20（= 模块默认） |
| 19 | `stream_queue_full_policy` | select | `backpressure` | `pool.py:55-68` | ⚠️需池 | 删除 | — | — | 常量 `backpressure`（= 模块默认，且是唯一"不丢消息"的选择） |
| 20 | `stream_flush_on_finish` | boolean | `False` | `pool.py:70-76` | ⚠️需池 | 删除 | — | — | 常量 `False`（= 模块默认；改成 True 是行为变更，本轮不动） |
| 21 | `speech_max_bubbles` | number | `5`（旧 4） | `app/agent/loop/assistant_output.py:59` | ⚠️需 `mode=speech` + 模型支持 tool_use | 保留 | `delivery.speech_max_bubbles` | `5` | 发言通道的核心旋钮 |
| 22 | `speech_max_bubble_chars` | number | `200`（旧 300） | `assistant_output.py:61` | ⚠️同上 | 保留 | `delivery.speech_max_bubble_chars` | `200` | 同上 |
| 23 | `speech_reminder_rounds` | number | `2`（旧 3） | `chat.py:281` | ⚠️同上 | 保留 | `delivery.speech_reminder_rounds` | `2` | 同上 |
| 24 | `speech_force_reply_on_call` | boolean | `True` | `assistant_output.py:89-91` | ⚠️同上 | 保留 | `delivery.speech_force_reply_on_call` | `True` | 被 @ 时补一轮，直接关系"不回"症状 |

> 表外键（不计入 109）：`speech_via_tool`（boolean，`True`，读取点 `app/agent/delivery/speech.py:314-329`、`assistant_output.py:56`、`capabilities.py:81`、`app/agent/persona/rules.py:109-112`、`app/agent/assembly/assembler.py:470-476`，被 `OutputOptionsView.get` 动态覆写为 `mode=="speech"`：`llm_output/src/output_mode.py:73-75`）→ **命运：仅迁移**，是 §3.4 的推导输入之一。

### 2.2 `llm_assembly`（7 键；`config_schema.py` L4-L52，默认 `main.py:48-56`）→ 全部进 `llm_prompt`

| # | 旧键 | 类型 | 旧默认 | 真实读取点 | 判定 | 命运 | 新键 | 新默认 | 理由 |
|---|---|---|---|---|---|---|---|---|---|
| 25 | `behavior_rules_override` | textarea | `""` | `app/agent/assembly/builtin_sections.py:150-171`（`req.cfg` 已是模块视图，`llm_assembly/service.py:33-42`） | ✅ | 保留 | `prompt.behavior_rules_override` | `""` | 行为规则整段覆盖，语义不变 |
| 26 | `participation` | select low/normal/high | `normal`（旧 `medium`） | `app/agent/persona/rules.py:116` → `:270-283`（只特判 `low`/`high`） | ✅但词表双轨 | 保留 | `prompt.participation` | `normal` | 代码词表就是 low/high/其它；`medium` 迁移归一 `normal` |
| 27 | `emoji_react_level` | number 0-3 | `0`（旧 1） | `builtin_sections.py:189-193`、`rules.py:118→370-380` | ✅默认漂移 | 保留 | `prompt.emoji_react_level` | **`1`**（建议，见 §7.5-1） | 0 时 `emoji_hint` 整段不注入，而行为段仍写"见【可用表情】"→ 悬空指涉 |
| 28 | `user_message_format` | select default/single_line/labeled | `default` | 组装侧 `app/agent/assembly/prompt.py:161-164` + `builtin_sections.py:245`；渲染侧读 `runtime.config`（`llm_message_meta/src/service.py:82,95`） | 🚫被旁路（三选项等价，选不到 legacy） | 合并 | `prompt.user_line_format` | `single_line` | 与两个 `meta_*_style` 是同一件事（§3.2） |
| 29 | `prompt_time_position` | select user_head/user_tail/off | `user_head` | `prompt.py:167-170`（只认 `head`）；`service.py:96,110,145` 读 `runtime.config` | 🚫三选项**全部**=tail，`off` 无效 | 合并 | `prompt.time_position` | `tail` | 与 `include_time` 合成一个三态（§3.1） |
| 30 | `prompt_dump_enable` | boolean | `False` | `app/agent/assembly/dump.py:45`（config 来自 `dump.py:129-130` 的 `runtime.assembly_options`） | ✅ | 合并 | `prompt.prompt_record` | `off` | 与 `llm_enhance.debug_prompt` 是同一需求的两个出口（§3.3） |
| 31 | `prompt_dump_max_entries` | number | `20` | `dump.py:51` | ✅ | 删除 | — | — | 滚动上限常量内化 20（= 模块默认），不值得占一个键位 |

### 2.3 `llm_message_meta`（14 键；`config_schema.py` L4-L90，默认 `main.py:54-69`）→ 全部进 `llm_prompt`

| # | 旧键 | 类型 | 旧默认 | 真实读取点 | 判定 | 命运 | 新键 | 新默认 | 理由 |
|---|---|---|---|---|---|---|---|---|---|
| 32 | `include_time` | boolean | `True` | `service.py:107-111`、`:145-146` | ✅**唯一**能关时间行的开关 | 合并 | `prompt.time_position` | `tail` | "有没有"+"放哪"合成三态（§3.1） |
| 33 | `include_private_qq` | boolean | `False`（旧 True） | `service.py:114-117` | ✅默认反转 | 合并 | `prompt.meta_sender` | `name` | 与 `include_sender` 同轴（都是"发送者怎么出现"，§3.6） |
| 34 | `include_sender` | boolean | `True` | `service.py:49-51`、`:127-134` | ✅ | 合并 | `prompt.meta_sender` | `name` | 同上 |
| 35 | `include_mentioned` | boolean | `True` | `service.py:53-54`、`:135-137` | ✅ | 合并 | `prompt.meta_mention` | `nickname` | 与 `fetch_at_nickname` 同轴（§3.6） |
| 36 | `include_quote` | boolean | `True` | `service.py:56-60`、`:139-144` | ✅ | 合并 | `prompt.meta_quote` | `with_sender` | 引用块三键同轴（§3.6） |
| 37 | `include_quote_sender` | boolean | `True` | `service.py:140` | ✅ | 合并 | `prompt.meta_quote` | `with_sender` | 同上 |
| 38 | `include_sent` | boolean | `True` | `service.py:148-150` | ✅ | 改名 | `prompt.meta_include_body` | `True` | 统一命名（`meta_*` 前缀），语义不变 |
| 39 | `fetch_at_nickname` | boolean | `True` | 本插件 `service.py:190-194`；**框架另读** `app/agent/loop/tool_source.py:36-38`（读 context 视图）、`app/agent/tools/session_tools.py:126` | ⚠️半生效（管得住本轮 @，管不住历史/背景块的 @） | 合并 | `prompt.meta_mention` | `nickname` | 与 `include_mentioned` 同轴；落地后两处读点统一读 prompt 视图（§8） |
| 40 | `fetch_quote_content` | boolean | `True` | `service.py:231-268` | ✅ | 合并 | `prompt.meta_quote` | `with_sender` | 同上（`off` 态保留"只显示引用了某条"= `ref_only`） |
| 41 | `meta_sender_style` | select compact/verbose/name_only | `compact` | `service.py:91` → `:129-134`（只认 `single`/`new`，其余=legacy）；`:95-98` 单行时被强制改写成 `single` | 🚫被覆写（三选项全=单行内联） | 合并 | `prompt.user_line_format` | `single_line` | 词表失配，合并进格式枚举（§3.2） |
| 42 | `meta_sent_style` | select compact/quoted/fenced | `compact` | `service.py:92` → `app/agent/session/model.py:580-609`（只认 `new`，其余=`发送了：`） | 🚫词表失配（三选项全=旧格式） | 合并 | `prompt.user_line_format` | `single_line` | 同上 |
| 43 | `meta_instruction_mode` | select auto/always/off | `auto` | `builtin_sections.py:226-257`（`off`→不注入；`new`→脱敏版；`auto/always` 都落 legacy） | ⚠️只有 `off`/`new` 有效 | 合并 | `prompt.prompt_nudges` | 含 `meta_legend` | 图例文案改成**由格式推导**（`:245-254`），只剩"要不要图例"这一个比特 → 归段开关（§3.7） |
| 44 | `meta_mask_nickname` | boolean | `False`（旧 True） | 本插件 **0 读取**；`llm_proactive/src/manager.py:206`、`llm_schedule/src/scheduler.py:470` 读的是**自己模块**的配置（永远 False）；主聊天历史 `app/agent/loop/turn_metrics.py:383-391` **硬编码 True** | 🔴僵尸 + 默认值反转 | 删除 | — | — | 替代：沿现状恒脱敏（主聊天已硬编码 True）；主动/定时改走同一渲染后自动一致（§4） |
| 45 | `clean_output_parentheses` | boolean | `True` | `assistant_output.py:36`（调用点 `:126`、`chat.py:600`、`turn.py:688`）、`pipeline.py:732-736`（都经 `policy_config(runtime,"meta")`） | ✅ | 保留 | `prompt.clean_output_parentheses` | `True` | 影响"进历史的助手文本"，属于消息渲染 |

### 2.4 `llm_context`（10 键；`config_schema.py` L4-L68，默认 `main.py:53-64`）→ **能力模块，9 保留 + 1 归 prompt**

| # | 旧键 | 类型 | 旧默认 | 真实读取点 | 判定 | 命运 | 新键 | 新默认 | 理由 |
|---|---|---|---|---|---|---|---|---|---|
| 46 | `context_expand_enable` | boolean | `True` | 模块 `llm_context/src/service.py:86-89`；框架 `prompt_prep.py:386-390`、`tool_source.py:36-38`、`session_tools.py:127`、`app/agent/tools/policy.py:79-86`；**读错来源** `llm_proactive/src/manager.py:214`、`llm_schedule/src/scheduler.py:478` | ✅但 6 个读点 | 保留(不动) | 同名（仍在 `llm_context`） | `True` | 能力模块键；本轮不动，只为 `llm_trigger` 消掉 2 个读错源（§8） |
| 47 | `referent_resolve_enable` | boolean | `True` | `llm_context/src/referent.py:180-185,221`；`prompt.py:263` | ✅ | 保留(不动) | 同名 | `True` | 与 `referent_prompt_enable` 是"与"关系，保留 |
| 48 | `referent_prefetch_enable` | boolean | `True` | `referent.py:253-259` | ✅ | 保留(不动) | 同名 | `True` | — |
| 49 | `referent_ambiguous_policy` | select | `fetch_all` | `referent.py:261,266-270` | ✅ | 保留(不动) | 同名 | `fetch_all` | — |
| 50 | `referent_prefetch_max` | number | `2` | `referent.py:242,262` | ✅ | 保留(不动) | 同名 | `2` | — |
| 51 | `referent_focus_ttl` | number | `1800` | `referent.py:226` | ✅ | 保留(不动) | 同名 | `1800` | — |
| 52 | `referent_focus_max` | number | `12` | `referent.py:227` | ✅ | 保留(不动) | 同名 | `12` | — |
| 53 | `referent_prompt_enable` | boolean | `True` | `app/agent/assembly/prompt.py:260-264` | ✅（提示词块开关，不是能力开关） | 合并 | `prompt.prompt_nudges`（`referent` 项） | 含该项 | 决定"焦点行进不进提示词"= 段开关，归 `llm_prompt`（Lead 已定） |
| 54 | `history_auto_qq_min_local` | number | `4` | `app/agent/tools/session_tools.py:374` | ✅ | 保留(不动) | 同名 | `4` | 历史补拉策略，属会话能力 |
| 55 | `history_cross_query_enable` | boolean | `True` | `session_tools.py:305-306` | ✅ | 保留(不动) | 同名 | `True` | 同上 |

### 2.5 `llm_memory`（23 键；`src/schemas.py`，默认由 schema 派生 `main.py:43`）→ **能力模块，全部保留(不动)**

> 01 §4.5 结论：23 键全部走 `policy_get(runtime,"memory",…)`，是全仓最干净的插件。本文**一键不动**。
> 另见 §1.2 勘误：实为 24 键（`memory_inject_recency`，`schemas.py:162` → `manager.py:311`）。

| # | 旧键 | 类型 | 旧默认 | 真实读取点 | 判定 | 命运 |
|---|---|---|---|---|---|---|
| 56 | `memory_enable` | boolean | `False` | `manager.py:141-142`；`main.py:114-115`；`app/agent/tools/policy.py:74-78`；`src/tool.py:119`；`src/commands.py:139` | ✅唯一总开关 | 保留(不动) |
| 57 | `memory_private_enable` | boolean | `True` | `manager.py:144-150` | ✅ | 保留(不动) |
| 58 | `memory_group_enable` | boolean | `True` | `manager.py:144-150` | ✅ | 保留(不动) |
| 59 | `memory_profile_enable` | boolean | `True` | `manager.py:202` | ✅ | 保留(不动) |
| 60 | `memory_recall_mode` | select hybrid/keyword | `hybrid` | `manager.py:158-160` | ✅ | 保留(不动) |
| 61 | `memory_embedding_model_id` | resource | `""` | `manager.py:163-174` | ✅ | 保留(不动) |
| 62 | `memory_recall_max` | number | `8` | `manager.py:273` | ✅ | 保留(不动) |
| 63 | `memory_recall_max_chars` | number | `1200`（旧 600） | `manager.py:275` | ✅默认翻倍 | 保留(不动) |
| 64 | `memory_profile_max_chars` | number | `800` | `manager.py:228` | ✅ | 保留(不动) |
| 65 | `memory_save_deterministic` | boolean | `True` | `manager.py:465` | ✅ | 保留(不动) |
| 66 | `memory_extract_enable` | boolean | `True` | `consolidate.py:110`；`manager.py:595` | ✅ | 保留(不动) |
| 67 | `memory_extract_interval_min` | number | `10` | `consolidate.py:114` | ✅ | 保留(不动) |
| 68 | `memory_consolidate_batch` | number | `20` | `consolidate.py:216`；`manager.py:535` | ✅ | 保留(不动) |
| 69 | `memory_consolidate_min_chars` | number | `30` | `consolidate.py:238` | ✅ | 保留(不动) |
| 70 | `memory_max_per_owner` | number | `200`（旧 300） | `manager.py:441` | ✅默认收紧 | 保留(不动) |
| 71 | `memory_user_cross_group` | boolean | `False` | `src/tool.py:138` | ✅ | 保留(不动) |
| 72 | `memory_audit_enable` | boolean | `True` | `manager.py:339,442,743,750` | ✅ | 保留(不动) |
| 73 | `memory_audit_inject` | boolean | `False` | `manager.py:341` | ✅ | 保留(不动) |
| 74 | `memory_min_confidence` | number | `0.5` | `manager.py:251` | ✅ | 保留(不动) |
| 75 | `memory_inject_hedge` | boolean | `True` | `manager.py:255` | ✅ | 保留(不动) |
| 76 | `memory_max_age_days` | number | `0`（旧 180） | `manager.py:252` | ✅语义反转（0=不限） | 保留(不动) |
| 77 | `memory_on_reset` | select suspend/clear/keep | `suspend` | `manager.py:719` | ✅ | 保留(不动) |
| 78 | `memory_upgrade_saved_only` | boolean | `True` | `manager.py:254` | ✅ | 保留(不动) |

### 2.6 `llm_proactive`（17 键；`config_schema.py` L4-L105，默认 `main.py:63-81`）→ 5 进 prompt / 1 进 delivery / 11 进 trigger

| # | 旧键 | 类型 | 旧默认 | 真实读取点 | 判定 | 命运 | 新键 | 新默认 | 理由 |
|---|---|---|---|---|---|---|---|---|---|
| 79 | `proactive_friend_enable` | boolean | `False` | `llm_proactive/src/manager.py:88-96` | ✅ | 合并 | `trigger.proactive_scope` | `off` | 与 `group_enable` 是"对哪几种会话开口"的一个选择（§3.8） |
| 80 | `proactive_group_enable` | boolean | `False` | `manager.py:88-96` | ✅ | 合并 | `trigger.proactive_scope` | `off` | 同上 |
| 81 | `proactive_friend_sessions` | string_list | `[]` | `manager.py:94,487` | ✅ | 保留 | `trigger.proactive_friend_sessions` | `[]` | QQ 列表，天然 string_list |
| 82 | `proactive_group_sessions` | string_list | `[]` | `manager.py:94,488` | ✅ | 保留 | `trigger.proactive_group_sessions` | `[]` | 群号列表 |
| 83 | `proactive_min_interval_minutes` | number | `30` | `manager.py:138-140` | ✅ | 合并 | `trigger.proactive_interval_minutes` | `"30-900"` | 与 max 是同一个随机区间（`random.randint(min,max)`） |
| 84 | `proactive_max_interval_minutes` | number | `900` | `manager.py:139` | ✅ | 合并 | `trigger.proactive_interval_minutes` | `"30-900"` | 同上 |
| 85 | `proactive_max_unanswered` | number | `3` | `manager.py:186-188` | ✅ | 保留 | `trigger.proactive_max_unanswered` | `3` | 独立语义（0=不限） |
| 86 | `proactive_quiet_hours_start` | number | `1` | `manager.py:99-106` | ✅（支持跨天） | 合并 | `trigger.proactive_quiet_hours` | `"1-7"` | 与 end 是同一个区间 |
| 87 | `proactive_quiet_hours_end` | number | `7` | `manager.py:99-106` | ✅ | 合并 | `trigger.proactive_quiet_hours` | `"1-7"` | 同上 |
| 88 | `proactive_group_idle_minutes` | number | `10` | `manager.py:151-154` | ✅ | 保留 | `trigger.proactive_group_idle_minutes` | `10` | 独立语义（群沉默多久开口） |
| 89 | `proactive_prompt` | textarea | `""` | `manager.py:197-198` | ✅（旧默认=内置模板原文） | 保留 | `trigger.proactive_prompt` | `""` | 主动消息的开口文案模板（触发侧内容，不是提示词装配） |
| 90 | `proactive_prompt_enable` | boolean | `True` | `app/agent/assembly/prompt.py:227`（经链视图 → `proactive_options`） | ✅（属装配概念却归主动消息） | 合并 | `prompt.prompt_nudges`（`proactive` 项） | 含该项 | 段落开关归 `llm_prompt` |
| 91 | `proactive_history_intent_nudge` | boolean | `True` | `prompt.py:242-254` | ✅ | 合并 | `prompt.prompt_nudges`（`history` 项） | 含该项 | 同上 |
| 92 | `proactive_env_prompt_enable` | boolean | `True` | `prompt.py:257,278-279` | ✅ | 合并 | `prompt.prompt_nudges`（`env` 项） | 含该项 | 同上 |
| 93 | `proactive_unresolved_prompt_enable` | boolean | `True` | `prompt.py:258,280-281,290-291` | ✅ | 合并 | `prompt.prompt_nudges`（`unresolved` 项） | 含该项 | 同上 |
| 94 | `proactive_env_intent_nudge` | boolean | `True` | `prompt.py:282-283` | ✅ | 合并 | `prompt.prompt_nudges`（`env` 项） | 含该项 | 与 `env_prompt_enable` 同一条"环境行"，合成一项（§3.7） |
| 95 | `stream_proactive_enabled` | boolean | `False` | `manager.py:322`：`config.get("stream_output") and config.get("stream_proactive_enabled")`，`config = self.module.config`（`:238`，只含 proactive 键） | 🚫条件不可达（恒 False） | 删除 | — | — | 替代：`delivery.mode` 统一管辖（§3.4）；恒 False ⇒ 删除零行为变化 |

### 2.7 `llm_schedule`（3 键；`config_schema.py` L4-L21，默认 `main.py:40-44`）→ 1 进 delivery / 2 进 trigger

| # | 旧键 | 类型 | 旧默认 | 真实读取点 | 判定 | 命运 | 新键 | 新默认 | 理由 |
|---|---|---|---|---|---|---|---|---|---|
| 96 | `schedule_enable` | boolean | `True` | `llm_schedule/src/scheduler.py:99`（`self.module.config`）；工具源 `service.py:28-31`；另 `app/agent/loop/prompt_prep.py:232-234` | ✅ | 保留 | `trigger.schedule_enable` | `True` | 定时任务总闸 |
| 97 | `schedule_prompt` | textarea | `""` | `scheduler.py:457-462` | ✅（旧默认=内置长模板） | 保留 | `trigger.schedule_prompt` | `""` | 定时开口文案模板（与 proactive 模板**不合并**：两者占位符与语气不同） |
| 98 | `stream_scheduled_enabled` | boolean | `False` | `scheduler.py:291-293`（与 agent `stream_output` 相与） | 🚫条件不可达 | 删除 | — | — | 同 #95：`delivery.mode` 统一管辖 |

### 2.8 `llm_tools_extra`（schema 5 键 / 持有 10 键；`config_schema.py` L8-L38，默认 `main.py:49-60`）→ **能力模块，全部保留(不动)**

| # | 旧键 | 类型 | 旧默认 | 真实读取点 | 判定 | 命运 |
|---|---|---|---|---|---|---|
| 99 | `onebot_tools_enable` | boolean | `True`（旧 False，**安全相关反转**） | `src/service.py:47-50`；`src/onebot/security.py:33-36` | ✅ | 保留(不动)（建议把默认改回 False，§7.5-5） |
| 100 | `onebot_tools_allowed` | list | `[]` | `security.py:37-38,62-64` | ✅（插件页不可见） | 保留(不动) |
| 101 | `onebot_tools_denied` | list | `[]` | `security.py:36-37,59-60` | ✅（插件页不可见） | 保留(不动) |
| 102 | `onebot_tools_max_result` | number | `2000` | `src/onebot/executor.py:146-152`：`getattr(runtime,"config").get(...)` | 🚫读错来源（改了不生效） | 保留(不动)，**落地要求改读点**（§8-3） |
| 103 | `onebot_tools_debug` | boolean | `False` | `executor.py:121-130`（`policy_get`） | ✅（插件页不可见） | 保留(不动) |
| 104 | `onebot_tool_overrides` | map | `{}` | `security.py:8-21,44-50` | ✅（插件页不可见） | 保留(不动) |
| 105 | `poke_cooldown_seconds` | number | `20` | `executor.py:64-72`（`policy_get`） | ✅ | 保留(不动) |
| 106 | `mcp_servers` | textarea(JSON) | `[]` | `src/mcp.py:161-190` | ✅ | 保留(不动) |
| 107 | `tool_result_directive_enable` | boolean | `True` | `app/agent/loop/tool_loop.py:56-69` ← `app/agent/tools/spec.py:402-407`（`policy_config(runtime,"tools")`） | ✅ | 保留(不动) |
| 108 | `tool_result_directive` | textarea | `""` | `tool_loop.py:63-69` | ✅ | 保留(不动) |

### 2.9 `llm_enhance`（1 键；`config_schema.py` L6-L16，默认 `main.py:25-27`）→ 进 `llm_prompt`

| # | 旧键 | 类型 | 旧默认 | 真实读取点 | 判定 | 命运 | 新键 | 新默认 | 理由 |
|---|---|---|---|---|---|---|---|---|---|
| 109 | `debug_prompt` | boolean | `False` | `llm_enhance/main.py:38-39` → `app/agent/loop/turn_metrics.py:326-348`（`_log_debug_prompt`） | ✅生效，但归属表判它"已删除"（`config_partition.py:404-409`）→ 迁移会静默丢值 | 合并 | `prompt.prompt_record` | `off` | 与 `prompt_dump_*` 是同一需求（落文件 vs 落日志），合成四态（§3.3） |

---

## 3. 合并裁决与语义映射（8 组）

> 通用裁决原则（写进迁移脚本注释，也是验收口径）：
> **(R1) 迁移不改变可观察行为**：旧代码读哪个 config 对象、解成什么词，就按那个结果映射；用户的"本意"留给设置页。
> **(R2) 词表以代码为准**：新 enum 的 option id 必须等于代码 `if/elif` 里认的字符串，不允许出现"界面上有、代码不认"的选项。
> **(R3) 一词一源**：合并后的键只允许有一个模块持有、一处读取（`policy_view` 链不再需要靠顺序决定胜者）。
> **(R4) 有损必留痕**：任何丢掉用户显式值的映射，必须写迁移 warning + 保留原始 `bindings.config_json` 备份。

### 3.1 时间行 2 键 → `prompt.time_position`（三态）

旧事实（`llm_message_meta/src/service.py:82,96,107-111,145-146`）：

- **有没有**由 `include_time` 决定（唯一有效开关）；
- **放哪**由 `prompt_time_position` 决定，但那段读的是 `runtime.config`（agent 层），且 `app/agent/assembly/prompt.py:167-170` **只认 `head`，其余（含 `off`）一律当 `tail`**；
- `off` 从未生效（`prompt.py:170` 把 off 解成 tail，`service.py:108` 只看 `include_time`）。

| 旧组合 | 旧实际行为 | 新 `time_position` |
|---|---|---|
| `include_time=False`（任意位置值） | 不注入时间行 | `off` |
| `include_time=True` 且生效位置=`head` | 时间行在消息头 | `head` |
| `include_time=True` 且生效位置=`tail`/`user_tail`/`off`/非法 | 时间行在尾部 | `tail` |
| `include_time` 缺省（True） | 尾部（同第三行） | `tail` |

裁决：**`off` 语义归"显隐"，`head/tail` 归"位置"**，一个三态表达两件事；旧 `off`（位置枚举里那个）与 `include_time=False` 在迁移时统一落到新 `off`。旧 `user_head` 按 **R1** 映射成 `tail`（因为代码就是这么解），并在迁移日志里提示"原 user_head 从未生效，如需头置请重新选择"。

### 3.2 本轮消息格式 4 键 → `prompt.user_line_format`（枚举）+ 图例推导

旧真实词表（`app/agent/session/model.py:580-609` + `llm_message_meta/src/service.py:91-163`）：

| 位置 | 代码认的值 | 行为 |
|---|---|---|
| `single_line`（`prompt.py:161-164`；渲染侧 `service.py:95` 读 agent 配置） | 只要 ≠ `legacy` 就是单行 | `昵称(QQ): 正文`，时间挪到尾部，六行块消失 |
| `sender_style`（`service.py:91,129-134`） | `single` / `new` / 其余=legacy | `single`=内联到正文行；`new`=`发送者昵称：`；其余=`发送者：` |
| `sent_style`（`service.py:92`→`model.py:599-602`） | `new` / 其余 | `new`=`消息正文：`；其余=`发送了：` |

新枚举（3 态，与代码可区分状态**一一对应**）：

| 新值 | 含义（UI 标签） | 代码对应 | 旧组合映射 |
|---|---|---|---|
| `single_line` | 单行（推荐）：`昵称(QQ): 正文`，时间在尾 | `single_line=True`（sender 被强制 `single`，sent_style 被忽略：`service.py:97-98` + `model.py:599-600` 的 if 在前） | `user_message_format` ∈ {default, single_line, labeled} **或** agent 生效值 ≠ legacy |
| `multi_line` | 六行块·旧标签 | `single_line=False` + sender=`legacy` + sent=`legacy` | `user_message_format=legacy` 且两个 style 都不是 `new` |
| `multi_line_verbose` | 六行块·新标签（`发送者昵称：` / `消息正文：`） | `single_line=False` + 任一 style=`new` | `user_message_format=legacy` 且 sender 或 sent 为 `new` |

裁决：
- **优先级**：`user_message_format`（档位）最高 → 决定单行/多行；两轴 style 只决定多行标签组。旧代码就是这么判的（`service.py:95-98` 先定 `single_line`，再 `if single_line: sender_style="single"`）。
- **有损点（R4 记录）**：`sender=new, sent=legacy` 与 `sender=legacy, sent=new` 两种混合组合会被归到 `multi_line_verbose`，只影响标签措辞、不影响信息量；迁移日志记一条。
- **图例（`meta_instruction_mode`）不再单独开关**：`builtin_sections.py:245-254` 本来就是"格式决定图例文案"，新模型里 `meta_legend` 只剩"要不要图例"一个比特，归 `prompt_nudges`（§3.7）；`auto`/`always` 等价（都注入）→ 映射为"选中 `meta_legend`"，`off` → 不选。

### 3.3 记录本轮请求 3 键 → `prompt.prompt_record`（四态）

| 旧组合 | 旧行为 | 新值 |
|---|---|---|
| `prompt_dump_enable=False` + `debug_prompt=False` | 不记录 | `off` |
| `prompt_dump_enable=True` + `debug_prompt=False` | 写滚动文件（`assembly/dump.py:45,51`，WebUI 在 `app/webui/api/prompt_dump.py` 看） | `file` |
| `prompt_dump_enable=False` + `debug_prompt=True` | 写日志（`turn_metrics.py:326-348`） | `log` |
| 两者都 True | 两份都有 | `both` |

裁决：**两个出口保留、一个开关选择**（不是删掉一个功能）；`prompt_dump_max_entries` 常量内化 20（= 模块默认），若旧值 ≠ 20 记 warning。`debug_prompt` 同时从 `config_partition.DELETED_KEYS`（`config_partition.py:404-409`）移出，改为归 `llm_prompt` 持有（否则迁移会静默丢值）。

### 3.4 输出形态 3 键 → `delivery.mode`（只留 mode）

| 旧键/来源 | 旧默认 | 旧作用 | 裁决 |
|---|---|---|---|
| `mode` | `speech` | 唯一事实源（`output_mode.py:38-62` → `service.py:43,73-79`） | **保留为本模块唯一形态键** |
| `stream_output` | `False` | `turn.py:307`（模块内轮次默认流式）、`capabilities.py:81-83`（无人声明时兜底） | **仅迁移**：只在从未保存过 `mode` 的老账号上参与推导（沿用 `output_mode.py:50-56` 的现有实现，见 §6.2） |
| `speech_via_tool` | `True`（隐藏键，用户改不到） | 被 `OutputOptionsView.get` 覆写成 `mode=="speech"`（`output_mode.py:73-75`）；但 `llm_output` 被禁用时回落到 agent 默认 True，与 `BlockingOutput` 打架（01 §2 第 1 行） | **仅迁移**：推导输入，不再作为独立配置存在 |
| `stream_proactive_enabled` | `False` | `manager.py:322` 与**自己模块**里的 `stream_output`（永远不存在）相与 → 恒 False | **删除**，替代＝`mode` |
| `stream_scheduled_enabled` | `False` | `scheduler.py:291-293` 同构 → 恒 False | **删除**，替代＝`mode` |

裁决：**四个入口（chat / proactive / schedule / llm_turn）统一由 `delivery.mode` 决定形态**（=TurnPlan.speak_mode 的唯一来源）。
行为变化声明（唯一一处，且是修复）：旧账号 `mode=stream` 时，主动/定时消息**从"从不流式"变成"流式"**——这正是那两个键本来的产品意图，属修复而非回归；`mode≠stream` 的账号零变化。

### 3.5 流式节奏 9 + 队列/前后缀 5 + 主动定时 2 → 5 键 + 常量

旧链（三处默认值打架，01 §5-6）：`config.py:77-93` 旧默认 / 模块 `default_config` / `app/domain/pace.py:21-44` 代码 fallback。

**新 `delivery.stream_pace` 枚举 = 代码真实分支（`pace.py:14-44`）**：

| 新值 | 代码路径 | 旧等价配置 |
|---|---|---|
| `off` | `:16-17` 直接返回 0 | `interval_mode=none`（旧默认） |
| `fixed` | `:25-26` `delay=base` | `interval_mode=fixed` |
| `sqrt` | `:28-29` `base + k*sqrt(len)` | `interval_mode=length_curve` + `curve=sqrt` |
| `log` | `:30-31` `base + k*log1p(len)` | **旧 schema 无此选项**（代码已支持，属"补暴露"，见 §7.3） |
| `inverse` | `:32-33` `base + k/(1+len)` | 同上 |
| `short_long` | `:34-39` 短句/长句固定延迟 | `curve=short_long` |
| —（删除的选项） | `curve=none` → 落 `:40-41` else=sqrt；`curve=linear` → 同 else | 两者都是"词表失配"，新枚举不再提供 |

**参数裁决**：

| 旧键 | 旧默认 | 新落点 | 有损？ |
|---|---|---|---|
| `stream_send_interval_base_ms` | `0` | `stream_pace_base_ms`（`fixed`/`sqrt`/`log`/`inverse` 用） | 否 |
| `stream_send_interval_max_ms` | `0`（0=不限） | `stream_pace_max_ms`（`pace.py:43-44` 的夹紧上限） | 否 |
| `stream_send_interval_min_ms` | `0` | 常量 0 | 否（模块默认即 0；代码 fallback 100 被清掉） |
| `stream_send_curve_k` | `1.0` | 常量 1.0（内化进 sqrt/log/inverse） | **是**，仅当用户显式改过 |
| `stream_short_message_length` / `_delay_ms` / `stream_long_message_delay_ms` | `10` / `300` / `1200` | `short_long` 预置常量 10/300/1200 | **是**，仅当用户显式改过 |
| `stream_send_max_queue` / `stream_queue_full_policy` / `stream_flush_on_finish` | `20` / `backpressure` / `False` | 常量 20 / `backpressure` / `False` | **是**，仅当用户显式改过 |
| `stream_send_prefix` / `stream_send_suffix` | `""` / `""` | 原样保留两键 | 否 |
| `stream_send_by_sentence` / `stream_keep_order` | `True` / `True` | **删除** | 行为本就恒定（§0-4），无损 |

裁决：**节奏只保留"档 + 两个数字"**（这是"同一件事"的最小可解释面），其余参数收进常量/预设；**若产品坚持保留细调**，唯一不破 ≤12 的办法是把 `stream_pace_base_ms/max_ms` + 7 个参数收进一个 `type: "dynamic"` 的 map 键（现成控件 `DynamicWidget.vue`），键数仍 12 但 UX 变差——**本轮选择"少而准"**。

### 3.6 元信息项 6 + 2 抓取键 → 4 键（同轴合并）

旧渲染（`service.py:103-163`）的真实轴只有 4 条，逐条合并：

| 轴 | 旧键 | 新键 | 选项（代码词表） | 映射表 |
|---|---|---|---|---|
| 发送者 | `include_sender`、`include_private_qq` | `prompt.meta_sender` | `off` / `name` / `name_and_qq` | `include_sender=False & private_qq=False`→`off`；`private_qq=True`→`name_and_qq`；否则`name` |
| @ 提及 | `include_mentioned`、`fetch_at_nickname` | `prompt.meta_mention` | `off` / `qq` / `nickname` | `include_mentioned=False`→`off`；`fetch_at_nickname=False`→`qq`；否则`nickname` |
| 引用 | `include_quote`、`include_quote_sender`、`fetch_quote_content` | `prompt.meta_quote` | `off` / `ref_only` / `plain` / `with_sender` | `include_quote=False`→`off`；`fetch_quote_content=False`→`ref_only`（`service.py:231-268` 只显示"引用了某条"）；`include_quote_sender=False`→`plain`；否则`with_sender` |
| 正文 | `include_sent` | `prompt.meta_include_body` | boolean | 原样 |

裁决：**同一条渲染链上的"显不显示 + 要不要反查"合成一个词表**，避免"两个开关互相否定"（如 `include_quote=False` 时 `fetch_quote_content=True` 仍会多打一次 OneBot API——旧代码 `service.py:139` 的 `_on("include_quote")` 先判，所以实际不会；但 UI 上看不出来）。`ref_only` 是新增的第 3 个引用态（旧只能靠 `fetch_quote_content=False` 表达），属"补暴露"。

### 3.7 提示段开关 7 键 → `prompt.prompt_nudges`（多选）

| 段 id | 吸收的旧键 | 旧读取点 | 默认 |
|---|---|---|---|
| `meta_legend` | `meta_instruction_mode`（`off`=不选；其它=选） | `builtin_sections.py:239-257` | 选 |
| `referent` | `referent_prompt_enable` | `prompt.py:260-264` | 选 |
| `proactive` | `proactive_prompt_enable` | `prompt.py:227-228` | 选 |
| `history` | `proactive_history_intent_nudge` | `prompt.py:242-254,274-275` | 选 |
| `env` | `proactive_env_prompt_enable` + `proactive_env_intent_nudge` | `prompt.py:257,278-279,282-283` | 选 |
| `unresolved` | `proactive_unresolved_prompt_enable` | `prompt.py:258,280-281,290-291` | 选 |

裁决：
- **只看"开关"，不看"能力"**：`prompt.py:260-264` 的 `referent` 行同时要求 `referent_resolve_enable`（留在 `llm_context`）→ 保留这个"与"关系：能力模块没开，段自然不出现；本键只表达"用户想不想要这一段"。
- `env` 两项合并的原因：`proactive_env_prompt_enable` 管环境行本体（`prompt.py:257`），`proactive_env_intent_nudge` 管"问到群名/成员时的补强"（`:282`），后者只在前者为真时才有输出（`show_env` 门控），**两个开关在语义上是母子关系**，合成一项无损。
- **丢弃的语义**：`meta_instruction_mode=new`（脱敏图例）不再是选项——图例文案改由 `user_line_format` 推导，`multi_line_verbose` 天然对应脱敏版（`builtin_sections.py:253-254`）。

### 3.8 触发器 13 键 → 10 键

| 新键 | 吸收 | 裁决规则 |
|---|---|---|
| `proactive_scope` | `proactive_friend_enable` + `proactive_group_enable` | `bool×bool → {off, friend, group, both}`；两个独立布尔在 UI 上无法表达"只想私聊"，枚举更准 |
| `proactive_interval_minutes` | `min_interval_minutes` + `max_interval_minutes` | `"30-900"`（分钟区间，`random.randint(min,max)`）；解析失败回默认，且强制 `max≥min`（旧代码 `manager.py:139` 就是这么兜的） |
| `proactive_quiet_hours` | `quiet_hours_start` + `quiet_hours_end` | `"1-7"`（0-23 小时，**支持跨天**：`manager.py:102-106`；`start==end` 视为不静默） |
| 其余 7 键 | 1:1 | `friend_sessions` / `group_sessions` / `group_idle_minutes` / `max_unanswered` / `proactive_prompt` / `schedule_enable` / `schedule_prompt` |

裁决：**范围类参数一律"一个键表达一个区间"**（旧的两个数字必须一起改才有意义，分开摆只会产生 `min>max` 的无效态）。

---

## 4. 删除清单（15 条，每条带替代方案）

> 统一说明：**"删除"= 从新 schema 退役 + 迁移时按表归档，不删用户数据**（`bindings.config_json` 原样保留，另写 `migration_log`）。

### 4.1 僵尸键（全仓 0 读取，3 条）

| # | 旧键 | 证据 | 替代方案（为什么删了不丢功能） |
|---|---|---|---|
| D-1 | `llm_output.stream_send_by_sentence` | 只有 schema `:17`、默认值 `main.py:80`、`config.py:78`、归属表 `:227`；切分恒执行 `app/agent/loop/tool_step.py:309`（`split_sentences(buffer, max_length=req.max_msg_len)`，无分支） | **补实现没有意义**：句切分是流式通道的固有行为，不存在"不按句发"的第二种实现。**删**，行为不变 |
| D-2 | `llm_output.stream_keep_order` | 同上（`main.py:96`、`config.py:93`、归属表 `:243`）；池是单队列 + 单发送协程（`pool.py:5,39,126`），天然 FIFO | **删**，顺序恒定；若将来要做"并发多队列"，那是新需求的键，不是这个 |
| D-3 | `llm_message_meta.meta_mask_nickname` | 本插件 0 读取；`llm_proactive/src/manager.py:206`、`llm_schedule/src/scheduler.py:470` 读的是**自己模块**的配置（永远 False）；主聊天历史 `turn_metrics.py:383-391` 硬编码 True | **删**，替代＝"恒脱敏"（主聊天现状）。主动/定时合并进 `llm_trigger` 后走同一渲染，自动一致，不再需要一个永远读不到的开关 |

### 4.2 常量内化（值＝原默认，9 条）

> 全部只在 `stream_send_pool_enabled=True` 时被读（`pace.py`/`pool.py`），而默认 `False`→ 默认状态下这些键**本来就是装饰**（01 §1.3）。
> 有损判定：仅当用户在旧配置里**显式改过**才有感知；迁移脚本对这些键逐个比对默认值，非默认值写 `warning` 并保留备份。

| # | 旧键 | 内化值 | 替代方案 |
|---|---|---|---|
| D-4 | `stream_send_interval_min_ms` | `0` | 由 `stream_pace_max_ms` + 常量下限 0 表达；旧默认即 0，代码 fallback 100 一并清掉（消除第三套默认） |
| D-5 | `stream_send_curve_k` | `1.0` | 内化进 `sqrt/log/inverse` 三档（k=1.0＝模块默认）；需要更强节奏请用 `base_ms` |
| D-6 | `stream_short_message_length` | `10` | `stream_pace=short_long` 预置 |
| D-7 | `stream_short_message_delay_ms` | `300` | 同上 |
| D-8 | `stream_long_message_delay_ms` | `1200` | 同上 |
| D-9 | `stream_send_max_queue` | `20` | 队列容量是工程参数，固定 20（= 模块默认） |
| D-10 | `stream_queue_full_policy` | `backpressure` | 固定"背压不丢消息"（唯一安全选择） |
| D-11 | `stream_flush_on_finish` | `False` | 固定 False（= 模块默认；改 True 属行为变更，另案处理） |
| D-12 | `llm_assembly.prompt_dump_max_entries` | `20` | 滚动上限固定 20（= 模块默认），`prompt_record` 只留"写哪儿" |

### 4.3 重复事实源 / 条件不可达（3 条，其中 1 条为"仅迁移"）

| # | 旧键 | 证据 | 替代方案 |
|---|---|---|---|
| D-13 | `llm_output.stream_output` | `turn.py:307`、`capabilities.py:81-83`；`mode` 已是唯一事实源（`output_mode.py:38-62`） | **仅迁移**：值用于推导 `mode`（§6.2），之后不再读；schema 不再暴露 |
| D-14 | `llm_proactive.stream_proactive_enabled` | `manager.py:322` 与 `self.module.config` 里的 `stream_output` 相与 → 恒 False | 替代＝`delivery.mode`（`stream` ⇒ 主动消息也流式）；恒 False ⇒ 删除零行为变化 |
| D-15 | `llm_schedule.stream_scheduled_enabled` | `scheduler.py:291-293` 同构 → 恒 False | 同上 |

### 4.4 表外（不计入 109 的）清理项

| 项 | 位置 | 处理 |
|---|---|---|
| `speech_via_tool`（隐藏键） | `config_partition.py:244`、`llm_output/main.py:97` | 从归属表删除；迁移期作为 `mode` 推导输入（§6.2）；`OutputOptionsView` 的覆写逻辑（`output_mode.py:73-75`）保留到 `llm_delivery` 落地后改为读 `mode` |
| 3 个 agent 侧幽灵键 `assembly_profile` / `behavior_rules_enable` / `speech_fallback_enable` | 全仓 0 读取（仅存档副本与 `tests/webui/test_agent_config_save_guard.py:165` 的"已删除"断言出现） | 不在 109 内；迁移时直接清出 `agent` 配置，不写入任何新键 |
| `llm_knowledge` 的 3 键（`knowledge_enable/_embedding_model_id/_recall_limit`） | `config_partition.py:307-311`，无插件持有 | 不在 109 内；从归属表删除（或另立插件，§7.5-4） |
| `referent_prefetch_item_chars`（孤儿键） | `llm_context/src/referent.py:281` 有代码、schema/归属表都没有 | 不在 109 内；建议补进 `llm_context` schema（本轮不动，属能力模块自身修复） |

---

## 5. 三份 `config_schema` 草稿

> 全部使用**当前 WebUI 已支持的 type**（`boolean/number/select/string/textarea/string_list/group/resource/dynamic`，见 `app/webui/dashboard/src/components/config/ConfigForm.vue:151,208-230` 与 `FieldControl.vue:20-30`）。
> `showIf` 语法与后端一致：`{key,value}` / `{key,in:[…]}` / `{key,notIn:[…]}` / `{key,gt:n}` / `{key,notEmpty:true}`（`app/webui/dashboard/src/components/config/ConfigForm.vue:123-142` 的 `matchesCondition`）。
> 组标签用 `group` 类型（不计入键数）。

### 5.1 `app/plugins/llm_prompt/config_schema.py`（12 键）

```python
"""提示词组装与渲染模块：行为规则 / 本轮消息渲染 / 段开关 / 请求记录。共 12 键。"""

SCHEMA = {
    # ── 组：行为与语气 ─────────────────────────────
    "group_behavior": {"type": "group", "label": "行为与语气", "collapsible": True},
    "behavior_rules_override": {
        "type": "textarea",
        "label": "覆盖行为规则",
        "description": "留空用内置行为规则；填写后整段替换（原样拼进 L1）。",
        "default": "",
        "group": "group_behavior",
    },
    "participation": {
        "type": "select",
        "label": "参与度",
        "description": "影响行为规则段的语气与主动程度。",
        "default": "normal",
        "options": {"low": "安静（少插话）", "normal": "正常", "high": "活跃（多接话）"},
        "group": "group_behavior",
    },
    "emoji_react_level": {
        "type": "number",
        "label": "贴表情活跃度",
        "description": "0 = 不贴、也不注入【可用表情】段；1 = 每 3~5 轮；2 = 优先贴；3 = 能贴就贴。",
        "default": 1,
        "min": 0,
        "max": 3,
        "group": "group_behavior",
    },

    # ── 组：本轮消息渲染 ───────────────────────────
    "group_turn_render": {"type": "group", "label": "本轮消息渲染", "collapsible": True},
    "time_position": {
        "type": "select",
        "label": "时间行",
        "description": "当前时间放在本轮消息的哪里；选『不注入』则模型看不到现在几点。",
        "default": "tail",
        "options": {"tail": "放在末尾（推荐：稳定行靠前，利于前缀缓存）",
                    "head": "放在开头",
                    "off": "不注入时间"},
        "group": "group_turn_render",
    },
    "user_line_format": {
        "type": "select",
        "label": "本轮消息格式",
        "description": "模型看到的『本轮 user 消息』长什么样。多行块会带六行元信息（时间/群号/发送者/提到了/引用了）。",
        "default": "single_line",
        "options": {
            "single_line": "单行：昵称(QQ): 正文（推荐）",
            "multi_line": "多行块（旧标签：发送者：/ 发送了：）",
            "multi_line_verbose": "多行块（新标签：发送者昵称：/ 消息正文：）",
        },
        "group": "group_turn_render",
    },
    "meta_sender": {
        "type": "select",
        "label": "发送者信息",
        "description": "是否给出『谁说的』。『带 QQ』会在私聊里额外加一行 (QQ: …)。",
        "default": "name",
        "options": {"off": "不注入", "name": "昵称", "name_and_qq": "昵称 + QQ"},
        "group": "group_turn_render",
    },
    "meta_mention": {
        "type": "select",
        "label": "@ 提及",
        "description": "列出本条消息 @ 了谁。『反查昵称』会多打一次 OneBot API（关掉省一次调用，只显示 QQ 号）。",
        "default": "nickname",
        "options": {"off": "不注入", "qq": "只显示 QQ 号（省一次 API）", "nickname": "反查昵称"},
        "group": "group_turn_render",
    },
    "meta_quote": {
        "type": "select",
        "label": "引用消息",
        "description": "本条消息引用了哪条。『只标注』不拉正文（省一次 API）；『带发送者』多一行引用者。",
        "default": "with_sender",
        "options": {"off": "不注入", "ref_only": "只标注『引用了某条』（不拉正文）",
                    "plain": "拉正文，不带发送者", "with_sender": "拉正文 + 引用者"},
        "group": "group_turn_render",
    },
    "meta_include_body": {
        "type": "boolean",
        "label": "附带原始正文",
        "description": "附带本条消息的原始正文，便于模型区分元信息与正文。",
        "default": True,
        "group": "group_turn_render",
    },
    "clean_output_parentheses": {
        "type": "boolean",
        "label": "历史里剥离括号动作",
        "description": "剥离助手历史输出里的（…），阻止括号风格自我强化；只影响进历史的文本，不影响对外展示。",
        "default": True,
        "group": "group_turn_render",
    },

    # ── 组：提示段开关 ─────────────────────────────
    "group_blocks": {"type": "group", "label": "提示段开关", "collapsible": True},
    "prompt_nudges": {
        "type": "string_list",
        "label": "要注入的提示段",
        "description": (
            "多选，每行填一个 id，留空=全部关闭。可选："
            "meta_legend（消息格式图例：告诉模型冒号前是元信息）、"
            "referent（当前对话焦点行：回指『那个/上一条』）、"
            "proactive（主动性协议段）、"
            "history（用户问『刚才/之前』时提醒先查记录）、"
            "env（环境查询提示：群名/成员/会话信息）、"
            "unresolved（有未展开内容时提醒按需展开）。"
        ),
        "default": ["meta_legend", "referent", "proactive", "history", "env", "unresolved"],
        "group": "group_blocks",
    },

    # ── 组：记录本轮请求（调试）────────────────────
    "group_record": {"type": "group", "label": "记录本轮请求（调试）", "collapsible": True},
    "prompt_record": {
        "type": "select",
        "label": "记录本轮请求",
        "description": "把每轮最终发给模型的完整内容留下来。文件＝滚动 JSON（设置页可查看/比对）；日志＝写进运行日志（截断）。默认关闭。",
        "default": "off",
        "options": {"off": "不记录", "file": "写文件", "log": "写日志", "both": "两者都写"},
        "group": "group_record",
    },
}

#: 本模块持有的键（供 config_partition.MODULE_KEYS / 迁移校验使用；不含 group 标签）
OWNED_KEYS = tuple(k for k, v in SCHEMA.items() if v.get("type") != "group")   # len == 12
```

### 5.2 `app/plugins/llm_delivery/config_schema.py`（12 键）

```python
"""发言与输出模块：输出形态 / 发言通道 / 流式节奏 / 唯一发送出口。共 12 键。"""

SCHEMA = {
    # ── 组：输出形态 ───────────────────────────────
    "group_mode": {"type": "group", "label": "输出形态", "collapsible": True},
    "mode": {
        "type": "select",
        "label": "输出形态",
        "description": (
            "speech = 模型输出的文本只是思考，发言靠 send_message（默认）；"
            "stream = 按句流式发出；text = 一次发完。"
            "该设置对本账号的聊天、主动消息、定时任务**统一生效**。"
        ),
        "default": "speech",
        "options": {"speech": "发言通道（默认）", "stream": "流式", "text": "一次发完"},
        "group": "group_mode",
    },

    # ── 组：发言通道 ───────────────────────────────
    "group_speech": {
        "type": "group", "label": "发言通道", "collapsible": True,
    },
    "speech_max_bubbles": {
        "type": "number", "label": "一轮最多几个气泡",
        "description": "发言通道下一轮对话里最多发几条。",
        "default": 5, "min": 1, "max": 20,
        "showIf": {"key": "mode", "value": "speech"},
        "group": "group_speech",
    },
    "speech_max_bubble_chars": {
        "type": "number", "label": "单条气泡字数上限",
        "description": "单个气泡的最大字符数，超了会拆条。",
        "default": 200, "min": 10,
        "showIf": {"key": "mode", "value": "speech"},
        "group": "group_speech",
    },
    "speech_reminder_rounds": {
        "type": "number", "label": "提前提醒轮数",
        "description": "剩余轮次不足该值时提醒模型尽快开口（0 = 不提醒）。",
        "default": 2, "min": 0, "max": 10,
        "showIf": {"key": "mode", "value": "speech"},
        "group": "group_speech",
    },
    "speech_force_reply_on_call": {
        "type": "boolean", "label": "@ / 私聊时补偿一轮",
        "description": "@、私聊、拍一拍时若一句话没说，额外补一轮让它开口。",
        "default": True,
        "showIf": {"key": "mode", "value": "speech"},
        "group": "group_speech",
    },

    # ── 组：流式发送 ───────────────────────────────
    "group_stream": {"type": "group", "label": "流式发送", "collapsible": True},
    "stream_sentence_max_length": {
        "type": "number", "label": "单条流式消息最大字数",
        "description": "按句切分后，每条消息的最大字符数（超出继续切）。",
        "default": 200, "min": 10,
        "showIf": {"key": "mode", "value": "stream"},
        "group": "group_stream",
    },
    "stream_send_pool_enabled": {
        "type": "boolean", "label": "启用有序发送池",
        "description": "开启后流式句子进入有序队列，按下面的节奏发送；关闭则边生成边发。",
        "default": False,
        "showIf": {"key": "mode", "value": "stream"},
        "group": "group_stream",
    },
    "stream_pace": {
        "type": "select",
        "label": "发送节奏",
        "description": (
            "相邻两条流式消息之间的等待。『不等待』= 立刻连续发；『固定』= 每条固定间隔；"
            "其余按字数计算（长句多等）。仅在启用发送池时生效。"
        ),
        "default": "off",
        "options": {
            "off": "不等待",
            "fixed": "固定间隔",
            "sqrt": "按字数·平方根（推荐的自然节奏）",
            "log": "按字数·对数（增长更平缓）",
            "inverse": "按字数·反比（长句等得少）",
            "short_long": "按长短句（短句/长句两档固定延迟）",
        },
        "showIf": {"key": "stream_send_pool_enabled", "value": True},
        "group": "group_stream",
    },
    "stream_pace_base_ms": {
        "type": "number", "label": "基础间隔（毫秒）",
        "description": "固定间隔或曲线的基准延迟；『按长短句』不使用该值。",
        "default": 0, "min": 0, "max": 60000,
        "showIf": {"key": "stream_pace", "in": ["fixed", "sqrt", "log", "inverse"]},
        "group": "group_stream",
    },
    "stream_pace_max_ms": {
        "type": "number", "label": "间隔上限（毫秒）",
        "description": "最终延迟的夹紧上限，0 = 不限制。",
        "default": 0, "min": 0, "max": 120000,
        "showIf": {"key": "stream_pace", "in": ["fixed", "sqrt", "log", "inverse", "short_long"]},
        "group": "group_stream",
    },
    "stream_send_prefix": {
        "type": "string", "label": "每条消息前缀",
        "description": "每条流式消息发送前追加的文本，留空不加。",
        "default": "",
        "showIf": {"key": "stream_send_pool_enabled", "value": True},
        "group": "group_stream",
    },
    "stream_send_suffix": {
        "type": "string", "label": "每条消息后缀",
        "description": "每条流式消息发送后追加的文本，留空不加。",
        "default": "",
        "showIf": {"key": "stream_send_pool_enabled", "value": True},
        "group": "group_stream",
    },
}

OWNED_KEYS = tuple(k for k, v in SCHEMA.items() if v.get("type") != "group")   # len == 12
```

### 5.3 `app/plugins/llm_trigger/config_schema.py`（10 键）

```python
"""主动与定时模块：主动消息（私聊/群聊）与到点任务。共 10 键。"""

SCHEMA = {
    # ── 组：主动消息 ───────────────────────────────
    "group_proactive": {"type": "group", "label": "主动消息", "collapsible": True},
    "proactive_scope": {
        "type": "select",
        "label": "对哪些会话主动开口",
        "description": "私聊按随机间隔主动发言；群聊在沉默一段时间后主动开口。",
        "default": "off",
        "options": {"off": "关闭", "friend": "仅私聊", "group": "仅群聊", "both": "私聊 + 群聊"},
        "group": "group_proactive",
    },
    "proactive_friend_sessions": {
        "type": "string_list",
        "label": "私聊对象（QQ 号）",
        "description": "对哪些 QQ 主动发言，每行一个。",
        "default": [],
        "showIf": {"key": "proactive_scope", "in": ["friend", "both"]},
        "group": "group_proactive",
    },
    "proactive_group_sessions": {
        "type": "string_list",
        "label": "群聊对象（群号）",
        "description": "对哪些群主动发言，每行一个。",
        "default": [],
        "showIf": {"key": "proactive_scope", "in": ["group", "both"]},
        "group": "group_proactive",
    },
    "proactive_interval_minutes": {
        "type": "string",
        "label": "私聊间隔区间（分钟）",
        "description": "两次主动发言之间的随机等待区间，格式『最短-最长』，例如 30-900。最长小于最短时按最短处理。",
        "default": "30-900",
        "showIf": {"key": "proactive_scope", "in": ["friend", "both"]},
        "group": "group_proactive",
    },
    "proactive_quiet_hours": {
        "type": "string",
        "label": "免打扰时段（小时）",
        "description": "该时段内不主动发言，格式『起始小时-结束小时』（0-23），例如 1-7；支持跨天如 22-6；起止相同=不生效。被 @ 等被动回复不受影响。",
        "default": "1-7",
        "group": "group_proactive",
    },
    "proactive_group_idle_minutes": {
        "type": "number",
        "label": "群沉默多久后开口（分钟）",
        "description": "群里安静超过该时长后主动开口。",
        "default": 10, "min": 1,
        "showIf": {"key": "proactive_scope", "in": ["group", "both"]},
        "group": "group_proactive",
    },
    "proactive_max_unanswered": {
        "type": "number",
        "label": "连续无人接话即停止",
        "description": "连续 N 次主动发言无人接话就停止，0 = 不限。",
        "default": 3, "min": 0,
        "group": "group_proactive",
    },
    "proactive_prompt": {
        "type": "textarea",
        "label": "主动开口提示词",
        "description": "留空用内置模板；支持 {{current_time}}、{{unanswered_count}} 占位。",
        "default": "",
        "group": "group_proactive",
    },

    # ── 组：定时任务 ───────────────────────────────
    "group_schedule": {"type": "group", "label": "定时任务", "collapsible": True},
    "schedule_enable": {
        "type": "boolean",
        "label": "启用定时任务",
        "description": "允许模型/你创建到点触发的任务。",
        "default": True,
        "group": "group_schedule",
    },
    "schedule_prompt": {
        "type": "textarea",
        "label": "定时触发提示词",
        "description": "留空用内置模板（让它像真人一样自然开口）。",
        "default": "",
        "group": "group_schedule",
    },
}

OWNED_KEYS = tuple(k for k, v in SCHEMA.items() if v.get("type") != "group")   # len == 10
```

---

## 6. 迁移规则（函数级伪代码）

### 6.1 取值顺序（R1 的落地）

```python
# 旧代码的"生效值"取决于读的是哪个 config 对象（02 §5.1）：
#   模块视图（config_view/policy_config）→ 模块 stored_config() 优先，agent 兜底
#   runtime.config（被旁路/读错源）      → 只有 agent 旧值（模块值从不生效）
def old_effective(module_stored: dict, agent_cfg: dict, key: str, default, *, bypassed: bool = False):
    if not bypassed:
        if key in module_stored and module_stored[key] is not None:
            return module_stored[key]
    return agent_cfg.get(key, default)
```

### 6.2 `llm_delivery.mode`

```python
def migrate_mode(out_stored: dict, agent: dict) -> tuple[str, list[str]]:
    """复用现有实现 app/plugins/llm_output/src/output_mode.py:38-62 的语义。"""
    warn = []
    raw = str(out_stored.get("mode") or "").strip().lower()
    if raw in ("text", "stream", "speech"):          # 用户显式存过 mode：直接用
        return raw, warn
    # 从未保存过 mode：按旧开关推导（output_mode.py:50-56）
    if "speech_via_tool" in out_stored:
        if out_stored["speech_via_tool"]:
            return "speech", warn
        return ("stream" if out_stored.get("stream_output") else "text"), warn
    if "stream_output" in out_stored:
        return ("stream" if out_stored["stream_output"] else "text"), warn
    # 模块存档里也没有 → agent 旧值（speech_via_tool 在 agent 层默认 True）
    if bool(agent.get("speech_via_tool", True)):
        return "speech", warn
    return ("stream" if agent.get("stream_output") else "text"), warn
```

> 边界提醒（01 §4.1 补充事实）：`ModuleConfig.set/update` 会把含默认值的 `raw_config` 整份落库（`app/plugin/base.py:64-78`），所以**只要用户在旧插件页点过一次保存**，`mode` 就已经显式存在 → 推导分支不会走。反过来，`llm_assembly`/`llm_message_meta`/`llm_context` 在账号 123 上全是 `{}`，说明"用户从没存过"是常态，推导分支很重要。

### 6.3 `llm_prompt`

```python
def migrate_prompt(agent: dict, old: dict) -> tuple[dict, list[str]]:
    assembly = old.get("llm_assembly", {})        # stored_config()
    meta     = old.get("llm_message_meta", {})
    enhance  = old.get("llm_enhance", {})
    context  = old.get("llm_context", {})
    pro      = old.get("llm_proactive", {})
    warn: list[str] = []

    # ── P4 时间三态（§3.1）──────────────────────────────
    include_time = old_effective(meta, agent, "include_time", True)
    # 位置由渲染侧读 runtime.config 决定（service.py:82,96），且代码只认 head
    pos_raw = str(agent.get("prompt_time_position") or "tail").strip().lower()
    time_position = "off" if not include_time else ("head" if pos_raw == "head" else "tail")
    if include_time and str(assembly.get("prompt_time_position") or "").lower() in ("head", "user_head"):
        warn.append("prompt_time_position 模块值从未生效（渲染侧读 agent 配置），已按旧行为映射为 tail")

    # ── P5 本轮格式（§3.2）──────────────────────────────
    fmt_raw = str(old_effective(assembly, agent, "user_message_format", "single_line",
                                bypassed=True)).lower()   # 渲染侧读 runtime.config → 取 agent
    sender_style = str(meta.get("meta_sender_style") or "legacy").lower()
    sent_style   = str(meta.get("meta_sent_style") or "legacy").lower()
    if fmt_raw != "legacy":
        user_line_format = "single_line"
    elif "new" in (sender_style, sent_style):
        user_line_format = "multi_line_verbose"
        if {sender_style, sent_style} - {"new", "legacy"}:
            warn.append(f"meta_sender_style={sender_style}/meta_sent_style={sent_style} 不在代码词表，按 legacy 处理")
    else:
        user_line_format = "multi_line"

    # ── P6/P7/P8 元信息四轴（§3.6）───────────────────────
    inc_sender = bool(old_effective(meta, agent, "include_sender", True))
    inc_qq     = bool(old_effective(meta, agent, "include_private_qq", False))
    meta_sender = "off" if (not inc_sender and not inc_qq) else ("name_and_qq" if inc_qq else "name")

    inc_mention = bool(old_effective(meta, agent, "include_mentioned", True))
    fetch_at    = bool(old_effective(meta, agent, "fetch_at_nickname", True))
    meta_mention = "off" if not inc_mention else ("nickname" if fetch_at else "qq")

    inc_quote  = bool(old_effective(meta, agent, "include_quote", True))
    inc_qsndr  = bool(old_effective(meta, agent, "include_quote_sender", True))
    fetch_q    = bool(old_effective(meta, agent, "fetch_quote_content", True))
    if not inc_quote:            meta_quote = "off"
    elif not fetch_q:            meta_quote = "ref_only"
    elif not inc_qsndr:          meta_quote = "plain"
    else:                        meta_quote = "with_sender"

    # ── P11 记录本轮请求（§3.3）──────────────────────────
    dump = bool(assembly.get("prompt_dump_enable", False))
    dbg  = bool(enhance.get("debug_prompt", False))
    prompt_record = "both" if dump and dbg else "file" if dump else "log" if dbg else "off"
    if int(assembly.get("prompt_dump_max_entries", 20) or 20) != 20:
        warn.append("prompt_dump_max_entries 非默认值，新实现固定 20（原值已备份）")

    # ── P12 段开关（§3.7）────────────────────────────────
    nudges = []
    if str(meta.get("meta_instruction_mode") or "auto").lower() != "off":
        nudges.append("meta_legend")
    if bool(context.get("referent_prompt_enable", True)):            nudges.append("referent")
    if bool(pro.get("proactive_prompt_enable", True)):               nudges.append("proactive")
    if bool(pro.get("proactive_history_intent_nudge", True)):        nudges.append("history")
    if bool(pro.get("proactive_env_prompt_enable", True)) or \
       bool(pro.get("proactive_env_intent_nudge", True)):            nudges.append("env")
    if bool(pro.get("proactive_unresolved_prompt_enable", True)):    nudges.append("unresolved")

    return {
        "behavior_rules_override": assembly.get("behavior_rules_override", ""),
        "participation": {"medium": "normal"}.get(str(assembly.get("participation") or "normal"), "normal"),
        "emoji_react_level": int(assembly.get("emoji_react_level", 0) or 0),
        "time_position": time_position,
        "user_line_format": user_line_format,
        "meta_sender": meta_sender,
        "meta_mention": meta_mention,
        "meta_quote": meta_quote,
        "meta_include_body": bool(old_effective(meta, agent, "include_sent", True)),
        "clean_output_parentheses": bool(old_effective(meta, agent, "clean_output_parentheses", True)),
        "prompt_record": prompt_record,
        "prompt_nudges": nudges,
    }, warn

def migrate_mask_nickname(meta: dict) -> list[str]:
    """D-3：键退役，只留一条留痕（值本身读不到，主聊天硬编码脱敏）。"""
    if "meta_mask_nickname" not in meta:
        return []
    return [f"meta_mask_nickname={meta['meta_mask_nickname']} 从未生效"
            "（本插件 0 读取；主聊天 turn_metrics.py:383-391 硬编码 True）；"
            "新实现恒脱敏，原值已备份"]
```

### 6.4 `llm_delivery`

```python
def migrate_delivery(agent: dict, old: dict) -> tuple[dict, list[str]]:
    out = old.get("llm_output", {})
    pro = old.get("llm_proactive", {})
    sch = old.get("llm_schedule", {})
    warn: list[str] = []

    mode, w = migrate_mode(out, agent); warn += w

    # ── 节奏（§3.5）────────────────────────────────────
    imode = str(out.get("stream_send_interval_mode") or "none").lower()
    curve = str(out.get("stream_send_curve") or "none").lower()
    if imode == "none":
        pace = "off"
    elif imode == "fixed":
        pace = "fixed"
    else:
        pace = {"sqrt": "sqrt", "log": "log", "inverse": "inverse",
                "short_long": "short_long"}.get(curve, "sqrt")   # none/linear/其它 → 代码 else 分支=sqrt
        if curve in ("none", "linear"):
            warn.append(f"stream_send_curve={curve} 无独立实现（代码走 else=sqrt），已迁移为 sqrt")
    if imode not in ("none", "fixed", "length_curve"):
        warn.append(f"stream_send_interval_mode={imode} 不在代码词表，按 none 处理"); pace = "off"

    base_ms = int(out.get("stream_send_interval_base_ms", 0) or 0)
    max_ms  = int(out.get("stream_send_interval_max_ms", 0) or 0)
    # 常量内化的键：非默认值 → warning（R4）
    for k, dflt in (("stream_send_interval_min_ms", 0), ("stream_send_curve_k", 1.0),
                    ("stream_short_message_length", 10), ("stream_short_message_delay_ms", 300),
                    ("stream_long_message_delay_ms", 1200), ("stream_send_max_queue", 20),
                    ("stream_queue_full_policy", "backpressure"), ("stream_flush_on_finish", False)):
        if k in out and out[k] != dflt:
            warn.append(f"{k}={out[k]} 已常量内化（默认 {dflt}），原值备份在 migration_log")
    # 两个恒 False 的键：删掉但要求实现改成读 mode（§3.4）
    for k, src in (("stream_proactive_enabled", pro), ("stream_scheduled_enabled", sch)):
        if bool(src.get(k, False)):
            warn.append(f"{k}=True 但该路径恒为 False（与不存在的 stream_output 相与）；"
                        "新实现由 mode 统一决定，如需主动消息不流式请把 mode 设为 text/speech")

    return {
        "mode": mode,
        "speech_max_bubbles": int(out.get("speech_max_bubbles", 5) or 5),
        "speech_max_bubble_chars": int(out.get("speech_max_bubble_chars", 200) or 200),
        "speech_reminder_rounds": int(out.get("speech_reminder_rounds", 2) or 2),
        "speech_force_reply_on_call": bool(out.get("speech_force_reply_on_call", True)),
        "stream_sentence_max_length": int(out.get("stream_sentence_max_length", 200) or 200),
        "stream_send_pool_enabled": bool(out.get("stream_send_pool_enabled", False)),
        "stream_pace": pace,
        "stream_pace_base_ms": base_ms,
        "stream_pace_max_ms": max_ms,
        "stream_send_prefix": str(out.get("stream_send_prefix", "") or ""),
        "stream_send_suffix": str(out.get("stream_send_suffix", "") or ""),
    }, warn
```

运行时解析辅助（新实现需要，供落地任务复制）：

```python
def parse_min_max(value: str, dmin: int, dmax: int) -> tuple[int, int]:
    """'30-900' → (30, 900)；非法/缺失回默认；保证 max ≥ min。"""
    try:
        a, b = str(value or "").replace("－", "-").split("-", 1)
        lo, hi = int(a.strip()), int(b.strip())
        return lo, max(lo, hi)
    except Exception:
        return dmin, dmax

def parse_quiet_hours(value: str, dstart: int = 1, dend: int = 7) -> tuple[int, int]:
    """'1-7' → (1, 7)，允许跨天（start > end）；start == end 视为不静默（对齐 manager.py:102-103）。"""
    lo, hi = parse_min_max(value, dstart, dend)
    return max(0, min(23, lo)), max(0, min(23, hi))
```

### 6.5 `llm_trigger`

```python
def migrate_trigger(old: dict) -> tuple[dict, list[str]]:
    pro = old.get("llm_proactive", {})
    sch = old.get("llm_schedule", {})
    warn: list[str] = []

    f, g = bool(pro.get("proactive_friend_enable", False)), bool(pro.get("proactive_group_enable", False))
    scope = "both" if f and g else "friend" if f else "group" if g else "off"

    lo = int(pro.get("proactive_min_interval_minutes", 30) or 30)
    hi = int(pro.get("proactive_max_interval_minutes", 900) or 900)
    if hi < lo:
        warn.append(f"max_interval({hi}) < min_interval({lo})，新模型按『最长=最短』处理"); hi = lo

    qs = int(pro.get("proactive_quiet_hours_start", 1) or 0)
    qe = int(pro.get("proactive_quiet_hours_end", 7) or 0)

    return {
        "proactive_scope": scope,
        "proactive_friend_sessions": [str(s) for s in (pro.get("proactive_friend_sessions") or [])],
        "proactive_group_sessions": [str(s) for s in (pro.get("proactive_group_sessions") or [])],
        "proactive_interval_minutes": f"{lo}-{hi}",
        "proactive_quiet_hours": f"{max(0, min(23, qs))}-{max(0, min(23, qe))}",
        "proactive_group_idle_minutes": int(pro.get("proactive_group_idle_minutes", 10) or 10),
        "proactive_max_unanswered": int(pro.get("proactive_max_unanswered", 3) or 3),
        "proactive_prompt": str(pro.get("proactive_prompt", "") or ""),
        "schedule_enable": bool(sch.get("schedule_enable", True)),
        "schedule_prompt": str(sch.get("schedule_prompt", "") or ""),
    }, warn
```

### 6.6 老配置推导示例（三个典型账号）

| 账号画像 | 旧存档 | 推导过程 | 新配置 |
|---|---|---|---|
| **A：从没在插件页保存过**（账号 123 的 `llm_assembly`/`llm_message_meta`/`llm_context`/`llm_schedule` 全是 `{}`，`llm_output` 有 818B） | `llm_output` 有 `mode`；meta/assembly 全空 | ① `mode` 直接取；② `include_time` 缺省 → True，位置取 agent（默认 `tail`）→ `time_position=tail`；③ `user_message_format` 取 agent `single_line` → `single_line`；④ 元信息全缺省 → `name`/`nickname`/`with_sender`；⑤ 段开关全缺省 → 6 项全选；⑥ 节奏 `interval_mode=none` → `off` | `delivery.mode=speech`；`prompt.time_position=tail`、`user_line_format=single_line`、四轴默认、`prompt_nudges` 全选；`prompt.prompt_record=off` |
| **B：只存过 `stream_output=True`（很老）** | `llm_output = {"stream_output": True}`，无 `mode` | `speech_via_tool` 不在存档 → 走 `stream_output` 分支 → `mode=stream`（`output_mode.py:55-56`） | `delivery.mode=stream`；同时因 `mode` 不再是 speech，发言通道键全部隐藏（`showIf`） |
| **C：老账号存了 `speech_via_tool=False` + `stream_output=False`** | `llm_output = {"speech_via_tool": False, "stream_output": False}` | `speech_via_tool` 存在且 False → `stream_output` False → `mode=text` | `delivery.mode=text` |
| **D：旧主动消息只想私聊** | `proactive_friend_enable=True`、`group_enable=False`、`min=30`、`max=900`、`quiet=1..7` | scope=`friend`；interval=`"30-900"`；quiet=`"1-7"` | `trigger.proactive_scope=friend` 等 |

> 说明：老账号里 `mode` 一旦被旧插件页整份落库（`app/plugin/base.py:64-78`），推导分支就永久走不到——这是**现状事实**，不是本文引入的；本文只保证"从未保存过"的账号按旧开关推导、行为不变。

---

## 7. 自检

### 7.1 算式闭合（109 键口径）

| 去向 | 键数 | 明细 |
|---|---|---|
| 保留（能力模块，不动） | **42** | `llm_context` 9（去 `referent_prompt_enable`）+ `llm_memory` 23 + `llm_tools_extra` 10 |
| 并入新键（52 旧 → 34 新） | **52** | prompt 26（→12 键）、delivery 13（→12 键）、trigger 13（→10 键） |
| 删除/退役 | **15** | 僵尸 3 + 常量内化 9 + 重复事实源/不可达 3（§4） |
| **合计** | **109** | 42 + 52 + 15 = 109 ✅ |

按来源插件核对（不重不漏）：

| 来源插件 | 键数 | 去向 |
|---|---|---|
| `llm_output` | 24 | delivery 24（13 并入 + 11 删除） |
| `llm_assembly` | 7 | prompt 7（6 并入 + 1 删除） |
| `llm_message_meta` | 14 | prompt 14（13 并入 + 1 删除） |
| `llm_context` | 10 | context 9 保留 + prompt 1 |
| `llm_memory` | 23 | memory 23 保留 |
| `llm_proactive` | 17 | prompt 5 + delivery 1 + trigger 11 |
| `llm_schedule` | 3 | delivery 1 + trigger 2 |
| `llm_tools_extra` | 10 | tools 10 保留 |
| `llm_enhance` | 1 | prompt 1 |
| **合计** | **109** | prompt 28 / delivery 26 / trigger 13 / 能力 42 ✅ |

新键数：prompt 12 + delivery 12 + trigger 10 = **34**；键总数 34 + 42 = **76**。

### 7.2 勘误版（110 键口径，供 Lead 取舍）

加入 `memory_inject_recency`（§1.2）：保留组 43 → **总数 77**（34 + 43），算式 43 + 52 + 15 = 110。
两个口径都不改变三核心模块的映射，只差"能力模块保留组 42 还是 43"。

### 7.3 可追溯矩阵（每个新键 → 旧键来源；纯新增键 = 0）

| 新键 | 来源旧键（数量） | 纯新增？ |
|---|---|---|
| `prompt.behavior_rules_override` | #25（1） | 否 |
| `prompt.participation` | #26（1） | 否 |
| `prompt.emoji_react_level` | #27（1） | 否（仅默认值裁决为 1） |
| `prompt.time_position` | #29 `prompt_time_position` + #32 `include_time`（2） | 否 |
| `prompt.user_line_format` | #28 #41 #42（3） | 否 |
| `prompt.meta_sender` | #33 #34（2） | 否 |
| `prompt.meta_mention` | #35 #39（2） | 否 |
| `prompt.meta_quote` | #36 #37 #40（3） | 否（新增 `ref_only` 态＝原 `fetch_quote_content=False` 的显式化） |
| `prompt.meta_include_body` | #38（1，改名） | 否 |
| `prompt.clean_output_parentheses` | #45（1） | 否 |
| `prompt.prompt_record` | #30 #109（2） | 否（四态＝两开关的两两组合） |
| `prompt.prompt_nudges` | #43 #53 + #90..#94（7） | 否 |
| `delivery.mode` | #1（+ #2 仅迁移 + 表外 `speech_via_tool`） | 否 |
| `delivery.speech_max_bubbles` | #21（1） | 否 |
| `delivery.speech_max_bubble_chars` | #22（1） | 否 |
| `delivery.speech_reminder_rounds` | #23（1） | 否 |
| `delivery.speech_force_reply_on_call` | #24（1） | 否 |
| `delivery.stream_sentence_max_length` | #4（1） | 否 |
| `delivery.stream_send_pool_enabled` | #6（1） | 否 |
| `delivery.stream_pace` | #7 #11（2） | 否（新增 `log`/`inverse` 两个**选项**，代码早已支持：`pace.py:30-33`） |
| `delivery.stream_pace_base_ms` | #8（1） | 否 |
| `delivery.stream_pace_max_ms` | #10（1） | 否 |
| `delivery.stream_send_prefix` | #16（1） | 否 |
| `delivery.stream_send_suffix` | #17（1） | 否 |
| `trigger.proactive_scope` | #79 #80（2） | 否 |
| `trigger.proactive_friend_sessions` | #81（1） | 否 |
| `trigger.proactive_group_sessions` | #82（1） | 否 |
| `trigger.proactive_interval_minutes` | #83 #84（2） | 否 |
| `trigger.proactive_quiet_hours` | #86 #87（2） | 否 |
| `trigger.proactive_group_idle_minutes` | #88（1） | 否 |
| `trigger.proactive_max_unanswered` | #85（1） | 否 |
| `trigger.proactive_prompt` | #89（1） | 否 |
| `trigger.schedule_enable` | #96（1） | 否 |
| `trigger.schedule_prompt` | #97（1） | 否 |

**纯新增键：0 个。** 新增的只有"能力已存在但界面没暴露"的 3 处（`stream_pace` 的 `log`/`inverse` 两档、`meta_quote` 的 `ref_only`），以及 `prompt_nudges`/`meta_*` 的 enum 化——都属于"把已有行为显式化"，不是新功能。

### 7.4 风险与有损清单（必须写进迁移日志的 10 条）

| # | 有损点 | 影响面 | 缓解 |
|---|---|---|---|
| 1 | `stream_send_curve_k` 内化 | 显式改过 k 的账号 | 先备份再 warning；需要强节奏可用 `base_ms` |
| 2 | `stream_short_message_length` 内化 | 改过的账号 | 同上 |
| 3 | `stream_short/long_message_delay_ms` 内化 | 改过的账号 | 同上 |
| 4 | `stream_send_interval_min_ms` 内化 0 | 改过的账号 | 同上 |
| 5 | 队列 3 键内化 | 改过的账号（极少，pool 默认关） | 同上 |
| 6 | `prompt_dump_max_entries` 内化 20 | 改过的账号 | 同上 |
| 7 | `sender/sent` 混合标签组合归一到 `multi_line_verbose` | 措辞，不影响信息量 | warning |
| 8 | `proactive_env_prompt_enable` / `env_intent_nudge` 合成一项 | 想只关其一的账号 | 二者本是母子门控（`prompt.py:257,282`），合并不产生"该出现的没出现" |
| 9 | `meta_mask_nickname` 恒脱敏 | 曾想关脱敏的账号（该值本就读不到） | 说明文档 + 后续如需可加 1 键（纯新增） |
| 10 | `stream_proactive/scheduled` 删除 → `mode` 统一 | `mode=stream` 账号的主动/定时开始流式 | 属修复；若产品要"按来源区分"，另加 `trigger.initiative_stream`（纯新增，不属本轮） |

**技术债/前置项**（不阻塞键表，但落地前要补）：

1. `string_list` 控件是自由文本行（`StringListWidget.vue`），没有标签/校验 → `prompt_nudges` 需要后端白名单校验 + 描述里列全 id；长期方案是给前端加 `enum_list` 控件。
2. `prompt_nudges`、`meta_*` 的"多选/枚举"若产品要求更细致（每项独立开关），需要 `list`/`dynamic` 控件的后端 endpoint 支持（`ListWidget` 走 `/api/module/<name>/list/<endpoint>`，`DynamicWidget` 需 `_selected` 配套键）——本轮按 ≤12 硬预算选择枚举化。
3. 归属表 `MODULE_KEYS` 要新增 3 个模块条目并同步删旧条目，`config_partition.py:221` 的"107 键"注释、`scripts/migrate_v3_split_config.py:1` 的"108 个键"一并改（§3 数字漂移）。
4. 迁移脚本要把 `debug_prompt` 从 `DELETED_KEYS`（`config_partition.py:404-409`）移出，补 `mode` 进归属表（01 §8-6）。
5. 静态闸门（新增 CI 断言）：schema 键 = `MODULE_KEYS` 键 = "有真实读取点的键"，差集为空；且三模块 schema 键数 ≤ 12/12/10。

### 7.5 需 Lead 拍板的 5 项（本文已给建议值）

| # | 项 | 本文裁决 | 备选 |
|---|---|---|---|
| 1 | `emoji_react_level` 默认 0 还是 1（模块 0 / agent 1 打架，0 会造成悬空指涉） | **1** | 保持 0 + 同时改行为段文案（去掉"见【可用表情】"） |
| 2 | `mode` 默认仍 `speech`？真实日志显示 speech 对国产模型易整轮静默（01 §1、doc `03-logs` B1/B2） | **默认不动**（先修"人格卡输出条款自动剥离"，再灰度） | 改 `stream` 作默认 |
| 3 | 主动/定时是否保留独立流式开关（本轮删除 2 键） | **删**，由 `mode` 统一 | 加 1 键 `trigger.initiative_stream`（纯新增，delivery/trigger 键数 12/11） |
| 4 | `llm_knowledge` 3 键（无插件持有，知识库工具永久不可用） | **从归属表删除** | 重建插件 |
| 5 | `llm_tools_extra.onebot_tools_enable` 默认 `True`（安全相关，旧默认 False） | **改回 False**（能力模块内单点修复，不占本轮键表） | 保持 True + 首次启用时弹确认 |

---

## 8. 落地清单（供后续任务直接引用，本轮不执行）

| # | 任务 | 涉及文件 | 验收 |
|---|---|---|---|
| 1 | 建 3 个插件目录 + `module.json` + `main.py`（`provides/supersedes` 与 `on_load` 手工接管按 01 §2 的现有模式） | `app/plugins/llm_{prompt,delivery,trigger}/**` | 3 个模块在插件页可见、各自 schema 键数 12/12/10 |
| 2 | 改 `MODULE_KEYS`：新增 3 条、删 `llm_knowledge`/旧 6 条、`llm_context` 去 `referent_prompt_enable`、补 `mode` | `app/agent/config_partition.py:222-358,404-409` | `stale_keys()/unknown_keys()` 无新增；同一键不被两个模块持有 |
| 3 | 改读点：`llm_message_meta/src/service.py:82,95-96` 从 `runtime.config` 改 `policy_config(runtime,"prompt")`；`tool_source.py:36-38`/`session_tools.py:126` 的 `fetch_at_nickname` 改读 prompt 视图；`llm_tools_extra/src/onebot/executor.py:148` 改 `policy_get(runtime,"tools",...)` | 框架 + 能力模块 | 各键"改了即生效"（02 §5.3-4 的回归） |
| 4 | `llm_proactive/src/manager.py:322`、`llm_schedule/src/scheduler.py:291` 的流式判定改读 `delivery.mode` | 旧插件（迁移期） | `mode=stream` 时主动/定时真的流式 |
| 5 | 写迁移脚本：`migrate_109_to_34.py`（本文 §6 伪代码）+ `migration_log.json`（warning 10 类） | `scripts/` | 109 键逐键落位；warning 可追溯到旧值；原 `bindings.config_json` 不动 |
| 6 | 加 CI 闸门：schema 键数 ≤12/12/10、`schema == MODULE_KEYS`、0 读取键为空、`description` 不含被删词表 | `tests/architecture/` | 三条断言全绿 |

> 备注（AGENTS.md 提交纪律）：本文件位于 `/logs/`（`.gitignore:58` 已忽略），属工作产物、不入库；本轮**未改任何 `.py`**，无需分步提交。
