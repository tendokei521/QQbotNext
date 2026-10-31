# 计划 · 把 9 个 LLM 插件浓缩成 3 个核心模块

> ⚠️ **已取代**：本方案按"一轮请求的生命周期"切（组装→发送→触发），已被
> [`dialog-modules.md`](dialog-modules.md) 取代（那版按"用户能改的点"切成 组装/交互/发送）。
> 仍然有效的部分：§2.3 槽接管路线 A、§3 框架侧 5 个 P0 修复、§6 迁移机制、§7 降级契约。
> 已作废的部分：TurnPlan、按生命周期划分的模块边界、§5 的旧键映射口径。

> 状态：**只出方案，未动工**（本轮不改任何 `.py`；`git status` 干净）。
> 写作时间：2026-10-31 21:5x ｜ 基线 HEAD：见 `git log -1`（含另一会话正在改的 `assembly/` WIP）。
> 上游评估：[`../reviews/2026-10-31-assembly-output-consolidation.md`](../reviews/2026-10-31-assembly-output-consolidation.md)
> 两份底账（已归档，本文的证据来源）：
> [`../reviews/2026-10-31-assessment/01-plugins.md`](../reviews/2026-10-31-assessment/01-plugins.md)（109 键逐键）、
> [`../reviews/2026-10-31-assessment/02-framework.md`](../reviews/2026-10-31-assessment/02-framework.md)（17 个判定点）。
> 两份设计输入（已随本文归档）：
> [`three-core-modules-detail/04-keymap.md`](three-core-modules-detail/04-keymap.md)（109 键逐键映射 + 三份 schema 草稿 + 迁移伪代码）、
> [`three-core-modules-detail/05-skeleton.md`](three-core-modules-detail/05-skeleton.md)（骨架/槽接管/迁移机制，483 行全带 file:line）。

---

## 0. 一句话

**把"按功能名切"的 9 个 LLM 插件，重切成"按一轮请求的生命周期阶段切"的 3 个核心模块**：
`llm_prompt`（发给模型什么）→ `llm_delivery`（模型的话怎么发出去）→ `llm_trigger`（这一轮由谁发起）；
三者只通过一个**框架持有的 `TurnPlan`** 通信，17 个"是否发言"判定点收敛成 1 个。

**不是重写功能，是重写边界**：五层装配、speech outbox、capabilities、section/provider 注册表全部复用。

---

## 1. 范围

### 1.1 改什么

| 旧模块 | 现状键数 | 去向 |
|---|---|---|
| `llm_assembly` | 7 | → `llm_prompt` |
| `llm_message_meta` | 14 | → `llm_prompt` |
| `llm_enhance` | 1 | → `llm_prompt`（`debug_prompt` 并入"请求落盘/打印"组） |
| `llm_output` | 24 | → `llm_delivery` |
| `llm_proactive` | 17 | → `llm_trigger`（其中 5 个 `*_prompt*` 键归 `llm_prompt`，1 个 `stream_proactive_enabled` 归 `llm_delivery`） |
| `llm_schedule` | 3 | → `llm_trigger`（`stream_scheduled_enabled` 归 `llm_delivery`） |
| `llm_context` | 10 | **保留**（能力模块）；仅 `referent_prompt_enable` 归 `llm_prompt` |
| `llm_memory` | 23 | **保留**（唯一全部键都有正确读取点的模块） |
| `llm_tools_extra` | 10 | **保留**（修 1 个读错来源） |
| `llm_knowledge` | 3 | **孤儿**：目录已删、bindings 有行、`MODULE_KEYS` 仍占位 → 见 §10-Q5 |

**结论：9 → 3 个核心 + 3 个能力模块 = 6 个。** 记忆/上下文/工具不并入核心的三个，理由：
它们已经挂在 `context_provider` / `tool_source` 的正确接缝上（不是"组装/输出"），并入会得到一个 ~8000 行、
配置 63 键的单模块，把"聚合"变成新的"不透明"。

### 1.2 不改什么

