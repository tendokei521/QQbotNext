# 内置模块报告

- 生成时间：2026-10-30
- 内置业务模块数：**10**（目录 `app/plugins/`，均为 `builtin: true`）
- 代码规模：73 个 .py，9697 行
- 持有配置键（去重前）：109 个

## 总表

| 模块 | 显示名 | 职责 | 持有配置键 | 代码行 | 特殊 |
|---|---|---|---:|---:|---|
| `llm_assembly` | 组装策略 | 控制每轮回复怎么拼、按什么格式发给模型 | 10 | 230 | 接管 assembly |
| `llm_context` | 上下文增强 | 让模型知道「刚才」「那条」指什么，需要时再展开上下文 | 10 | 1941 | hook |
| `llm_enhance` | LLM增强 | 连发的消息自动合并，补齐用户信息再回复 | 0 | 52 | pinned、hook |
| `llm_knowledge` | 知识库 | 把资料整理成知识库，回答前先查一查 | 3 | 755 | — |
| `llm_memory` | 长期记忆 | 记住用户的偏好和事实，之后聊天时想起来 | 17 | 2479 | — |
| `llm_message_meta` | 消息元信息 | 给消息补上时间、发送者、引用和 @ 信息 | 14 | 496 | — |
| `llm_output` | 输出策略 | 决定一轮回复怎么发：一次发完、逐句发还是先想再说 | 25 | 509 | 接管 output |
| `llm_proactive` | 主动消息 | 私聊随机、群聊沉默后主动开口，支持免打扰 | 17 | 1126 | — |
| `llm_schedule` | 定时任务 | 到点自动发言（每天 / 每周 / 每月） | 3 | 1111 | — |
| `llm_tools_extra` | 工具扩展 | 给模型更多可用工具，并控制它怎么回应工具结果；OneBot 工具的开关与权限在「高级工具 → OneBot 工具」里设置 | 10 | 998 | — |

## 逐模块

### `llm_assembly` · 组装策略（v1.0.0）

- 职责：控制每轮回复怎么拼、按什么格式发给模型
- 权限：`everyone`；分类：LLM；pinned：否
- 接管能力：assembly
- 代码：4 个文件 / 230 行；schema 10 项，默认配置 10 项
- 持有配置键（10）：
  - assembly_profile、behavior_rules_enable、behavior_rules_override、participation、emoji_react_level、user_message_format、prompt_time_position、system_prompt_style、prompt_dump_enable、prompt_dump_max_entries
- 相关测试文件：8 个

### `llm_context` · 上下文增强（v1.0.0）

- 职责：让模型知道「刚才」「那条」指什么，需要时再展开上下文
- 权限：`everyone`；分类：LLM；pinned：否
- 钩子：llm_hook("pre_request")
- 代码：9 个文件 / 1941 行；schema 10 项，默认配置 10 项
- 持有配置键（10）：
  - context_expand_enable、referent_resolve_enable、referent_prefetch_enable、referent_ambiguous_policy、referent_prefetch_max、referent_focus_ttl、referent_focus_max、referent_prompt_enable、history_auto_qq_min_local、history_cross_query_enable
- 相关测试文件：12 个

### `llm_enhance` · LLM增强（v1.0.0）

- 职责：连发的消息自动合并，补齐用户信息再回复
- 权限：`everyone`；分类：LLM；pinned：是
- 钩子：llm_hook("pre_request", event_type="*", order=30)
- 代码：3 个文件 / 52 行；schema 2 项，默认配置 1 项
- 持有配置键（0）：
  - （纯机制模块，无独占配置键）
- schema 有但归属表未列：debug_prompt、group_debug
- 相关测试文件：0 个

### `llm_knowledge` · 知识库（v1.0.0）

- 职责：把资料整理成知识库，回答前先查一查
- 权限：`everyone`；分类：LLM；pinned：否
- 代码：9 个文件 / 755 行；schema 3 项，默认配置 3 项
- 持有配置键（3）：
  - knowledge_enable、knowledge_embedding_model_id、knowledge_recall_limit
- 相关测试文件：11 个

