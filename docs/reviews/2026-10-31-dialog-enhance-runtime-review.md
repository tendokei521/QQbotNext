# 评审 · "对话增强"插件方案（三控制面 / Conversation Runtime）

> 2026-10-31 ｜ 只评审，未动工。
> 评审对象：你贴的那份讨论稿（Context / Interaction / Delivery 三控制面 + 事件流 + ownership + action 系统 + 配置作用域）。
> 评审基线：本仓库现状（9 个 `llm_*` 插件 + 五层装配 + FeatureRegistry + bindings/authority/channel_overrides + WebUI schema 表单），
> 证据来自本轮已归档的三份底账：[`2026-10-31-assessment/01-plugins.md`](2026-10-31-assessment/01-plugins.md)、
> [`02-framework.md`](2026-10-31-assessment/02-framework.md)、
> [`2026-10-31-assembly-output-consolidation.md`](2026-10-31-assembly-output-consolidation.md)。

---

## 0. 三句话结论

1. **作为"长期架构愿景"：合理，而且大部分判断是对的**（三个维度确实独立、`ContextPackage`/`GenerationEvent`/`ResponsePlan` 三个数据契约确实该有、ownership 必须互斥、上下文快照要不可变）。
2. **作为"一个插件"：不合理**。这份方案描述的是**一套运行时/框架**，把它塞进一个插件，等于在插件里再造一个框架——而本仓库已经有这套机制（而且坏在"没人用/用错了"，不是"没有"）。这会造出**第二套真值**，正是现在 9 个插件碎片化的放大版。
3. **对本项目真正该做的是**：**一个插件 + 三个内部模块 + 复用现有扩展点**；把"接管"当成"修好现成的 `assembly`/`output` 槽"，而不是新造 ownership 系统；把"增强"的力气全部压在**群聊上下文感知**（检索/话题/回复链/参与者/压缩）上——那才是产品价值。

---

## 1. 方案里"对"的部分（并且本仓库已有对应物，别重写）

| 方案主张 | 本仓库现状 | 评价 |
|---|---|---|
| 三控制面（看什么 / 怎么用 / 怎么落地） | `assembly`（段表+方案）、`prompt_prep`（历史/工具/指代）、`delivery`（output/speech/directives） | ✅ 维度成立；但应做成**插件内部三层目录**，不是三个模块 |
| `ContextPackage` 统一中间产物 | 已有 `PromptRequest` + `ctx`（含 history/available_tools/memory_text/pre_history/extra_blocks） | ✅ 概念对；**复用它**，不要新造并行类型 |
| `GenerationEvent` 事件流归一（流式/非流式同构） | 已有 `stream/blocking` 双实现 + `tool_step` 步骤抽象，但**没有统一事件**（这正是三形态判定混乱的根） | ✅ 值得做，但属于**框架改造**（不是插件） |
| ownership 三态（observe/augment/replace），Handle 互斥 | `FeatureRegistry` 已经是"一个扩展点一个 owner + 租约 + 优先级/交棒"；observe=钩子，transform=pre/post 钩子 | ✅ 已存在；**当前坏在**：`Module` 没实现 `build()/respond()` → 租约被拒只 warning，`on_load` 手工装槽 → 运行时与租约表不一致（`controllers.py:171-192,221,261`） |
| 同一能力只能有一个执行者，防重复发送 | 现在的真实风险就是**三条发送出口**（pipeline / channel / 裸 `bot.send_*`） | ✅ 判断正确，且是必须修的真问题 |
| 上下文快照不可变 + 消息顺序 | 今天靠 debounce + 会话锁；无快照 | ✅ 群聊并发确实需要 |
| 配置作用域（全局→机器人→群→请求） | **已有**：全局默认（`DEFAULT_LLM_CONFIG`）+ 账号（`bindings.config_json`）+ 群（`channel_overrides`）；**没有**请求级 | ✅ 三层已有，方案说"尚未设计"是没看代码 |

---

## 2. 方案里"不成立 / 过重"的地方

### 2.1 规模与需求不匹配