- 五层装配模型（L1 系统提示词 / L2 人格 / L3 注入 / L4 历史 / L5 本轮）—— 已经收敛，见 `docs/plan/prompt-assembly-refactor.md`。
- `BlockingOutput` / `StreamOutput` / `SpeechOutbox` 三个实现 —— 只挪归属，不改行为。
- 人格预设 / 提示词方案（三套）的文案体系 —— 只在 `llm_prompt` 里接管，不重写文案。
- Provider 链、工具循环、会话历史、群记录 —— 不动。

### 1.3 三处前提校正（比评估文档更准）

| 评估里说的 | 实测（本轮） | 影响 |
|---|---|---|
| 账号 123 有 9 行 `llm_*` bindings、4 行有配置 | **10 行**（含孤儿 `llm_knowledge`）、**5 行有配置**、**3 行 enabled=1** | 迁移脚本要处理"有行无模块" |
| `module_config` / `module_authority` 是只读迁移源 | **两张表都已 0 行**，迁移早已跑完 | 迁移输入只能是 `bindings` |
| 迁移链在 `start.py` | 在 **`app/bootstrap.py::run()`**（`start.py` 只是 venv 启动器） | 插桩点定在 `config_service.init()` 之后、`registry.discover()` 之前 |

---

## 2. 目标结构

### 2.1 三个模块的职责（硬边界）

| 模块 | 一句话职责 | 拥有的东西 | 明确不做 |
|---|---|---|---|
| **`llm_prompt`**<br>「提示词组装」 | 决定**发给模型的 messages 长什么样** | 段表与层、三套方案文案、行为规则、人格落位、user 行格式、元信息（时间/发送者/引用/@）、资料块落位、请求落盘与打印 | 不决定"发不发"、不发送、不管触发 |
| **`llm_delivery`**<br>「发言与输出」 | 决定**模型产出怎么变成群里的消息** | 输出形态 `mode`、发言协议文本、`send_message`/`finish` 工具、分条/上限/补一轮、流式节奏、**唯一发送出口**、静默语义 | 不拼提示词、不管触发 |
| **`llm_trigger`**<br>「主动与定时」 | 决定**这一轮由谁发起** | 主动消息（冷场/私聊/免打扰）、定时任务（每天/每周/每月）、触发轮的统一入口 | 不拼提示词、不发送（复用 delivery 的出口） |

三者的调用关系：

```
llm_trigger ──发起(chat|proactive|schedule)──┐
                                           ▼
                              resolve_plan(runtime, source)   ← 框架，唯一构造点
                                           │  TurnPlan（frozen）
                    ┌──────────────────────┼──────────────────────┐
                    ▼                      ▼                      ▼
             llm_prompt.build(req)   llm_delivery.respond(ctx)   loop/pipeline
             （消费 prompt 字段）     （消费 speak 字段）          （发送/历史/遥测）
```

### 2.2 `TurnPlan`：框架持有，三个模块只读

```python
# app/agent/plan.py（新增，框架层；不放进任何模块，否则会出现"模块 import 模块"）
@dataclass(frozen=True)
class TurnPlan:
    source: Literal["chat", "proactive", "schedule"]
    runtime: AgentRuntime                  # ★ 永远是真 runtime，不是 BaseModule

    # 组装侧（由 llm_prompt 的配置算出）
    preset: str                            # chat|roleplay|assistant（显式，可进日志）
    sections: tuple[SectionSpec, ...]       # 本轮段序 + 每段文案来源
    user_line_format: Literal["single_line", "legacy"]
    time_position: Literal["head", "tail", "off"]
    meta_fields: frozenset[str]             # sender/time/quote/at/mention
    dump: DumpOptions                       # 落盘 or 打日志 or 都关

    # 发送侧（由 llm_delivery 的配置算出）
    speak_mode: Literal["text", "stream", "speech"]
    protocol_text: str | None               # 贴不贴发言协议，贴什么
    persona_conflicts: tuple[str, ...]      # 本轮必须从人格卡剥掉的输出格式条款
    bubbles: SpeakLimits                    # max_bubbles / max_chars / force_reply
    pace: StreamPace | None                 # 仅 stream / speech
```

**为什么要它**：现在"这一轮怎么说话"由 17 个判定点分别回答（7 通道 + 8 结果 + 2 缺失），
彼此可以不一致（已确认 3 条不一致链：非流式静默、思考外泄、主动/定时口径反转）。
`TurnPlan` 让"决定"只发生一次，"执行"只读不改。

