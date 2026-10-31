# 计划 · Prompt 装配重构（新系统提示词 + 人格预设 + 钩子注入 + 历史）

> 背景与证据：[`../reviews/2026-10-31-prompt-assembly-analysis.md`](../reviews/2026-10-31-prompt-assembly-analysis.md)
> 目标一句话：**废掉"legacy 老装配"，把一次请求固定按 5 层拼：系统提示词（方案）→ 人格预设 → 钩子注入 → 对话历史 → 本轮消息。**

## 进度（2026-10-31）

| 步骤 | 状态 |
|---|---|
| P0 修 `presets.resolve`（getter-only 配置对象）+ 契约测试 | ✅ `adc81f94` |
| 清理死代码（`prompt.build_messages` 老装配器 / `_CHAT_SCHEDULE` / `CAP_TOOL_NAMES`） | ✅ `9f1483f9` |
| **删除旧配置键**：`assembly_profile`（legacy/qq_agent 双档）、`behavior_rules_enable`；格式类键保留并去掉"档位"语义（默认单行 / 时间在尾） | ✅ 本次提交 |
| 顺序口径：**A**（规则在前 + L1 末尾复述安全段与工作方式） | ✅ 已定 |
| P1 定契约（AssemblyPlan / describe 带 layer） | 待做 |
| P2 文案收敛（L1 合成一条 system；legacy 常量退场） | 待做（键已删，剩文案合并） |
| P3 人格单一来源 | 待做 |
| P4 钩子通道收敛（Section 唯一） | 待做 |
| P5 缓存与预算、P6 文档门禁 | 待做 |

---

## 0. 结论先行

现状不是"没按 5 层拼"，而是**三种装配形态叠在一起**：平铺 12 段（按 order）+ 三条旁路注入通道
+ 两档行为模式（`legacy` / `qq_agent`）+ 三处文案矩阵（legacy / new / single-line / 方案各一套）。
`prompt_preset` 解析失效（`presets.resolve` 的 `in config` 对 getter-only 配置对象抛 TypeError 被吞）
让这个叠层直接塌回 legacy —— 于是"新系统提示词"从不生效，"旧文案"一直生效。

重构要解决的不是"加一层"，而是**收敛成一条路径、一份文案、一个注入契约**。

---

## 1. 现状盘点（代码事实）

### 1.1 段表（`assembler.py` docstring 与 `builtin_sections.default_sections()`）

| order | 段 | 角色 | 生效条件 | 文案来源 |
|---|---|---|---|---|
| 10 | persona | system | 始终 | 人格预设 / `config.system_prompt` |
| 20 | behavior | system | 选中提示词方案（产品路径恒有） | 方案段落 / `behavior_rules_override` |
| 30 | emoji_hint | system | 方案或 qq_agent + 能贴表情 + 活跃度>0 | 方案 `emoji_hint` |
| 40 | schedule | system | `schedule_enable` | 方案 `schedule_instruction` / 常量 |
| 50 | proactive | system | 有可用能力 | 方案 `proactive_lines` / 常量 |
| 60 | message_meta | system | 元信息已注入 + 模式≠off | 方案两份 / 常量三份 |
| 70 | skills | system ×N | 有技能 | 技能文件 |
| 80 | memory | system | 记忆召回非空 | 钩子（`llm_memory`） |
| 90 | background | system | 指代块或环境背景非空 | 钩子（`llm_context`）+ 历史拉取 |
| 100 | group_log | system | 群聊记录模块在场 | `app/agent/group_log` |
| 105 | extra | system ×N | `req.extra_blocks` 非空（兼容通道） | `runtime.context_providers` |
| 110 | history | 对话 | 非空 | 会话历史（可压缩） |
| 120 | user | user | 始终 | 本轮正文 + 元信息 + 引用 + 图片 |

### 1.2 三条并行的注入通道（这正是"钩子注入"的现状）

| 通道 | 接口 | 能力 | 现在谁在用 |
|---|---|---|---|
| `Section` 注册表 | `register_section(runtime, Section(order≥200))` | 有序、可命名、可覆盖内置同名段、可 `complete` 独占整条 | **没有插件在用**（只有内置段） |
| `extra_blocks` | `runtime.context_providers` → `req.extra_blocks` | 只能插在 group_log 与 history 之间，顺序固定 | `llm_context`（指代块）、`llm_memory`（记忆召回） |
| `post_assemble` 钩子 | `apply_post_assemble(runtime, ctx, messages)` | 整体改写/阻断本轮 | `llm_assembly`（prompt dump）、`llm_proactive`/`llm_schedule`（跳过本轮） |

三者语义重叠：都能"加提示词"，但没有统一顺序保证，也没有统一的"这一段是谁加的"观测。

