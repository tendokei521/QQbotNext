# Agent 配置字段说明（设计意图归档）

> 界面上的文案只讲"做什么"，这里保存被精简掉的**设计意图 / 历史原因 / 与第三方框架的对应关系**。
> 文案规范见 `docs/UI_COPY.md`。

---

## 重试与预算

### `retry_profile`（重试档位）

界面写的是「一轮对话的请求次数与输入 token 上限」。

**原文（已移出界面）**：一整轮对话最多能发几次请求、累计烧多少输入 token 的总闸门。
节流：最多 2 次请求、不重试；均衡：4 次请求、同模型最多重试 1 次；抗压：6 次请求、同模型最多重试 3 次。
闸门管的是**总账**——没有它，「HTTP 重试 × 空回复重试 × 收敛重试 × 回退模型数」会相乘成十几次请求，
每次都带着全量上下文（线上为此报过 16.7 万个 token 发了三遍的账单）。

### `reasoning_overflow_retries`

**原文**：思考模型把 max_tokens 全花在 reasoning 里、正文一个字都没写出来时，追加一次
「不要复述思考、直接给最终回答」的提醒再要一枪的次数。同样的 prompt 原样重试必然同样打满，
所以这里换的是问法而不是重复；它只是尽力而为——预算真不够时仍会失败，根治办法是把模型的
「最大输出 Token」调大或缩短上下文。

### `empty_reply_retries`

**原文**：请求成功结束却既无文本也无工具调用时，同一模型再试的次数。偶现的空回复多半是
网关提前断流或只回了思考内容，重试一次即可避免把「暂时无法回答」发给用户。

### `max_tokens`（已从 Agent 配置删除）

原先这里有一个「兜底最大输出 Token」，只有在整条模型链都没设置时才生效。
v3.2 起彻底移除：**模型参数只认「Provider 预设 → 模型」**，agent 页再放一份只会制造歧义
（用户改了 16384 而实际生效 1024，日志每轮都警告"未生效"）。

---

## 组装与格式

### `prompt_preset`（提示词方案）

**原文**：三选一 —— 聊天 / 角色扮演 / 助手。它替换的是**框架生成的 system 文案**
（行为规则段、定时任务协议、消息格式说明、可用表情提示、主动性行），
**不碰人格块**：人设永远来自 `system_prompt` 或 `persona_preset_id` 指向的角色预设。
所以「换个方案」= 换说话方式，不换你是谁。

三条设计约定（实现见 `app/agent/assembly/presets.py`）：

1. **留空 = 逐字等于改造前**：未选方案时行为段仍只看 `assembly_profile`，其余文案仍是
   原来那几条常量。选方案是纯增量，清空即回退，存量 bot 升级后一个字符都不变。
2. **方案自己就是开关**：选中方案即注入该方案的行为段，不再要求 `assembly_profile=qq_agent`
   ——否则用户在本体页面点了「角色扮演」，却因为模块默认档位是 `legacy` 而拿不到行为段，
   表现就是"点了没反应"。同理表情提示与格式说明也不再被档位门控。
3. **底线不随方案变**：安全规则段三套共用且不可被方案或 `behavior_rules_override` 删掉；
   工作方式段按 `speech_via_tool` 的真实状态各写成文（说错会让模型整轮静默，或把思考发出去）；
   段落里点名的工具仍按本轮 `available_tools` 裁剪。

### 回复形态（默认：文本=思考，发言靠工具）

默认 `speech_via_tool=True`（归 `llm_output` 模块）：模型输出的文本**只是思考，不会发出去**；
要发言必须调用 `send_message`（字符串=一条、字符串数组=多条）。**什么都不调用 = 静默**，
这是正常结局（减压式聊天：看在眼里但决定不接话）。

- 关掉该开关 → 回到旧行为「模型输出的文本就是回复」；
- 与 `assembly_profile`、`stream_output` 解耦：发言通道会自己走流式策略（分条在轮次边界发送），
  不再要求"拟人化档位 + 开流式"三条件；
- 本轮模型/适配器不支持 tool calling 时**自动降级**为文本回复并留一条 warning，
  不会因为"想完没法说"变成整轮静默；
- 三套系统提示词方案都会按该形态生成行为段（`chat` 已对齐 qqbridge v2 的
  「工具是动作 / 分条自检 / 静默正常」口径）。

三套方案的分工：`chat` 混在群里的普通群友（默认口径，逐字等于 `persona_rules.render`）；
`roleplay` 以人设身份持续扮演（角色一致性 / 演绎表达 / 明确不跳出）；
`assistant` 服务型助手（有求必应 / 先给结论 / 结构化 / 不确定要说清）。

### `assembly_profile`

**原文**：`legacy` = 改造前的旧行为（逐字不变）；`qq_agent` = 注入独立的行为规则段
（安全 / 工作方式 / 反 AI 味 / 主体性 / 该说不该说 / 群聊节奏…），拟人化效果更好。

界面上改称「标准 / 旧版」，不再出现选项的内部值。

### `system_prompt_style`

**原文**：分块 = 人设/定时协议/技能/记忆/背景各一条 system（默认）；单条 = 把所有 system 块
折成一条（对齐 AstrBot 的组装形态，便于迁移比对）。

### `user_message_format`

**原文**：单行 = 与历史条目同一形态「昵称(QQ): 正文」（默认，拟人化档位下生效）；
多行 = 旧版时间/群号/发送者/发送了 六行元信息块。

---

## 上下文与指代

### `group_log_enable`

**原文**：把「群聊记录」模块持续记录的内容（群消息 + 表情回应 + 戳一戳 + 撤回 + 我的发言）
作为环境块注入上下文。它与「包含群聊环境背景」是两个来源：本项是持续记录（含互动），
后者是每次请求临时拉取的在线历史；两者正文按 `message_id` 去重，不会重复出现。