**归属纪律**（写进架构测试）：
1. `TurnPlan` 只由 `resolve_plan()` 构造，模块**不得**构造或改写；
2. 除 `resolve_plan` 与两个新模块外，**任何代码不得再读** `speech_via_tool` / `stream_output` /
   `user_message_format` / `prompt_time_position`（grep 闸门锁死，白名单=迁移层）；
3. 每轮必须打一行 `TurnPlan` 摘要（`source / preset / speak_mode / sections / spoken / silent_reason`）——
   现在的 `[Prompt]` 只打段长度和 `prefix` 指纹，**没法回答"这轮用了哪套方案、哪种输出"**。

### 2.3 槽接管：走路线 A（Module 自己实现契约）

现状：`llm_output`/`llm_assembly` 声明了 `supersedes`，但 FeatureRegistry 要求 owner 自身有
`respond()`/`build()`（`app/plugin/features/controllers.py:142,221,261`），`Module` 没有 →
**租约被拒只打 warning**；真正接管靠 `on_load` 手工赋值（`llm_output/main.py:105-132`），
于是运行时被接管了、租约表里查不到、UI 显示的"接管状态"是假的。

| 路线 | 改动 | 评价 |
|---|---|---|
| **A（推荐）** | `Module.build(req)` / `Module.respond(ctx, *…)` 各 6–10 行，转调 `service.py` 的能力对象；`controllers.py` **零改动**；删掉 `on_load` 里的手工赋值 | 与 `docs/FEATURE_REGISTRY.md:91-114` 已写明的契约一致；产品代码净增 ≈30 行 |
| B | 让 FeatureRegistry 支持注册"能力对象" | 要改 `features/base.py + controllers.py + registry.py` ≈120 行，还得留"owner 自身合格"兼容分支；当前没有场景需要 |

⚠️ **不允许**给 `BaseModule` 加空 `respond()` —— `_usable()` 只查 callable（`controllers.py:142`），
加了会让所有模块都"合格"，校验彻底作废。

**A 只解决"能接管"；"接管失败必须可见"另配四道闸**（阶段 1，先于模块落地）：

| 闸 | 行为 |
|---|---|
| G1 启动期契约校验 | `supersedes` 里的能力找不到控制器、或 owner 缺方法 → **error + 记录 load_error + 不建立租约**（现在 `loader.py:357-361` 是无条件继续） |
| G2 拒绝结果回传 | `acquire_module()` 回传被拒项，`activate` 写进实例状态 |
| G3 告警升级 | `warning` → `error`，文案必须含"该能力不会被接管"与下一步 |
| G4 可查询/可展示 | `/modules/features` 带 `health / missing_methods`；模块卡片红条；就绪清单加一项 |

---

## 3. 框架侧必须先做的 5 个修复（阶段 0，独立于插件边界）

这 5 条不修，模块合并只是"把坏实现搬进新文件夹"：

| # | 修什么 | 位置 | 为什么必须先修 |
|---|---|---|---|
| 0.1 | `ConfigView` 对非 owned 键返回哨兵、`ChainedConfig` 不再被首个视图短路 | `app/agent/policy.py:70-85`、`app/agent/delivery/output.py:256-278` | 实测复现：`llm_output` 一装，排在后面的 `llm_assembly` 配置**永远读不到**（拿到的是 agent 旧值）。新模块同样会被短路 |
| 0.2 | 新增 `app/agent/plan.py`：`TurnPlan` + `resolve_plan()` + 每轮摘要日志 | 新增；接 `prompt_prep.py` / `manager.py` / `scheduler.py` | 17 → 1 的落点；也是"静默必留痕"的载体 |
| 0.3 | 主动/定时/模块轮统一：传真 runtime（不是 `BaseModule`），config 用 `policy_view` | `llm_proactive/src/manager.py:238,286`、`llm_schedule/src/scheduler.py:448,516`、`build_for`/`apply_post_assemble` 调用点 | 现在 `build_for(self.module, …)` 传的是 `BaseModule`，没有 `assembler/prompt_sections/llm_hooks/memory_service/provider_chain` → 主动/定时路径上**行为段整段消失**、钩子/预算/dump 全失效 |
| 0.4 | 三条发送出口收敛成一条 | 新增 `app/agent/delivery/exit.py`；`pipeline._send` 改用它；`manager.py:391`、`proactive/stream.py:110` 切过来 | 现在只有 chat 那条有 `pre_send`/指令解析/`msg_out`/`message_id` 回填；主动/定时裸 `bot.send_*` 不解析 `[reply]`，而提示词还在教模型写 `[reply]` |
| 0.5 | `turn.py` 补发言通道判定与 flush（或对 speech 明确 fail-fast） | `app/agent/loop/turn.py:306,419-500` | `turn.py` 全文无 `speech`：非流式 + speech → 气泡丢弃、`result.text=""`、**连"静默"日志都不打**；`stream=True` → 思考外泄 |

