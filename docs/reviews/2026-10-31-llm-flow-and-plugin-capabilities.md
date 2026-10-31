# LLM 响应流程 & 插件能力盘点（2026-10-31）

> 口径：以当前仓库代码为准。`app/plugins/` 下的 10 个是**内置插件**；
> `data/module/plugins/` 下的 12 个是**外置插件**（代码不在仓库，跟随数据目录）。

---

## 1. 一条消息的完整响应链路

```
OneBot 上报
  │  gateway.py：解码(codec) → 接收日志 → 忽略标记 → 派发
  ▼
NodeRunner 入站链（app/plugin/nodes/inbound.py）
  ① router(100)      订阅匹配 + 账号归属 + **已关闭模块直接排除**
  ② permission(110)  enabled → 谁响应(responder) → 群/用户范围 → 身份档位
  ③ command(115)     #llm 等指令（命中即 event.llm.stop()）
  ④ invoke(120)      逐个调用模块 handle（业务插件）
  ⑤ agent(130)       框架兜底：取 Agent 运行时 → 门控 → LlmPipeline.submit
  ▼
LlmPipeline._run（app/agent/loop/pipeline.py）
  · 指令 / 图片探测
  · 群聊触发判定（trigger_mode：at/keyword/both/all/none + 概率）
  · 场景开关（群聊/私聊 + 群级覆盖 + 静音）
  · quiet_hours / 用户例外 / responder / 权限（统一 ResponseAuthority 判定链）
  · debounce 合并 → pre_request 钩子 → 图片解析 → 会话锁
  ▼
prepare_prompt（app/agent/loop/prompt_prep.py）
  · 历史读取/压缩、在线历史补拉、群聊环境记录文本
  · 收集工具：框架系统工具 + 模块 @tool + 模块 tool_sources + 发言通道工具
  · 收集上下文块：module context_providers → extra_blocks
  ▼
turn.run_turn（app/agent/loop/turn.py）
  · PromptAssembler / 模块装配适配器 → messages
  · 工具循环（工具执行 / 中间件 / 审批 / 超时 / 结果落盘）
  · Provider 链（多模型池、重试、降级、模态门控）
  ▼
输出策略（app/agent/delivery/output.py）
  · BlockingOutput（一次发完）
  · StreamOutput（逐句 + 发送池/节奏）
  · 发言通道（文本=思考，send_message 出稿 → 轮次边界 flush）
  ▼
delivery/pipeline：pre_send / 切分 / [reply][@QQ] 指令 / 发送 / post_send
  ▼
写历史、mark_replied、记忆蒸馏等 turn_completed 钩子、遥测与日志
```

关键事实：**框架负责"怎么跑"**（链路、门控、装配、工具循环、provider、输出、历史、遥测）；
**插件负责"业务策略"**（配置域、额外工具、额外上下文、定时/主动、记录等）。

---

## 2. 不装/关闭所有插件时，框架还剩什么

### 2.1 可用的完整能力

| 能力 | 说明 |
|---|---|
| 消息接入与分发 | OneBot v11 编解码、入站节点链、指令、事件总线 |
| 访问控制 | Agent 启停、群/私聊场景、触发方式、静默/免打扰、用户例外、多账号响应权、身份档位（统一 `ResponseAuthority`） |
| 会话与历史 | SQLite 会话、历史落盘/归档、按需补拉 QQ 历史、历史检索、图片缓存、超大工具结果 spill |
| 提示词装配 | 12 个默认 section（人设/行为规则/表情提示/定时协议/主动性/元信息格式/底线/技能/记忆/背景/群记录/历史/用户）+ 三套提示词方案 + 角色预设 + 安全与工作方式 |
| 工具运行时 | 工具循环、并行、审批、超时、结果截断/spill、按需取回、中间件、四类开关过滤 |
| 框架自带工具 | `get_current_session`、`get_chat_history`、`recall_tool_result`、`history_search`、`tavily_search`（配 key + 开关）、`send_message`/`finish`（发言通道模式） |
| Provider | OpenAI-compatible / Responses / Anthropic / Gemini 适配器、多模型池、重试、降级、模态门控、token 预算 |
| 输出 | 阻塞 / 流式 / 发言通道；切句、发送节奏池、引用/@ 指令、发送钩子 |
| 上下文管理 | 历史压缩、token 预算、`pre_request`/`turn_completed` 等钩子广播 |
| 配置与 UI | Provider/人格/提示词/权限/回复策略/日志/统计等页面（模块配置页由模块 schema 驱动） |

### 2.2 只有"零件"、没有产品能力的部分

框架里保留了这些**原语**，但真正接线在插件：

- 群聊环境记录：`app/agent/group_log/`（store/context/render 都在），但**没有记录者**——需要插件订阅事件并写入；
- 记忆/知识库：`app/agent/storage/` 有存储接口与实现，但没有工具、没有上下文召回、没有 UI；
- 提示词 section 扩展点、工具源/上下文贡献者注册表（`register_section` / `register_tool_source` / `register_context_provider`）；
- 主动消息/定时任务的运行时槽（`runtime.proactive` / `runtime.scheduler` 默认 None）。

---

## 3. 内置插件（`app/plugins/`，10 个）分别提供什么