### `llm_memory` · 长期记忆（v1.0.0）

- 职责：记住用户的偏好和事实，之后聊天时想起来
- 权限：`everyone`；分类：LLM；pinned：否
- 代码：12 个文件 / 2479 行；schema 17 项，默认配置 17 项
- 持有配置键（17）：
  - memory_enable、memory_private_enable、memory_group_enable、memory_recall_max、memory_recall_max_chars、memory_save_deterministic、memory_extract_enable、memory_extract_interval_min、memory_max_per_owner、memory_user_cross_group、memory_audit_enable、memory_audit_inject、memory_min_confidence、memory_inject_hedge、memory_max_age_days、memory_on_reset、memory_upgrade_saved_only
- 相关测试文件：27 个

### `llm_message_meta` · 消息元信息（v1.0.0）

- 职责：给消息补上时间、发送者、引用和 @ 信息
- 权限：`everyone`；分类：LLM；pinned：否
- 代码：5 个文件 / 496 行；schema 14 项，默认配置 14 项
- 持有配置键（14）：
  - include_time、include_private_qq、include_sender、include_mentioned、include_quote、include_quote_sender、include_sent、fetch_at_nickname、fetch_quote_content、meta_sender_style、meta_sent_style、meta_instruction_mode、meta_mask_nickname、clean_output_parentheses
- 相关测试文件：5 个

### `llm_output` · 输出策略（v1.0.0）

- 职责：决定一轮回复怎么发：一次发完、逐句发还是先想再说
- 权限：`everyone`；分类：LLM；pinned：否
- 接管能力：output
- 代码：6 个文件 / 509 行；schema 25 项，默认配置 26 项
- 持有配置键（25）：
  - stream_output、stream_send_pool_enabled、stream_send_by_sentence、stream_sentence_max_length、stream_send_interval_mode、stream_send_interval_base_ms、stream_send_interval_min_ms、stream_send_interval_max_ms、stream_send_curve、stream_send_curve_k、stream_short_message_length、stream_short_message_delay_ms、stream_long_message_delay_ms、stream_send_prefix、stream_send_suffix、stream_send_max_queue、stream_queue_full_policy、stream_flush_on_finish、stream_keep_order、speech_via_tool、speech_max_bubbles、speech_max_bubble_chars、speech_reminder_rounds、speech_force_reply_on_call、speech_fallback_enable
- schema 有但归属表未列：mode
- 归属表有但 schema 未列：speech_via_tool
- 相关测试文件：8 个

### `llm_proactive` · 主动消息（v1.0.0）

- 职责：私聊随机、群聊沉默后主动开口，支持免打扰
- 权限：`everyone`；分类：LLM；pinned：否
- 代码：8 个文件 / 1126 行；schema 17 项，默认配置 17 项
- 持有配置键（17）：
  - proactive_friend_enable、proactive_group_enable、stream_proactive_enabled、proactive_friend_sessions、proactive_group_sessions、proactive_min_interval_minutes、proactive_max_interval_minutes、proactive_max_unanswered、proactive_quiet_hours_start、proactive_quiet_hours_end、proactive_group_idle_minutes、proactive_prompt、proactive_prompt_enable、proactive_history_intent_nudge、proactive_env_prompt_enable、proactive_unresolved_prompt_enable、proactive_env_intent_nudge
- 相关测试文件：12 个

### `llm_schedule` · 定时任务（v1.0.0）

- 职责：到点自动发言（每天 / 每周 / 每月）
- 权限：`everyone`；分类：LLM；pinned：否
- 代码：7 个文件 / 1111 行；schema 3 项，默认配置 3 项
- 持有配置键（3）：
  - schedule_enable、schedule_prompt、stream_scheduled_enabled
- 相关测试文件：11 个

### `llm_tools_extra` · 工具扩展（v1.0.0）