---

## 4. 实施批次（一步一提交、可独立回滚）

> 顺序原则：**先让"现有 9 个插件"语义正确（阶段 0）→ 再让"接管可见"（阶段 1）→ 再一个个换模块（阶段 2-4）→ 最后收尾（阶段 5）**。
> 迁移**增量做**（每个模块落地时带上自己的键映射），不攒到最后一次性迁——否则中间态会跑在"新模块默认值"上，行为会突变。

| 批 | 提交 | 内容 | 关键验收 |
|---|---|---|---|
| **0** | 1 | `fix(policy): 视图只答自有键，修 ChainedConfig 短路` | 存模块值 → `policy_view.get` 读得到 |
| | 2 | `feat(agent): TurnPlan 单一决策对象 + 每轮摘要日志`（先只读，不改行为） | 日志出现 `[TurnPlan] source/speak_mode/...` |
| | 3 | `fix(proactive,schedule): 传真 runtime + policy_view` | 主动/定时 prompt 里**行为段回来了** |
| | 4 | `fix(delivery): 发送出口收敛为 send_outbound` | 主动/定时消息走 `pre_send`、`[reply]` 被解析、`msg_out` 有记账 |
| | 5 | `fix(turn): 发言通道判定 + flush / fail-fast` | speech + 非流式不再静默；或不支持时**显式报错** |
| **1** | 6 | `fix(plugin): 接管失败升级为 error + load_error`（G3） | 声明接管却缺方法 → 模块 status=error |
| | 7 | `feat(plugin): 启动期校验接管契约，拒绝时不留租约`（G1/G2） | 拒绝的模块不再"显示已启用" |
| | 8 | `feat(webui,readiness): 暴露接管 health`（G4） | `/modules/features` 有 health；就绪清单有该项 |
| **2** | 9 | `feat(plugin): 新建 llm_delivery（接管 output 槽）` + 旧 `llm_output` 变 shim + 键迁移 | 三形态行为与现在**逐项等价**（text/stream/speech 各一条契约测试） |
| **3** | 10 | `feat(plugin): 新建 llm_prompt（接管 assembly 槽）` + `llm_assembly`/`llm_message_meta`/`llm_enhance` 变 shim + 键迁移 | 同配置下 messages **与现在逐字一致**（快照比对，除已知要修的差异） |
| | 11 | `feat(prompt): 元信息渲染与图例同源（不再依赖 meta 插件开关）` | 关掉任何子开关，图例与实际格式仍一致 |
| | 12 | `feat(delivery): speech 模式自动剥离人格卡输出条款` | 人格含"输出纯文本" + `mode=speech` → 条款被剥、plan 标 conflict |
| **4** | 13 | `feat(plugin): 新建 llm_trigger（主动+定时统一入口）` + 两个旧模块变 shim + 键迁移 | 定时/主动走同一 `resolve_plan` 与同一出口 |
| | 14 | 旧行禁用（`enabled=0`）+ 一次性清理孤儿/无归宿键 | 迁移报告 `dropped` 逐条落盘，无静默丢弃 |
| **5** | 15 | `chore(config): 重写 config_partition 归属 + 删化石键` | `unknown_keys()==[]`、`stale_keys()==[]`、`0 读取键==0` |
| | 16 | `docs(plugin): MODULE_DEV / FEATURE_REGISTRY / 配置页文案同步` | 文档与代码一致 |