需求是"一个插件增强群上下文感知 + 允许接管流式"。方案给出的是：3 控制面 × 4 维度 ×（事件流 + action 系统 + ownership 体系 + 作用域合并 + 幂等 + 并发 + 可观测）= **一个框架的体量**。
一个插件吃不下：本仓库最大的插件（memory）也就 4.6k 行；照这份方案做，单插件至少 6–10k 行、配置 60+ 键——比现在的 9 个插件加起来更难维护，而且没有第二个人能看懂。

### 2.2 与现有框架大量重复 → 第二套真值

方案默认"框架只提供原子操作"，但本仓库的框架**已经提供了整套中间层**：

| 方案要新造 | 本仓库已有 |
|---|---|
| 模型策略 / 多模型路由 / fallback | Provider 预设 + model 池 + `provider_chain`（`llm_retry`/`llm-deepseek`） |
| 权限体系（查询范围/操作授权） | `authority` + `permission` + 高危工具审批通道（fail-closed） |
| 会话与历史存储 | SQLite 会话 + `session_manager` + 历史压缩/指纹/前缀缓存 |
| 事件总线 / 生命周期 | `@llm_hook` 5–6 阶段 + `@module_hook` + `@bot_lifecycle_hook` |
| 发送与流式传输 | `StreamOutput`/`BlockingOutput`/`SpeechOutbox` + 发送池 + 指令解析 + `pre_send/post_send` |
| 消息拆分 / @ / 引用 | `directives.py` + `OutboundDirectives` |
| 冷却 / 频率 | `reply_cooldown`、`poke_cooldown_seconds` |
| 工具执行（含 175 个 OneBot 工具） | `llm_tools_extra` 的工具源 + 权限分级 + 审批 |

**只要插件内再实现一遍，就会出现"插件说的权限"和"框架的权限"两套**——这正是本项目现在的病（8 处"读错来源"、同一概念 4 个默认值）。

### 2.3 配置体系落不了地（最硬的约束）

方案给的是嵌套 YAML（`sources/retrieval/enrichment/assembly/compression`、`actions.allowed[]`、`ownership.*`）。
**本仓库的 WebUI 只支持平铺 schema**：`boolean / number / select / textarea / string_list / group / showIf`，
**没有嵌套对象编辑器、没有 multiselect**（`app/plugin/schema_utils.py:20-107`、`ConfigForm.vue:126-146`）。
也就是说：这套配置**写不进界面、无法校验、无法解释**，只能落到 JSON 文件里手改——这正好回到"开关说不清、改了没反应"的老路。

### 2.4 把三处核心都 Handle 掉，会把其它模块全部打穿

`ownership: { context.build: handle, model.generate: handle, message.deliver: handle }` = 框架退化成"能力库"。
但本仓库其它模块（记忆、群记录、工具、触发、外置 12 插件、WebUI 统计/遥测）**全都挂在框架钩子上**：

- `model.generate` 一旦被插件 Handle，`llm_hook` 全阶段、token 预算、空回复重试、审批、遥测**全部失效或被绕过**；
- `context.build` 一旦 Handle，`register_section` / `context_providers`（记忆/指代）全部失效；
- `message.deliver` 一旦 Handle，`pre_send`/`post_send`/`msg_out` 记账/`message_id` 回填/群记录"我的发言"入账全部丢失。

这不是理论风险：现在的主动/定时路径就是"传错 runtime → 插件装配器/section/钩子/预算/dump 全部静默失效"（`02-framework.md §6.3`）。把局部故障放大到全局，就是这份方案的默认形态。

### 2.5 权重放错了：真正该做的被放在"缺少的问题"里

方案 §7 才提"群聊上下文 ≠ 会话历史、话题识别、并发快照"——**这恰恰是这个项目的核心价值**。
而方案的篇幅主要给了：事件流、ownership、action 系统、幂等、配置合并——这些是**框架已做或该在框架里做**的东西。
一句话：**它在修地基，而你要的是加房间。**

### 2.6 几个具体的技术判断值得商榷