### 1.3 双档 + 文案矩阵（"旧提示词还在用"的直接来源）

> 已处理：`assembly_profile` 与 `behavior_rules_enable` 已删除；下面记录删除前的形态，
> 供复盘"为什么会出现新旧两套文案同时在跑"。

- ~~`assembly_profile`：`legacy`（默认）/ `qq_agent`。5 处 `build_*` 用它做门控。~~
- 旧常量：`SCHEDULE_INSTRUCTION`、`PROACTIVE_LINES`、`LEGACY_MESSAGE_META_INSTRUCTION`、
  `MESSAGE_META_INSTRUCTION`、`SINGLE_LINE_MESSAGE_META_INSTRUCTION`。
- 方案又各带一份：`schedule_instruction` / `proactive_lines` / `message_meta_single_line` / `message_meta_legacy`。
- 结果：同一句"消息格式说明"最多有 **4 个版本**，运行时选哪个取决于（已删除的）档位 × 方案 × 模块开关。

### 1.4 本轮实测（账号 123，2026-10-31 13:56）

装配结果 = persona(1665) + schedule + proactive + background，**behavior / emoji / message_meta 全缺**；
且该账号的 `llm_*` 插件全部 `enabled=0` → 钩子层实际为空（extra_blocks 没有贡献）。
另有 `llm_assembly` 禁用 → `behavior_rules_override` 也读不到。

---

## 2. 目标模型：5 层

```
L1 系统提示词（方案渲染；唯一"规则/行为"来源）
   ├─ 安全规则（不可被 override 删除）
   ├─ 工作方式（发言通道/文本直发，随能力说真话）
   ├─ 表达与底线（反 AI 味、引用/点名、汇报禁令）
   ├─ 协议文案（定时任务 / 主动性 / 消息格式——随方案，随能力裁剪）
   └─ 用户 override（设置页编辑的三套文案；安全段强制追加）
L2 人格预设（唯一来源：persona_preset_id；本轮不变 → 可缓存）
L3 钩子注入（插件段：记忆 / 知识库 / 群记录 / 指代 / 表情标签…按 order 升序，可观测）
L4 对话历史（窗口 / 压缩 / 开场对话）
L5 本轮消息（元信息 + 引用 + 图片 + 附件）
```

### 2.1 现有 12 段 → 5 层映射

| 层 | 现在的段 | 处置 |
|---|---|---|
| L1 | behavior、schedule、proactive、message_meta | **合并为一条 system**（方案渲染），文案只留方案一份 |
| L2 | persona | 收敛为单一来源；`config.system_prompt` 只作迁移期兜底 |
| L3 | emoji_hint、skills、memory、background、group_log、extra | 统一走 `Section`（插件段与内置段同一张表），删 `extra_blocks` 通道 |
| L4 | history | 不变 |
| L5 | user | 不变 |

### 2.2 与"你说的顺序"的一处冲突（必须先定）

现状是 **persona(10) → behavior(20)**：人设在前、规则在后。
你描述的顺序是 **系统提示词 → 人格预设**：规则在前、人设在后。

这不是排版问题：越靠后的 system 内容，模型通常越当"当前指令"。改成规则在前，
人设（尤其用户手写的"禁止 Markdown / 50 字 / 称呼老师"这类）会**压过**规则段，
安全段/工作方式被稀释的风险上升。两个可选口径：

- **A（推荐）**：L1 规则在前、L2 人设在後，但 L1 末尾保留「安全与工作方式的再声明」（短，~10 行），
  相当于开头讲规则、结尾再钉一次；
- **B**：维持人设在前、规则在后（只做"层"的收敛，不动相对顺序），风险最小。

---

## 3. 差距清单（按严重度）

| # | 差距 | 影响 | 处理阶段 |
|---|---|---|---|
| G1 | `presets.resolve` 用 `in config` → getter-only 配置对象抛 TypeError → 恒 `None` | 三选一/设置页文案全部不生效 | **P0** |
| G2 | 双档 `legacy`/`qq_agent` + 4 套文案矩阵 | "旧提示词还在用"、行为不可预测 | P2 |
| G3 | 人格双份（预设 vs 账号副本），内容已分叉 | 改了不生效 / 旧文本会顶上 | P3 |
| G4 | 三条注入通道并行，`Section` 无人使用 | 顺序无保证、无法回答"这段谁加的" | P4 |
| G5 | `behavior_rules_override` 归 `llm_assembly` 模块所有 | 模块一关，override 静默消失 | P2 |
| G6 | 门控散在 5 个 `build_*` 里（方案/档位/schedule_enable/meta_injected/caps） | 无法一眼看出"本轮注入了什么" | P4 |
| G7 | 文案与段数变化会打断前缀缓存 | 上线首轮缓存全失效（一次性） | P5 |
| G8 | 测试全部用普通 dict 构造 config | 真实配置对象零覆盖（本次事故根因） | P0 |
| G9 | 文档仍描述旧顺序 | 新增插件会照着旧模型写 | P6 |