**shim 与删除**：旧 9 个目录在 T0 改成 shim（`hidden=True`、`provides=()`、`supersedes=()`、
`on_load` 只打 deprecated、不 import 新模块、不接管任何能力）；观察 ≥2 个版本后，
用一个独立提交删 shim 目录 **+ 同提交删对应测试**（AGENTS.md 纪律）。

---

## 5. 配置收敛

### 5.1 目标键数（逐键账已闭合）

| 模块 | 现在 | 目标 | 说明 |
|---|---|---|---|
| `llm_prompt` | `llm_assembly` 7 + `llm_message_meta` 14 + `llm_enhance` 1 + `llm_proactive.*_prompt*` 5 + `llm_context.referent_prompt_enable` 1 = **28** | **12** | 见 §5.2 的 8 组合并 |
| `llm_delivery` | `llm_output` 24 + 主动/定时流式开关 2 = **26** | **12** | `mode` 唯一事实源；节奏键用 `showIf` 按 `mode` 显隐 |
| `llm_trigger` | `llm_proactive` 11 + `llm_schedule` 2 = **13** | **10** | 主动 8、定时 2 |
| 三个核心合计 | **67** | **34** | |
| 能力模块（不动） | `llm_context` 9 + `llm_memory` 23 + `llm_tools_extra` 10 = **42** | 42 | 勘误：`memory_inject_recency` 被底账漏登，实为 24 → 43 |
| **总计** | **109** | **76**（含勘误 77） | |

算式（逐键核对，不重不漏）：**保留 42 + 并入（52 旧键 → 34 新键）+ 退役 15 = 109**。
其中退役 15 条 = 僵尸 3（全仓 0 读取）+ 常量内化 9 + 重复事实源/条件不可达 3，**每条都有替代方案**。
**纯新增键 0 个**：34 个新键每个都能追溯到 ≥1 个旧键。

### 5.2 必做的 8 组合并（现在最典型的"开关说不清"）

| # | 概念 | 现在的来源 | 合并成 |
|---|---|---|---|
| 1 | 时间 | `prompt_time_position`（三态，代码只认 `head`）+ `include_time`（bool） | `time_position`：`head / tail / off`（修正"off 无效、位置恒 tail"） |
| 2 | 本轮行格式 | `user_message_format` + `meta_sender_style` + `meta_sent_style` + `meta_instruction_mode` | `user_line_format`：`single_line / multi_line / multi_line_verbose`（词表=代码真实词表）；**图例由格式推导，不再单独开关** |
| 3 | 元信息项 | 6 个 `include_*` + 2 个 `fetch_*` | 4 个：`meta_sender / meta_mention / meta_quote / meta_include_body`（同轴合并，`ref_only` 是"只取引用不取正文"的显式化） |
| 4 | 提示段开关 | `proactive_*_nudge` 等 7 个 | `prompt_nudges`（多选，一次看全） |
| 5 | 记录请求 | `prompt_dump_enable` + `prompt_dump_max_entries` + `debug_prompt` | `prompt_record`：`off / file / log / both`（`max_entries` 内化为常量 20） |
| 6 | 输出形态 | `mode` + `stream_output` + `speech_via_tool` | 只留 `mode`；后两个仅迁移期读 |
| 7 | 流式节奏 | 19 个 `stream_*`（2 个 0 读取、9 个默认关时全是装饰） | `stream_pace`（6 档真实词表）+ `base_ms` + `max_ms` + 池开关 + 句长 + 前后缀；`stream_send_by_sentence`/`stream_keep_order` **删且不补实现**（切分恒在 `tool_step.py:309`，池天然 FIFO） |
| 8 | 触发器 | `proactive_*` 13 个 | 10 个（会话白名单、间隔、免打扰、冷场、最多未答、提示词、定时开关、定时提示词） |

**关键手段：`showIf`**（后端 `config_schema.py:1006-1039`、前端 `ConfigForm.vue:126-146` 都已支持）——
`mode=text` 时节奏键直接不显示、`schedule_enable=false` 时定时提示词不显示。
这是"开关有什么用"最直接的答案：**不生效的开关不出现**，而不是显示着让人猜。