| 方案主张 | 本项目实际 |
|---|---|
| `streaming.strategy: edit_message` 作为首选 | 需要 QQ 适配器支持"编辑消息"；本项目走 OneBot v11 反向 WS，**没有可靠的消息编辑能力**（可用的是分条发/整段发）。按方案会先做一个用不了的功能 |
| 主动 Action 结构化（poke/emoji/sticker） | 已有 `send_poke`/`set_msg_emoji_like` 工具（带权限/冷却/审批）。正确做法是**插件门控、复用执行器**，不是另造 Action 执行器 |
| 请求级配置覆盖 | 当前架构（bindings + channel_overrides）没有请求级槽位；引入它会让"同一个群同一分钟两条消息配置不同"变成不可解释的故障源。建议**不做** |
| `max_parallel: 3` 等并发参数 | 单进程 + 单账号 + 会话锁；真正需要的是 debounce 合并与会话锁（已有），并行模型调用反而会打乱顺序 |

---

## 3. 你贴的方案末尾问了三个实现问题，直接答（都是本仓库事实）

**Q1 插件与框架的关系？**
**同进程、直接调用**。插件是 Python 包（`app/plugins/<name>/main.py` 导出 `class Module(BaseModule)`），loader 按文件路径加载、并以合成包名 `qqbot_plugin_<name>` 注入 `sys.modules`。
边界纪律：插件只通过 `app.plugin`（插件 API）与 `app.agent`（tool/skill）使用框架，**不得** import `app.webui`/`app.bootstrap`，插件之间不得互相 import。没有 RPC。

**Q2 历史与会话状态由谁管？**
**框架管**。会话存 SQLite（`data/app.db`），历史读写走 `session_manager`；插件通过 `self.sessions`（读写当前会话）、`self.llm_turn(...)`（无事件的完整一轮）使用；群记录由外置插件 `group_log` 写入、框架的 `group_log` 块渲染。
**不要让插件接管存储**：历史指纹、前缀缓存、压缩、消息 id 回填全依赖框架那份数据，接管即失配。

**Q3 插件执行方式是哪种？**
**混合，且这是本仓库的强项**：
- 钩子：`@llm_hook`（pre_request / post_response / pre_send / post_send / post_stream / turn_completed）+ `@module_hook` + `@bot_lifecycle_hook` + `@send_hook`；
- 注册制：`register_section`（提示词段）、`context_providers`（上下文块，支持 `role=system/user`）、`tool_sources`（工具）、`@skill`；
- 槽接管（互斥）：`assembly`（实现 `build(req)`）、`output`（实现 `respond(ctx)`）、`provider_invoker`（实现 `chat/stream`）；
- 能力声明：`capabilities`（声明"本运行时能输出什么"），消费方 `tools/policy.py`。
**当前唯一坏的环节**：槽接管要求模块自身实现契约方法，而现有插件用 `on_load` 手工赋值 → 租约被拒只 warning。**修这里，不要另造 ownership 系统。**

---

## 4. 如果真做"一个插件 对话增强"，我建议的形态

### 4.1 一个插件，三个内部目录（不是三个模块）

```
app/plugins/dialog_enhance/
├── main.py            # Module：一个插件；声明接管 output（+ 可选 assembly）；注册 providers/tools/sections
├── config_schema.py   # 一份平铺 schema（WebUI 可直接渲染）
├── service.py         # 组装三层能力的门面
└── src/
    ├── context/       # 看什么：来源、筛选、增强、组织、压缩 → 产出 ContextPackage
    ├── interact/      # 怎么用：请求协议、能力（历史检索/展开/指代）、预算 → 产出事件流
    └── deliver/       # 怎么落地：渲染、分条、流式呈现、动作门控 → 产出 ResponsePlan
```

对外它仍是**一个插件**（一个模块卡片、一份配置）；对内三层各自有数据契约，好测试、好换实现。

### 4.2 复用而不重造

