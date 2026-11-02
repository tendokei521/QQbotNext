# Agent 配置键参考

> 读者：要改配置项、写迁移脚本、排查"这个开关到底管什么"的人。
> 界面文案只讲"做什么"；这里补**默认值、生效时机、什么时候该动**。
> 键的真源是 `app/agent/config.py`（默认值）+ `app/agent/config_schema.py`（渲染）+ `app/agent/config_partition.py`（谁拥有）。

## 0. 三层与"一个字段只有一个入口"

| 层 | 存在哪 | 谁渲染 | 说明 |
|---|---|---|---|
| 进程配置 | `.env` / 环境变量 | 启动参数 | 端口、日志目录、DB 路径、WebSocket 超时等，**不是** Agent 配置 |
| **本体键** | `data/app.db`（agent 配置） | 「AI 对话面板」「回复与权限」等页面 | 框架自身行为 |
| **模块键** | `data/app.db`（模块配置） | 各模块自己的配置页 | 插件行为（记忆/触发/工具…） |

- 别把模块键写进 Agent 页面，也别在框架里直接读模块键——用 `app/agent/policy.py` 的
  `policy_get(runtime, kind, key, default)`（模块接管读模块值，否则回退本体值）。
- 已删除的键：`assembly_profile`、`behavior_rules_enable`、`system_prompt_style`。
  库里的残值会被忽略，不需要手工清理。

## 1. 回复开关与触发（`group_switch` / `group_trigger` / `group_debounce`）

| 键 | 默认 | 说明 |
|---|---|---|
| `private_enable` | `true` | 私聊是否回复 |
| `group_enable` | `false` | 群聊是否回复（默认关，避免进群即刷屏） |
| `reply_cooldown` | `5` | 群聊回复后的冷却秒数，避免连续插话 |
| `trigger_at` | `false` | 群里被 @ 才回 |
| `trigger_keyword` | `[]` | 命中关键词才回（与 @ 取"或"） |
| `message_debounce_seconds` | `0` | 同一人连发多条时的合并等待；0 = 每条立即处理 |
| `message_debounce_max_wait` | `15` | 持续有人说话时最长等待，避免一直不回复 |

> 逐群/逐人的覆盖不在这里：那是「回复与权限」的绑定（binding / dialogue policy），
> 页面会标出每个值来自"默认 / 账号 / 群"。

## 2. 权限（`group_permission`）

| 键 | 默认 | 说明 |
|---|---|---|
| `permission` | `member` | 响应权档位：拥有者 > 管理者 > 群主 > 群管 > 成员 |
| `agent_owner_list` | `[]` | 拥有者 QQ 名册（唯一来源，取代旧 `owner_id`） |
| `agent_manager_list` | `[]` | 管理者 QQ 名册 |
| `tool_approval_enable` | `false` | 高危工具执行前是否要求人工审批（无人确认则不执行） |

## 3. 会话与上下文（`group_session` / `session_window` / `group_compress`）

| 键 | 默认 | 说明 |
|---|---|---|
| `prompt_preset` | `chat` | **三选一提示词方案**：`chat` 群友 / `roleplay` 扮演 / `assistant` 助手。决定 L1 规则文案，不改人格 |
| `persona_preset_id` | `""` | **人格唯一来源**；`system_prompt` 只是兜底副本（启动时对齐） |
| `session_timeout` | `60` | 无消息多久结束一轮会话（秒） |
| `history_rounds` | `50` | 带多少轮历史给模型 |
| `history_mode` | `recent` | `recent` 只带最近若干条（更省，旧内容模型可自己查）；`all` 全带 |
| `history_recent_messages` | `12` | `recent` 模式的条数 |
| `max_message_length` | `200` | 单条消息字数上限（长回复会被切分） |
| `context_compress_enable` | `true` | 超上限时把较早历史压成摘要 |
| `context_window` | `0` | 模型上下文窗口（token）；0 = 不按长度触发压缩 |
| `context_compress_trigger_ratio` | `0.8` | 输入估算占窗口多少比例时触发压缩 |
| `context_compress_keep_ratio` | `0.16` | 压缩后保留最近多少比例原文 |
| `context_token_budget` | `0` | 上下文 token 硬上限；0 = 不限 |
| `include_pre_history` | `false` | 额外拉取入站前的群聊消息当背景（群聊记录模块停用时才有用） |
| `group_log_enable` 等 | 见界面 | 群聊背景记录的开关/窗口/条数/字数 |

## 4. 回复形态与投递（`group_stream` / `group_interrupt`）