### 5.3 删除 / 迁移清单

完整的 109 键逐键表（命运 / 新键 / 新默认 / 转换函数 / 冲突裁决 / 三份 schema 草稿）在
[`three-core-modules-detail/04-keymap.md`](three-core-modules-detail/04-keymap.md)
（1149 行：§2 逐键总表、§3 八组合并裁决、§4 退役清单、§5 schema 草稿、§6 迁移伪代码）。
代码里落成一张纯函数表 `KEYMAP`：`旧键 → (新模块, 新键, 转换函数)`，
迁移脚本与回归测试共用同一张表（**不允许两处各写一份**）。

两处底账勘误（不改变主算式，但迁移表必须照此处理）：

1. `llm_memory` 实为 **24 键**（底账漏登 `memory_inject_recency`，`llm_memory/src/schemas.py:162` 定义、
   `src/manager.py:311` 真读）；它留在能力模块，只让保留组从 42 变 43（总计 76 → 77）。
2. `speech_via_tool` **不在 109 个 schema 键里**（它已被从表单隐藏），但它是老配置推导 `mode` 的唯一依据，
   必须留在 `KEYMAP` 里。

原则：
1. **不静默丢**：无归宿的键写进迁移报告的 `dropped`，并落盘（沿用 `scripts/migrate_v3_split_config.py:122-130` 的纪律）；
2. **词表以代码为准**：schema 里写的选项如果代码不认（4 处已确认），迁移时就按代码词表改写并打日志；
3. **默认值只留一处**：`DEFAULT_LLM_CONFIG` 里那批已归模块所有的键删掉，模块 `default_config` 是唯一默认值。

---

## 6. 数据迁移

| 项 | 方案 |
|---|---|
| 脚本 | 新增 `app/application/core_modules_migration.py`：`async def migrate_core_module_bindings(config_service) -> MigrationReport` |
| 插桩 | `app/bootstrap.py::run()` 的 `config_service.init()` 之后、`registry.discover()` **之前**（`bootstrap.py:520 / :576`）；**不是 `start.py`** |
| 幂等 | 双保险：`kv[MIGRATION_FLAG]` + "新行已有键优先"合并（重跑天然收敛）；flag 只在全部账号成功后写 |
| 回滚 | 跑前备份 DB（`backups/app.core-migration.<ts>.db`）+ 旧行整体快照进 `kv`；`--rollback` 从快照重放；`--dry-run` 只打 plan |
| 旧行 | **只 `enabled=0`、保留 config 作回滚快照，不删**（`bindings` 没有元数据列，无法标 deprecated） |
| 启用位裁决 | `llm_prompt = OR(assembly, message_meta)`、`llm_delivery = output`、`llm_trigger = OR(proactive, schedule)`；`llm_enhance` **不参与**（否则调个 debug 就把整个提示词模块打开） |
| 孤儿 | `llm_knowledge`（有行无目录）：3 个键进 `dropped` 并落盘，等 §10-Q5 拍板 |
| 只建已有行的账号 | `bot1` 没有任何 `llm_*` 行 → 迁移后保持"无行=默认关"，不要凭空给它建行 |

---

## 7. 降级契约（这次要治的病）

现在"禁用模块"= 静默失去能力，用户没有任何提示。三个新模块各自必须声明"我没了会退化成什么"：

| 模块 | 禁用/加载失败后 | 日志 | UI |
|---|---|---|---|
| `llm_prompt` | 槽不被接管 → `runtime.assembler` 是内置 12 段表；user 行格式回默认；**元信息图例不注入** | WARNING 启动一条 + 每轮 debug 一行 | 模块卡片灰 + 红条"提示词组装使用内置默认" |
| `llm_delivery` | `runtime.output=None` → 按 agent 行 `stream_output` 选 `BlockingOutput`（一次性发） | WARNING 启动一条 | 红条"发言方式回退为一次性发送" |
| `llm_delivery`⚠️ | **disabled 但 agent 行残留 `speech_via_tool=true`** → 贴协议不 flush → **复现真实静默** | **ERROR + fail-loud**：强制 BlockingOutput 并摘掉 `send_message` 工具，绝不静默 | 首页红条 + 就绪清单 blocker |
| `llm_trigger` | 主动/定时不跑，已存任务不执行（数据保留） | INFO 启动一条 + 每个跳过点 debug | "主动与定时"页红条 + 任务标"未生效" |