| 想做的事 | 用现成的 |
|---|---|
| 上下文材料进 prompt | `context_providers`（`role="user"` 放资料块）+ `register_section` |
| 模型主动查历史 | `tool_sources` 注册 `search_history` / `get_message` / `expand_*` |
| 接管流式/发言通道 | `supersedes=("output",)` + `Module.respond(ctx)`（修好租约后） |
| 接管装配（可选） | `supersedes=("assembly",)` + `Module.build(req)` |
| 权限/审批/冷却 | `authority`、审批通道、`send_poke` 自带冷却 |
| 发送/分条/@/引用 | `delivery.directives` + 唯一发送出口（先修三条出口问题） |
| 模型/预算/重试 | Provider 预设 + provider chain + token 预算 |

**不重造**：模型路由、存储、事件总线、权限体系、发送传输层、175 工具执行器。

### 4.3 配置规模：一份 schema、三组、≈18 键

| 组 | 键（示意） | 说明 |
|---|---|---|
| 上下文（6–8） | 来源开关（消息/回复链/成员/互动事件/附件）、最近消息窗口、相关消息检索（开/条数）、群记录窗口、时间呈现、身份呈现、压缩策略 | 全部可用 `select/boolean/number` 平铺表达 |
| 能力（4–6） | 历史检索（开/范围/条数）、消息展开（开/预算）、指代消解（开/策略）、图片是否给模型看 | 每个都注册成工具 + 一行提示文案 |
| 发送（4–6） | 回复形态（一次/逐句/先想后发）、条数与字数、清洗强度、能否静默、附加动作（表情/戳/拍 开关+频率） | 接管 `output` 槽后由插件执行 |

**不出现**：`ownership.*`（用 `supersedes` 声明）、`actions.allowed[]`（用现有工具开关）、嵌套 sources 结构（用平铺键 + `group` 分组）。

### 4.4 必须先修的三件框架事（否则插件再对也跑不对）

1. `ConfigView`/`ChainedConfig` 短路（模块配置永远读不到）——**已实测复现**；
2. 槽接管契约（`Module.build/respond`）+ 接管失败 fail-loud（现在是 warning + 手工装槽）；
3. 发送出口收敛为一条 + `turn.py` 补发言通道判定（否则"先想后发"仍会整轮静默）。

---

## 5. 我要跟你确认的四件事（这几个定了，方案才能落到"可开发"）

1. **"对话增强"要吸收哪些现有插件？** 我的建议：吸收 `llm_assembly` + `llm_message_meta` + `llm_context` + `llm_output` + `llm_enhance`（这 5 个正好覆盖"看什么/怎么用/怎么发"）；`llm_memory`、`llm_tools_extra`、`llm_proactive`、`llm_schedule` 保留为独立模块并由它复用。要不要连记忆一起吸收？
2. **要不要让它接管 `assembly` 槽？** 接管 = 提示词完全由它拼（能力最大、风险最大）；不接管 = 只注册段和资料块（更安全）。我建议**先只接管 `output`**，装配先用注册制，跑稳后再谈接管。
3. **"流式输出"你指的是哪一种？**（a）模型逐 token 收（请求侧）；（b）逐句发到群里（发送侧）；（c）边生成边编辑同一条消息（需要 QQ 支持编辑，本项目大概率没有）。我建议只做 (b)。
4. **配置面能接受多大？** 一份 `dialog_enhance` schema 大约 15–20 键；超过 25 键就要拆成"基础/高级"两页，否则又回到"开关太多没人懂"。

---

## 6. 和上一轮"三个模块"方案的关系

不冲突，反而可以合并：**上一轮的三份配置设计，正好降级成这一个插件内部的三个 schema 分组与三个目录**。
- 三个模块 → 一个插件的三层（对外只有一个模块卡片）；
- `RequestPlan` / `ContextPackage` / `ResponsePlan` → 插件内部的三个数据契约；
- 能力事实表（CapabilityFacts）→ 仍然要，它防的是"提示词说 A、代码做 B"（真实"不回"的成因）。

**唯一要警惕的是**：三层都在一个插件里时，"配置谁负责、代码谁负责"更容易糊在一起 → 所以三个目录必须有**单向依赖**：`deliver → interact → context`，不许反向。