| 插件 | 提供的能力 | 关闭/缺席后的表现 |
|---|---|---|
| `llm_assembly` | 接管 `runtime.assembler`：把组装相关配置（`user_message_format` / `system_prompt_style` / `prompt_time_position` / `prompt_dump_*`）从 agent 配置搬到模块视图；prompt dump 依赖它挂的 `assembly_options` | 回退框架 `PromptAssembler`（默认段仍工作，但组装相关配置以 agent 旧值为准，无法做 prompt dump） |
| `llm_output` | 接管 `runtime.output`：三选一输出形态 `text/stream/speech` + 二十余个流式节奏键（池/间隔/前缀后缀/打断） | 回退 agent 配置的 `stream_output`（默认阻塞式），无逐句节奏与发言通道包装 |
| `llm_context` | `pre_request` 焦点表衰减 + 上下文贡献者（指代/焦点块）+ 工具源：`expand_recent` / `expand_message` / `expand_user` / `expand_image`；历史相关配置（`history_*`） | 无"刚才/那条"指代块、无按需展开工具（框架只留 `get_chat_history` 粗粒度） |
| `llm_memory` | 长期记忆：store + 上下文召回 provider + 工具 `memory_save/recall/delete/correct/deny` + `pre_request` 确定性写入 + `turn_completed` 蒸馏 | 无记忆召回块、无记忆工具、无蒸馏 |
| `llm_knowledge` | 知识库：store/向量 + 工具源 `knowledge_add/search/delete` + `runtime.knowledge_service`（知识页/API 用） | 无知识库工具与页面数据（没有自动注入，模型只能靠工具查） |
| `llm_message_meta` | 两个 `pre_request` 钩子：收集并渲染"谁/时间/引用/@/已发送"进本轮 user 文本；拥有 14 个 `include_*` / `meta_*` 键 | 用户消息只保留原始正文（没有发送者/时间/引用渲染） |
| `llm_proactive` | 主动消息管理器：订阅 `message_observed`/`bot_sent`，按配置主动开口（私聊随机、群聊冷场、免打扰）；`proactive_service` 状态页 | 无主动消息，`runtime.proactive` 保持 None |
| `llm_schedule` | 定时任务：`schedule_task` 工具 + 调度器执行器 + `runtime.scheduler`；`schedule_enable`/`schedule_prompt` 配置 | 无定时发言能力，框架只有"协议文案"没有执行器 |
| `llm_tools_extra` | OneBot API 工具（175 个，含 send/管理/查询，风险分级与权限/冷却/审批）、MCP 工具（`mcp_servers`）、`tool_result_directive_*`、`poke_cooldown_seconds` | 无 OneBot 工具与 MCP 工具；模型只能用框架自带工具 |
| `llm_enhance`（现名「LLM调试」） | 仅一个 `pre_request` 钩子：`debug_prompt` 打开后打印完整 prompt | 无影响（纯调试） |

> 注：`llm_knowledge` 当前工作区正在被另一个会话改动/删除（`git status` 显示删除未提交），
> 本表按 HEAD 版本记录。

---

## 4. 外置插件（`data/module/plugins/`，12 个，非内置）

这些代码不在仓库里，跟随数据目录安装；能否加载取决于插件目录与启用状态：

| 插件 | 能力 |
|---|---|
| `group_log` | **群聊记录的唯一记录者**：把消息/表情/戳/撤回/机器人发言写入 `GroupLogStore`（框架的 group_log 块才能渲染） |
| `bilibili_parser` / `douyin_parser` | 解析 B 站 / 抖音链接，命中后接管回复（`event.llm.stop()`） |
| `df_password` | 定时推送/查询"今日密码"类口令 |
| `emoji_reply` / `poke_reply` | 表情 / 拍一拍回应 |
| `group_request` | 入群申请自动审核（关键词 + 等级） |
| `log_sender` | 聊天里 `/log`、`/logs` 取日志文件 |
| `recall_back` | 消息撤回后的转发/提示 |
| `time_sign_in` | 每日群签到（`#签到`） |
| `what_to_eat` | "吃什么"推荐 |
| `example` | 示例插件（写插件时照着抄） |

注意：这些模块在"新账号"上默认关闭（`modules_default_enabled=0`），
这也是之前日志里 `account_disabled` 的来源（现已改为静默）。

---

## 5. 结论：哪些能力"离开插件就没有"

| 能力 | 依赖 |
|---|---|
| 群聊环境记录（环境块里有内容） | `group_log`（外置） |
| 长期记忆（召回/写入/蒸馏） | `llm_memory` |
| 知识库（增删查 + 工具） | `llm_knowledge` |
| OneBot 工具（175 个）/ MCP 工具 | `llm_tools_extra` |
| 定时发言 | `llm_schedule` |
| 主动消息 | `llm_proactive` |
| 用户消息的发送者/时间/引用渲染 | `llm_message_meta` |
| 指代消解 / 上下文按需展开 | `llm_context` |
| 输出形态与流式节奏（UI 配置） | `llm_output` |
| 组装配置域 / prompt dump | `llm_assembly` |
| 半自动业务（链接解析、签到、撤回提示等） | 12 个外置插件 |

**不依赖任何插件即可工作**：单账号接消息 → 触发/权限门控 → 读人设 + 行为规则 → 历史 →
模型请求 → 文本/流式回复 → 写历史/日志。工具面最小为会话/历史/spill/（可选）搜索，
没有记忆、知识、OneBot 操作、定时与主动能力。