**明确不允许的降级**：租约被拒但 `on_load` 手工接管；模块显示已启用实际没接管；
贴了发言协议却没有输出实现；"没发出去"没有任何日志。

---

## 8. 验收

### 8.1 契约测试（新增/替换，删模块时同提交删旧测试）

| # | 契约 | 断言要点 |
|---|---|---|
| 1 | 模块配置保存后读得到 | `llm_prompt.user_line_format=single_line` → `policy_view.get()` 返回 single_line |
| 2 | 发言语义单源 | `mode=text`：无协议文本、无 `send_message` 工具；`mode=speech`：两者同时存在 |
| 3 | 人格冲突被处理 | 人格含"输出纯文本" + `speech` → 最终 messages 里该条被剥，`plan.persona_conflicts` 非空 |
| 4 | 三条来源同一 plan | chat / proactive / schedule 的 `plan.preset`、`speak_mode`、段名集合一致 |
| 5 | 静默必留痕 | 任何路径没发出去 → 一行日志含 `source/speak_mode/spoken=0/reason` |
| 6 | 前缀稳定 | 同配置连续两轮 system 前缀逐字节一致（扩展到主动/定时） |
| 7 | 迁移幂等 | 跑两次第二次 0 变化；不覆盖新行已有值；无行模块不被默认值误开；孤儿键落 `dropped` |
| 8 | 零僵尸键 | 静态扫描：schema 键 = 归属表键 = 有真实读取点的键 |

### 8.2 量化目标

| 指标 | 现在 | 目标 |
|---|---|---|
| 插件数 | 9 个 LLM 插件 | **3 核心 + 3 能力 = 6** |
| 插件配置键 | 109 | **76**（核心 34 + 能力 42；含勘误 77） |
| 失效/僵尸开关 | 12 + 2 仅兼容 + 3 幽灵 | **0**（15 条退役，每条带替代方案） |
| 装配入口 | 4 | **1**（`resolve_plan`） |
| 发送出口 | 3 | **1**（`send_outbound`） |
| "是否发言"判定点 | 17 | **1**（`TurnPlan.speak_mode`） |
| 真实账号静默可归因率 | 0 | **100%**（每轮 plan 摘要） |

### 8.3 架构闸门（CI）

- `tests/architecture/test_core_modules.py`：三个模块存在、`supersedes` 与实现方法匹配、
  `MODULE_KEYS` 与 schema 一一对应、`unknown_keys()/stale_keys()` 为空；
- grep 闸门：除 `plan.py` / 三个模块 / 迁移层白名单外，**禁止出现** `speech_via_tool`、
  `stream_output`、`user_message_format`、`prompt_time_position`；
- `gen_module_manifests.py --check`：`module.json` 与类属性一致。

---

## 9. 风险与回滚

| 风险 | 概率 | 影响 | 对冲 |
|---|---|---|---|
| 迁移把用户配置搬错 | 中 | 行为突变 | `--dry-run` 先看 plan；DB 备份 + kv 快照；旧行不删可回滚；`dropped` 落盘 |
| 合并后**行为漂移**（尤其 prompt 文案） | 中 | 回复风格变 | 阶段 3 第 10 步要求"同配置下 messages 与现在逐字一致"的快照测试；差异必须逐条列出并说明 |
| 中间态跑在默认值上（迁移拖到最后） | 高（若按大爆炸迁移） | 一小时内行为变化 | **增量迁移**：每个模块落地即迁自己的键 |
| 第三方/外置插件依赖旧模块名 | 低 | import 失败 | 全仓已禁止插件互相 import；外置 12 个插件不引用 `llm_*`；shim 目录保留 2 个版本 |
| `llm_trigger` 只是把坏实现搬家 | 中 | 主动/定时仍坏 | 它依赖阶段 0.3/0.4 先修；未修不合入 |
| 工作区有另一会话在改 `assembly/` | 高 | 冲突 | 阶段 0/3 动手前先 `git pull`/确认 WIP 已提交；改 `assembly/` 只碰必要文件 |