| 键 | 默认 | 说明 |
|---|---|---|
| `speech_via_tool` | `true` | **文本=思考，发言必须调 `send_message`**；不调用=静默（正常结束） |
| `speech_max_bubbles` | `4` | 一轮最多几条气泡 |
| `speech_max_bubble_chars` | `300` | 单条气泡字数上限 |
| `stream_output` | `false` | 是否流式输出（需模型/适配器支持）；失败会自动降级为整段 |
| `interrupt_enable` | `false` | 回复途中来新消息时是否打断重来 |
| `interrupt_save_sent` | `true` | 打断时把已发出的句子记进历史 |
| `clean_output_parentheses` | `true` | 清掉"（笑）"这类括号动作 |

> 流式细分参数（`stream_send_*`、`stream_*_delay_ms` 等 19 个键）只在开流式时生效，
> 界面分组里有说明，一般不用动。

## 5. 工具与多模态（`group_tools`）

| 键 | 默认 | 说明 |
|---|---|---|
| `tool_parallel_max` | `8` | 同一时刻最多并行几个工具（1 = 全串行） |
| `tool_timeout_default_s` | `20` | 工具默认超时；超时只算本次失败，不卡整轮 |
| `tool_spill_enable` | `true` | 工具结果过长时落盘，只给模型摘要 + 位置 |
| `tool_spill_max_bytes` | `50000` | 超过多少字节落盘 |
| `image_understanding_enable` | `true` | 把本轮图片发给模型（需模型支持视觉） |
| `gif_understanding_enable` | `true` | 动图/表情包拆帧后一起发 |
| `gif_max_frames` / `gif_frame_max_edge` | `6` / `512` | 拆帧数与单帧长边 |
| `system_tools_enabled` / `module_tools_enabled` / `mcp_tools_enabled` | `{}` | 逐工具开关（未列出的按默认） |

## 6. 联网与缓存（`group_tavily` / `group_model_cache`）

| 键 | 默认 | 说明 |
|---|---|---|
| `tavily_enable` | `false` | 开启联网搜索工具 |
| `tavily_api_key` | `""` | Tavily Key（界面脱敏显示） |
| `tavily_max_results` / tavily_search_depth / tavily_max_content_chars | `5` / `basic` / `2000` | 搜索条数 / 深度 / 单条截断 |
| `prompt_cache_enable` | `true` | 让模型复用上一轮读过的提示词前缀（省钱、加快首字）；只有部分模型支持 |

## 7. 高级（`group_advanced`）与重试预算

| 键 | 默认 | 说明 |
|---|---|---|
| `reply_mode` / `participation_level` / … | 见界面 | 参与度与"该不该接话"的策略键（`participation_*`） |
| `structured_assembly` | 见界面 | 结构化消息组装（如按 JSON 组织回复段落） |
| `retry_profile` | `balanced` | 一轮请求次数与输入 token 上限总闸：`thrifty` 2 次 / `balanced` 4 次 / `robust` 6 次 |
| `reasoning_overflow_retries` | `1` | 推理超长时的收敛重试次数 |
| `empty_reply_retries` | `1` | 模型返回空内容时重试次数 |
| `max_tool_rounds` | `5` | 一轮内工具循环最多几轮 |
| `prompt_dump_enable` / `prompt_dump_max_entries` | `false` / `20` | 每轮最终 messages 落盘对账（排障用） |

## 8. Provider / 模型侧（不在 Agent 页面）

模型与连接是另一套资源：`data/app.db` 的 provider 预设（地址+Key）与模型实例（模型名 + 能力 + 温度等）。
「模型服务」页管理，Agent 只挑一个 `provider_model_id` / 模型池。
模型的"支持能力"（文本/图像/语音/工具调用）由模型实例的 `config.modalities` 决定；
拉取模型时会按厂商 `/models` 元数据给出预勾选建议（推断不出来就用默认值，可手改）。

## 9. 改键的流程（清单）

1. 在 `app/agent/config.py` 加默认值；
2. 在 `app/agent/config_schema.py` 加渲染（`group`/`label`/`description`/`default`）；
3. 在 `app/agent/config_partition.py` 决定归本体还是归某个模块；
4. 写迁移（如需改旧值/删旧键）并保证幂等；
5. 跑 `tests/architecture/test_config_partition.py` 与 `tests/core/test_env_template.py`（如涉及 `.env`）；
6. 更新本文档与 [UI_COPY.md](UI_COPY.md) 的术语。