---

## 4. 分阶段实施（每阶段独立可提交、可回退）

### P0 · 止血（1 个提交，零行为风险）
- `presets.resolve` 改哨兵写法（兼容只有 `get()` 的配置对象）；
- 契约测试：用「只有 `get()` 的配置对象」跑 `PromptAssembler().build()`，断言行为段存在；
- 验收：`[Prompt]` dump 里出现 behavior 段（3540 字），方案切换 chat/roleplay/assistant 文案确实不同。

### P1 · 定契约（不改行为）
- 新增 `app/agent/assembly/plan.py`：`Layer` 常量（`system_prompt/persona/injected/history/user`）+ `AssemblyPlan`
  描述"层 → 段 + order"，装配器只按 plan 渲染；
- `describe(req)` 输出带 `layer` 字段，`[Prompt]` 日志改为**段表**（name/layer/order/chars/source），
  全文另由 `debug_prompt` 开关打印；
- 验收：同一份输入下 plan 渲染结果与现状逐字节一致（快照对比）。

### P2 · 文案收敛（行为变更，可回退）
- L1 四段（behavior/schedule/proactive/message_meta）合并成**一条** system，由方案一次渲染；
- 删 `legacy` 常量与 `assembly_profile` 门控（保留一个 release 的开关 `assembly_profile=legacy`
  指向旧渲染器，默认新路径）；
- `behavior_rules_override` 从 `llm_assembly` 模块配置迁到 agent 配置（模块禁用也生效）；
- 验收：三套方案 × 三档能力（含/不含发言通道）× 有/无 override 的文案契约测试。

### P3 · 人格单一来源
- 只读 `persona_preset_id`；`config.system_prompt` 降级为"未选预设时兜底 + 迁移期对账"；
- 启动迁移：把与绑定预设不一致的副本按预设覆盖并记日志（当前账号 123 的 1829 vs 1665 就会被这一条修正）；
- UI：账号级 `system_prompt` 标注"仅在未选择人格预设时生效"（或直接隐藏）；
- 验收：编辑预设 → 下一轮 prompt 立即变；删除预设 → 明确回退到兜底并留痕。

### P4 · 钩子通道收敛
- `Section` 为唯一有序通道：`llm_context`、`llm_memory` 从 `extra_blocks` 迁到 `register_section`；
- `extra_blocks` 标 legacy（保留一个 release），`post_assemble` 只保留"整体改写/阻断"，不再用于插块；
- 段表加 `source`（模块名），`[Prompt]` 里可直接看出"记忆召回块来自 llm_memory"；
- 验收：禁用某模块 → 对应段整段消失且段表里可见。

### P5 · 缓存与预算
- 层内顺序固定、层间顺序固定；L1/L2 本轮内逐字稳定（可缓存前缀），L3 之后才允许动态内容；
- `token_budget` 按层给上限（L1+L2 建议 ≤ 4k tokens），超限优先裁 L3 再裁 L4；
- 验收：前缀指纹（`prompt_cache.system_prefix_fingerprint`）在连续两轮之间不变。

### P6 · 文档与门禁
- 更新 `assembler.py` 顶部段表、`docs/MODULE_DEV.md §4.7.4`、`docs/ARCHITECTURE.md`；
- 架构测试：段表顺序快照 + "L1 必须在 L2 之前" + "L1 只有一个 system" 的静态守卫。

---

## 5. 风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| 前缀缓存失效 | 段合并/顺序变化，首轮全 miss | 明确"一次性成本"，P2 集中发布 |
| 人设与规则打架 | 规则前置后人设（用户手写禁令）权重变高 | 选口径 B 或 A 的"结尾再声明" |
| 插件兼容 | 现有插件用 `extra_blocks`/`post_assemble` | P4 保留一个 release 的兼容通道 + 启动 warning |
| token 膨胀 | L1 3540 字 + 人设 1665 字已约 5k 字 | P5 给层预算，并把"协议文案"按能力裁剪 |

---

## 6. 需要拍板的设计选择

1. **顺序**：A（规则在前 + 结尾再声明）还是 B（人设在前、只做层收敛）？
2. **L1 是否只允许一条 system**（我建议是，方便缓存与观测），还是保留多条以便按需开关？
3. **人格是否还能携带"输出格式"约束**（如 50 字/Markdown 禁令）——建议移到 L1 的 chat 方案文案，人格只管"我是谁"；
4. **legacy 档保留多久**（建议 1 个 release，默认新路径，开关可回退）。