**回滚单位 = 提交**：每一步都可单独 `git revert`；shim 期间还可"启用旧模块 + 禁用新模块"热切。

---

## 10. 需要你拍板（按阻塞程度排序）

| # | 问题 | 我的建议 | 影响 |
|---|---|---|---|
| Q1 | 槽接管走 A 还是 B | **A**（Module 实现 `build/respond`，转调 service） | 插件侧 30 行 vs 框架 120 行 |
| Q2 | 四道可见性闸是否与合并同批 | **同批，且先做**（对现有 9 个插件立即生效） | 决定会不会"换一种方式继续静默" |
| Q3 | 三个核心之外，`llm_memory/llm_context/llm_tools_extra` 是否也并 | **不并**（并 = 单模块 8000 行 / 63 键） | 决定最终是 3 个还是 6 个模块 |
| Q4 | `llm_enhance.debug_prompt` 是否驱动 `llm_prompt.enabled` | **不驱动**（调 debug 不该打开整个提示词模块） | 决定账号 123 迁移后 `llm_prompt` 是 0 还是 1 |
| Q5 | 孤儿 `llm_knowledge` 3 个键的归宿 | 进 `dropped` 落盘；要么建回知识库模块，要么从归属表删键 | 3 个键 |
| Q6 | 旧 bindings 行与 shim 保留多久 | 只 `enabled=0` 保留；**2 个版本后**删目录+删行（同提交删测试） | 决定是否要给 `binding_store` 加 `delete_binding()` |
| Q7 | `prompt_nudges`（7 个开关并成 1 个）与元信息项的**多选控件** | **新增 `multiselect`**（+40 行：`schema_utils.py` 白名单 + `FieldControl.vue` + `ConfigForm.vue`）。退路是 `string_list` 手打键名——那等于把"开关不明"换成"选项要背" | 决定是否动前端 |
| Q8 | 三套提示词方案是否保留差异化 | **保留**，但方案名必须进 `TurnPlan` 与日志 | 决定文案体系动不动 |
| Q9 | `emoji_react_level` 默认值（模块 0 / agent 1 打架） | **取 1**（取 0 会让行为段指涉一个不存在的【可用表情】段） | 新账号默认行为 |
| Q10 | `mode` 默认是否仍 `speech` | **先不动**（先落阶段 0.5 + 人格自动剥离，再灰度）；若想更稳可默认 `stream` | 决定新账号第一印象 |
| Q11 | `llm_tools_extra.onebot_tools_enable` 默认（迁移时被从 False 改成 True） | **改回 False**（安全相关：默认不该给模型 175 个写操作工具） | 安全 |
| Q12 | 主动/定时是否保留独立流式开关 | **删**，由 `mode` 统管四个入口（现在这两个键恒 False，因为与已迁走的 agent `stream_output` 相与） | 键数 −2 |

---

## 附录 A · 与已完成工作的关系

- 装配内部已收敛（`docs/plan/prompt-assembly-refactor.md`，P0–P6 全绿）：本计划**不重做**装配，
  只把"谁来决定装配参数"从 3 个插件收成 1 个。
- 人格卡已清洗（`docs/reviews/2026-10-31-persona-cleanup.md`）：本计划的"speech 自动剥离"是它的运行时版本，
  两者叠加才能让"人格里写输出格式"这件事长期不复发。
- 评估里的 P0 缺陷 1/3/4（ChainedConfig 短路、`build_for` 传错 runtime、`turn.py` 无 speech）已排进阶段 0，
  不依赖模块合并即可先落地见效。

## 附录 B · 不做的事（避免范围蔓延）

1. 不重写 `PromptAssembler` / `StreamOutput` / `SpeechOutbox` 的内部逻辑（只挪归属）。
2. 不改 Provider 链、工具循环、会话历史存储。
3. 不改三套提示词方案的文案内容（只改"哪段由谁渲染"）。
4. 不动 WebUI 的模块页框架（除 G4 的 health 与 §10-Q7 的 multiselect）。
5. 不动 `app/plugins/` 之外的业务插件（外置 12 个）。