### `context_compress_enable`

**原文**：历史超过 `history_rounds` 时，只把超出的部分压缩成摘要，保留最近 `history_rounds`
条原文（仿 AstrBot）。压缩本身也要调一次 LLM，所以会额外产生 token 消耗。

### `referent_*` 三个字段

**原文**：维护会话焦点表，并在每轮请求注入「当前对话焦点」，让模型知道用户回指的
"那条/刚才的"是哪一条；同时把本会话已取回过的内容渲染为「已展开」，避免重复读取。
`referent_prefetch_enable` 命中时框架直接取回内容，不依赖模型自己决定要不要调工具。

---

## 多模态

### `gif_probe_all_images`

**原文**：默认只对「`.gif`/`.webp` 后缀、`[动画表情]` 标注、`sub_type=1`、商城表情」
这些线索命中的图片才下载确认；若 OneBot 实现不提供这些线索、动图识别不到，可打开本项让
每张图都探测一次（代价：每张图多一次下载、多一次 token）。

---

## 权限

### `agent_owner_list`

**原文**：每行一个 QQ 号。拥有者享有最高权限，可越过任何档位限制。v3 起这是拥有者身份的
唯一来源（原「账号级 `owner_id`」已迁入此处；留空则谁都不是拥有者，owner 档模块与 `#llm`
管理指令都不会响应）。

---

## 工具执行与审批

P3–P6 新增的 6 个键。前四个在「高级功能 → 工具执行」，`prompt_dump_verify` 在
「会话管理 → 上下文压缩」，`tool_approval_enable` 在「权限管理 → 高危工具审批」
（分组以 `config_partition.py` 的 `UIGroup` 为准，界面不另写一份）。

| 键 | 默认 | 取值 | 设计意图 |
|---|---|---|---|
| `tool_parallel_max` | 8 | 1..32 | 同一时刻最多几个「声明可并行」的工具一起跑；1 = 全串行。独占工具始终是屏障，结果回填顺序恒等于模型顺序——并发只省时间，不改语义。 |
| `tool_timeout_default_s` | 20 | 1..600 | 工具没声明 `timeout_s` 时的墙钟上限。超时**不 cancel** 协程（发了一半的消息、写了一半的文件比超时更糟），只把结果换成 `TOOL_TIMEOUT`，协程跑完再记一条 `_orphan` 日志。 |
| `tool_spill_enable` | True | 开/关 | 超过阈值的工具结果落盘，消息里只留 head/tail 预览 + 路径 + 取回提示。关掉时连 `recall_tool_result` 都不注入——否则是一个永远读不到东西的工具。 |
| `tool_spill_max_bytes` | 50000 | 1024..2000000 | 落盘阈值（字符）。落盘失败只 warn、保留原文：存储问题不该把一次成功调用变成失败。 |
| `prompt_dump_verify` | False | 开/关 | 每轮对话开始时核对「会话日志投影」（`SessionLog.derive_messages()`）与内存历史是否一致，不一致时落盘 diff。在线不变式自检，默认关（多一次比较，排障时才开）。 |
| `tool_approval_enable` | False | 开/关 | OneBot 来源且 `sensitivity in {high, critical}` 的工具执行前先问人。fail-closed：没有 `runtime.approval_channel` 就拒绝，只有 `ALLOWED_ONCE` 生效且仅本次。 |

### 上下文与缓存（Q1/Q2/Q5）

| 键 | 默认 | 取值 | 设计意图 |
|---|---|---|---|
| `context_window` | 0 | 0..2000000 | 模型窗口（token）。**0 = 不启用压力压缩**，回退到按 `history_rounds` 条数触发。 |
| `context_compress_trigger_ratio` | 0.8 | 0..1 | 估算输入占窗口的比例越过它就压缩。0 = 不启用（按条数）。 |
| `context_compress_keep_ratio` | 0.16 | 0..0.9 | 压缩时保留最近多少比例原文（仍受 `history_rounds` 上限约束）。 |
| `context_compress_backend` | summary | summary / prune / both | `prune` 只裁剪旧工具结果、**不调模型**；`both` 先裁再摘要。 |
| `history_mode` | recent | recent / full | `recent` 只内联最近若干条，更早的内容由 `search_history` 工具按需检索；`full` = 改造前的全量注入。 |
| `history_recent_messages` | 12 | 2..200 | `recent` 模式下注入多少条历史。 |
| `prompt_cache_enable` | True | 开/关 | 给支持显式缓存的 provider（Anthropic）在 system 末尾打缓存标记；网关不支持时关掉即回到旧请求形态。 |

> 上线顺序建议：先观察 `history_mode=recent` 的回复质量（可用 `tests/eval` 的 `-m eval` 评测集），
> 再按需打开 `context_window`。两者都可以单独回滚。

> `stream_output` / `speech_via_tool` / `context_expand_enable` 三个旧键仍是配置，
> 但「有没有这项能力」改由提供方声明（`runtime.capabilities`）决定，声明优先于配置；
> 判定链见 `docs/TOOL_STATE.md`。

---

## 迁移史（仅存档）

| 变更 | 说明 |
|---|---|
| `owner_id` → `agent_owner_list` | v3：身份从"按账号"改为"按名册"，`owner_id` 折旧为只读 `None` |
| 108 个配置键 → 9 个模块 | v3：机制留框架，策略归模块；agent 配置 160 → 40 键 |
| `model` / `temperature` / `max_tokens` 删除 | v3.2：只在 Provider 预设里配一次 |
| 本体键 45 → 51 | P3–P6 新增：并发/超时 +2、历史自检 +1、落盘 +2、高危审批 +1（以 `config_partition.CORE_KEYS` 为准） |