- 职责：给模型更多可用工具，并控制它怎么回应工具结果；OneBot 工具的开关与权限在「高级工具 → OneBot 工具」里设置
- 权限：`everyone`；分类：LLM；pinned：否
- 代码：10 个文件 / 998 行；schema 5 项，默认配置 10 项
- 持有配置键（10）：
  - onebot_tools_enable、onebot_tools_allowed、onebot_tools_denied、onebot_tools_max_result、onebot_tools_debug、onebot_tool_overrides、poke_cooldown_seconds、mcp_servers、tool_result_directive_enable、tool_result_directive
- 归属表有但 schema 未列：onebot_tool_overrides、onebot_tools_allowed、onebot_tools_debug、onebot_tools_denied、onebot_tools_enable
- 相关测试文件：9 个

## 装配与开关机制

- **位置**：内置业务模块 = `app/plugins/<name>/`（10 个），运行时代码随仓库走；不是 `data/module/plugins/`（那是用户安装/外置目录）。
- **清单来源**：每个模块的 `module.json`（display_name / version / entry / permission / description），缺失时回落到类属性。
- **订阅与定位**：这 10 个全是 **LLM 策略模块**（`subscribe == []`），不处理消息事件；它们通过钩子（`llm_hook`）或能力接管（`supersedes`）参与每轮对话，另有两个真正"接管槽位"的：
  - `llm_assembly` → `supersedes=("assembly",)`，接管消息组装；
  - `llm_output` → `supersedes=("output",)`，接管输出策略。
- **启用规则**：模块启停存 `bindings`（capability + account 作用域），未写过的按 `enabled=True`；
  账号还有 `modules_default_enabled`：**老账号默认开（1）、新建账号默认关（0）**，所以"新号功能插件全灰、老号全亮"是设计而非 bug。
- **默认置顶**：只有 `llm_enhance` 设了 `pinned=True`（功能插件列表默认置顶），不影响启停。
- **配置归属**：每个模块持有自己的配置键（`app/agent/config_partition.py::MODULE_KEYS`），合计 109 个键；
  模块配置最终写进 `bindings.config_json`，Agent 面板只读策略视图。
- **框架级模块**：`agent`（Agent 框架本身）是**虚拟模块**，不在 `app/plugins`，但同样在「功能插件」里显示启停与权限。

## 与「功能插件」页上看到的其他模块的区别

当前 `data/module/plugins/` 还装着 12 个**外置模块副本**（旧的业务插件）：

`bilibili_parser`、`df_password`、`douyin_parser`、`emoji_reply`、`example`、`group_log`、`group_request`、`log_sender`、`poke_reply`、`recall_back`、`time_sign_in`、`what_to_eat`

它们**不是内置模块**（`builtin=false` 语义），代码不在仓库里，随数据目录走；这也解释了为什么"功能插件"页会多出十几个模块，以及之前"模块被全部打开"的现象来自这批外置副本的批量启用。

## 发现的问题（建议后续修）

| # | 问题 | 证据 |
|---|---|---|
| 1 | `app/plugins/README.md` 已过期：还写着"仓库自带的 14 个业务插件"并列出早已移出的业务插件 | README 表格与 `app/plugins/` 实际内容不符 |
| 2 | `llm_output` 的配置归属与 schema 漂移：schema 新增 `mode`（text/stream/speech），`MODULE_KEYS` 仍列旧的 `speech_via_tool`；`mode` 无归属 | 见 `llm_output` 小节的两行"未列"提示 |
| 3 | `llm_enhance` 的 `debug_prompt` / `group_debug` 未进归属表 | 同上 |
| 4 | `llm_tools_extra`：`default_config` 有 10 项，schema 只露 5 项（onebot_* 已挪到「高级工具 → OneBot 工具」页） | 同上；属有意拆分，但键仍归该模块 |
| 5 | 外置副本与内置模块存在"同名旧实现"隐患（目前不同名，无冲突） | `data/module/plugins` 12 个 vs `app/plugins` 10 个 |

## 数据说明

- 代码行数 = 模块目录下全部 `.py` 的行数。
- "相关测试文件" = `tests/` 下文本中出现模块名的文件数（粗略指标，含间接引用）。
- 本报告数据由脚本读取 `module.json`、`Module` 类属性与 `MODULE_KEYS` 生成，可重复。
