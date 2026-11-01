# 参与度回复模式 · 收口计划

> 面向：新版回复模式（`reply_mode=participation`）从"骨架"到"可以上真实群"。
> 依据：HEAD `ba6e55f2` 的代码实测（2026-11-01 晚间核对），不是设计稿推测。
> 规则同 [`README.md`](README.md)：一个提交只做一件事、清单活维护、改完补 hash。

---

## 0. 结论先行：现在不能上真实群

| 编号 | 阻塞项 | 证据（实测） | 后果 |
|---|---|---|---|
| **P0-1** | **参与度分支根本进不去** | `pipeline.py:459` 用 `_effective_config()` 取配置，而该函数只回填 `SCOPE_OVERRIDABLE["agent"]` 的 **9 个白名单键**（`pipeline.py:129-142` + `domain/dialogue_policy.py:45-57`），`reply_mode` 不在其中 → `config.get("reply_mode")` **恒为 `"legacy"`**。实测：`_effective_config()` 返回 `['trigger_mode','trigger_probability']` | 前端把开关切成"参与度驱动"后**毫无效果**，群里仍走旧触发；且提示词已显示参与度约定 → 模型被告知"由你决定是否发言"，系统却只在 @ 时才请求 |
| **P0-2** | **消息永久丢失** | `pipeline.py:468-469` 先 `observe_message()` 再**无条件**`ctx.state["user_recorded"]=True`；`observe_message` 在 `get_or_create_session()` 返回 `None` 时返回 `False`（`participation.py:286-288`），而 `turn_input.py:178` 在**群聊冷却期且非 @** 时返回 `None` | 冷却期内的普通消息既不写历史、又被标记"已记录"，`prompt_prep.py:141` 跳过补写 → 该条消息**永久消失** |
| **P0-3** | **冷却顺序错、@/回复被误伤** | `pipeline.py:482-491`：先 `_cooldown_ok()` 再 `gate()`；`_cooldown_ok()` 在**通过检查的那一刻**就写 `last[key]=now`（`participation.py:234`） | 被门拒绝的消息也消耗冷却；随后 @/回复到达时正处冷却 → 该回的不回，且冷却计时被无关消息不断刷新 |
| **P0-4** | **沉默语义不成立** | `delivery/speech.py:314-320` 在参与度模式强制 `speech_via_tool=True`，但发言通道依赖 `llm_output` 模块接管 `runtime.output`；`llm_output` 当前**禁用中** → `output.py:304-317` 回退 `BlockingOutput/StreamOutput`（文本即回复） | 每条通过门的消息都会**被直接回一条**，模型无法"沉默"；`prompt_prep.py:276-284` 只打一条 `warning`（每 bot 一次），随后"按文本即发言处理" |
| **P0-5** | **判定链自相矛盾** | 通过参与度门后仍走 `pipeline.py:518-529` 的 `trigger_check(triggered, mode=config.get("trigger_mode"), probability=...)`；`submit()` 的 `_would_trigger_llm()`（`pipeline.py:329-366`）也仍用旧 `_trigger_decision()` | 通过参与度门的消息在 UI/日志里被记成旧模式的判定；`high/very_high` 下多条消息并发时**不抬高代际**（不打断），可能一轮未说完又起一轮 |
| **P0-6** | **配置未入体系** | `reply_mode` / `participation_level` / `participation_max_requests` / `participation_max_tokens` / `participation_cooldown_seconds` / `structured_assembly` 六个键只在 `config_schema.py:246-277` 定义，**不在** `config_partition.py` 的 `PANEL_GROUPS / UI_PAGES / MODULE_KEYS / DELETED_KEYS` 任一归宿 → `tests/architecture/test_config_partition.py:91-96`（孤键）会红；且 `SCHEMA` 在 `config_schema.py:1260` 被过滤成 `CORE_KEYS`，这 6 键实际被当作"已迁移/废弃" | 配置合法性无人守；前端 `AiPanelPage.vue:414-491` 能存但后端无白名单校验；契约测试红 |

补充（非阻塞但需在同轮处理）：

- `reply_rules_section`（`participation.py:63-110`）读的是账号级 `req.config`，**读不到群级 policy**；旧版分支直接读 `trigger_mode` 而不走 `trigger_mode_from()`，只配了 `trigger_at` 的账号会永远显示"仅 @"。
- `participation.py:113-130` `participation_section` 定义**两次**，后者覆盖前者（当前无引用，属死代码）。
- 私聊没有参与度分支（全仓 grep 无 `message_private` 侧判定）→ 新版只影响群聊。
- 全量写入后 `MAX_HISTORY_MESSAGES=200`（`session/manager.py:434`）滚动加快；图片逐条落盘未评估。

---

## 1. 目标形态：Observer → Decider → Sender

```
入站(AgentNode) ── 场景/权限/免打扰 ──► LlmPipeline._run
                                           │
                         ① Observer（participation 模式，先于一切本地门）
                            · 无条件写会话：不看冷却、不看触发、不看预算
                            · 返回 Observed(ok/reason)；只有 ok 才置 user_recorded
                                           │
                         ② 场景/免打扰/用户例外判定（与 legacy 同序）
                                           │
                         ③ Decider（唯一规则来源 app/agent/reply/rules.py）
                            · gate(level, event) → allow/deny + reason + checks
                            · 冷却（@/回复豁免）→ 预算（请求数 + 真实 token）
                            · 产出 Decision(kind=respond|silent, reason=...)
                            · 同一份表同时生成提示词文案（reply_rules 段）
                                           │
                         ④ LLM（能力检查通过才进入）
                                           │
                         ⑤ Sender（发言通道）
                            · participation 下必须 speech_via_tool 可用，否则 fail-loud
                            · 未调用 send_message = 沉默；被 @/回复必须有发言
```

三条硬契约（写进测试）：

1. **观察先于门**：`participation` 模式下，通过入站（账号启用/场景/权限）的每条群消息都进会话历史；门/冷却/预算只决定"要不要请求"。
2. **规则单一来源**：门的谓词、提示词文案、冷却与预算策略都由 `ReplyPolicy` 派生，禁止第二处判定。
3. **沉默可证伪**：`silent` 必须有 reason，且 `participation` 下不允许"文本即回复"的回退——能力缺失就拒绝进入并记 error。

---

## 2. 阶段与提交

状态：`[ ]` 未开始 / `[~]` 进行中 / `[x]` 已完成（补 hash） / `[!]` 阻塞

### P0 止血（必须全部完成才可开灰度）

| 编号 | 提交信息 | 改动 | 验收 |
|---|---|---|---|
| P0-1 | `fix(reply): make participation keys visible to the group gate` | `domain/dialogue_policy.py` 白名单加 `reply_mode`/`participation_*`；`pipeline.py` 用生效配置读模式 | 单测：`_effective_config` 含 `reply_mode`；手测日志出现 `reply_mode=participation` |
| P0-2 | `refactor(reply): add reply observer before any local gate` | 新增 `app/agent/reply/observer.py`；`pipeline.py` 顶部（`is_command` 之后、群/私聊分支之前）统一观察；删除分支内观察与误置的 `user_recorded`；`turn_input.get_or_create_session(..., observe=True)` 绕开冷却 | 单测：冷却期消息仍写入；`observe` 失败不置 `user_recorded`；禁用场景不写入 |
| P0-3 | `fix(reply): gate before cooldown and exempt at/reply` | `_cooldown_ok` 改为"仅在允许请求后计时"；@/回复豁免冷却 | 单测：门拒绝不消耗冷却；@ 不被冷却挡住 |
| P0-4 | `fix(reply): fail loud when the speech channel is missing` | `participation` 且 `runtime.output` 非发言通道 → `log_skip("participation_needs_speech", level=error)` 并 return，**不进入 LLM**；前端模式卡给红字提示 | 单测：无 speech 通道 → 0 次请求 + error 日志；有通道 → 正常 |
| P0-5 | `refactor(reply): single reply policy table` | 新增 `app/agent/reply/rules.py`（`ReplyPolicy`：mode/level/cooldown/budget/exempt）；`participation.py` 删除或降为兼容壳；`reply_rules_section` 读生效配置 + `trigger_mode_from()` | 单测：五档 gate 表驱动；文案两种模式；群级覆盖生效 |
| P0-6 | `fix(pipeline): record participation decisions with the new gate` | 通过参与度门后写 `Decision(kind=respond, reason=gate.reason)`，不再套 `trigger_check` 旧模式；`_would_trigger_llm()` 复用同一 gate 谓词 | 单测：判定链记录 `participation` 来源；`high` 下新消息抬高代际 |
| P0-7 | `fix(usage): charge participation budget with real tokens` | `usage/metered.py::_record` 成功后回填 `Budget.charge(tokens)`（按 bot_id 存模块级 Budget 表）；`budget.record(0)` 删除 | 单测：一次请求后 token 预算减少；预算耗尽只记录不请求 |
| P0-8 | `refactor(config): partition reply-mode keys` | `config_partition.py` 归类 6 键（`reply_mode`/`participation_*` → `PANEL_GROUPS` 新增组；`structured_assembly` → 新 surface `advanced_options`）；跑 `scripts/gen_sections.py` 重新生成 JSON；`SCOPE_OVERRIDABLE` 加键（按群灰度用） | `tests/architecture/test_config_partition.py` 全绿；`sections.json`/`ai-panel.json` 逐字节一致 |

### P1 补齐（灰度期做，可与 P0 并行验证）

| 编号 | 提交信息 | 说明 |
|---|---|---|
| P1-1 | `test(reply): contract tests for observe/gate/budget` | 四支契约测试（见 §4），并把 `tests/plugins/test_llm_prompt.py` 随已删模块一起清理 |
| P1-2 | `feat(dashboard): expose participation token budget` | AI 面板触发卡补 `participation_max_tokens`；`AdvancedOptionsPage.vue:58` 标题「提示词组装」→「高级选项」 |
| P1-3 | `build(dashboard): rebuild dist for reply-mode changes` | 单独提交 dist（与 src 分开） |
| P1-4 | `fix(reply): explicit group_enable check before the gate` | 门前显式判场景，避免"群关着却消耗冷却/预算" |
| P1-5 | `feat(reply): participation mode for private chats` | 私聊接入同一 Observer/Decider；默认沿用账号级配置 |
| P1-6 | `fix(agent): align reply rules text with effective policy` | 提示词段与 gate 同源（P0-5 的收尾：删掉旧文案分支残留） |

### P2 观测与压力（上线后再做）

| 编号 | 提交信息 | 说明 |
|---|---|---|
| P2-1 | `feat(telemetry): participation metrics` | 每群请求数 / 沉默原因分布 / 预算余量；复用 `skip_log` 的 reason 统计口径 |
| P2-2 | `perf(session): bound group history under observe-all` | 评估 `MAX_HISTORY_MESSAGES=200` 与图片逐条落盘；按会话配置历史上限或按"最近 N 分钟 + N 条"滚动 |

### 灰度（P0 全绿后）

1. 单账号、单群开 `reply_mode=participation` + `participation_level=very_low`（最保守）；
2. 观察 24h：`skip_log` 里 `participation_*` 原因分布、`user_recorded` 缺失次数、预算余量；
3. 逐档升到 `low` → `medium`；`high/very_high` 必须先在非生产群试；
4. 回滚 = 一键切回 `legacy`（新键默认值保持 legacy，不下发默认值变更）。

---

## 3. 每个 P0 的改动位置（便于认领）

| 编号 | 文件 | 具体位置 |
|---|---|---|
| P0-1 | `app/domain/dialogue_policy.py` | `SCOPE_OVERRIDABLE["agent"]`（:45-57）加 6 键；`app/agent/loop/pipeline.py:459-500` 改读生效配置 |
| P0-2 | 新增 `app/agent/reply/observer.py`；`app/agent/loop/pipeline.py:423-541`；`app/agent/loop/turn_input.py:163-181`；`app/agent/loop/prompt_prep.py:141-144` | — |
| P0-3 | `app/agent/participation.py:220-235`、`238-257` | 冷却改为 `check()` + `mark()` 两步；gate 先跑 |
| P0-4 | `app/agent/loop/pipeline.py`（门前）/`app/agent/delivery/output.py:241-317`/`app/agent/delivery/speech.py:314-320` | 能力探测函数放 `delivery/output.py`，pipeline 只消费 |
| P0-5 | 新增 `app/agent/reply/rules.py`；`app/agent/participation.py`；`app/agent/assembly/builtin_sections.py:149-151`、`:268` | 段构建改调 `reply.rules.reply_rules_section` |
| P0-6 | `app/agent/loop/pipeline.py:518-529`、`:329-366` | — |
| P0-7 | `app/agent/usage/metered.py:104-133`；`app/agent/participation.py:136-170` | Budget 表从 runtime 挂载改为模块级 `bot_id → Budget` |
| P0-8 | `app/agent/config_partition.py:185-219`；`app/agent/config_schema.py:246-277`；`scripts/gen_sections.py`；`app/webui/dashboard/src/agent/{sections,ai-panel}.json` | — |

---

## 4. 测试策略（契约，不是实现快照）

1. `tests/agent/test_reply_observer.py`
   - 冷却期、非 @ 的群消息**仍写入**历史（回滚 P0-2）；
   - 观察失败不返回 `user_recorded=True`；
   - 群被禁用时不写入（观察点在场景判定之后）。
2. `tests/agent/test_reply_gate.py`
   - 五档 gate 逐档断言（very_low/low/medium/high/very_high）；
   - @/回复豁免冷却；门拒绝不消耗冷却；
   - 预算：请求数用尽 → `silent(budget)`；token 用尽 → `silent(budget)`。
3. `tests/agent/test_reply_rules_text.py`
   - 同一 `ReplyPolicy` 生成的文案与 gate 结论一致（例如 very_low ⇒ 文案含"仅被 @"）；
   - 旧版文案随 `trigger_mode_from()` 变化（只配 `trigger_at` 不再是"已配置触发方式"）。
4. `tests/agent/test_reply_speech_required.py`
   - `participation` + 无发言通道 → 不请求 + error 级 skip；
   - `participation` + 有通道 → 正常进入。
5. 架构：`tests/architecture/test_config_partition.py` 必须绿；`sections.json` 契约（`tests/agent/test_agent_section_contract.py`）必须绿。

> 按 AGENTS.md：删/改模块时**同一提交**删掉对应测试（`tests/plugins/test_llm_prompt.py` 属已删模块的遗留）。

---

## 5. 上线前置（人肉清单，跑完再开灰度）

- [ ] `llm_output` 模块**启用**，输出形态 = 发言通道（否则 P0-4 会一直拒绝请求，属于"设计如此"）。
- [ ] `llm_context` 模块启用（`get_context` 工具；否则模型无法按 id 回看被折叠的消息）。
- [ ] `participation_max_tokens` 从 10,000,000 调到真实预算（当前默认等于"不限"）。
- [ ] 重启进程（改 `.py` 生效），前端 dist 强刷（`Ctrl+F5`）。
- [ ] `reply_mode` 保持默认 `legacy`；灰度按账号/群覆盖开启。
- [ ] 备份 `data/app.db`（沿用 `backups/` 惯例）。

---

## 6. 待确认（需要产品决定，不阻塞 P0 编码）

| 编号 | 问题 | 建议 |
|---|---|---|
| Q1 | token 预算口径：只算群聊请求，还是该账号**所有** LLM 调用（含压缩/摘要/记忆蒸馏）？ | 建议"该账号所有调用"——预算是成本闸门，越保守越好；实现上 P0-7 就是这样挂的 |
| Q2 | 私聊是否本轮接入参与度（P1-5）？ | 建议先只做群聊，私聊保持 legacy，避免一次改两条链路 |
| Q3 | `medium` 档的"正在参与"判据（当前是"会话尾部 6 条内有 assistant 消息"）够不够？ | 先保留，P2 引入"最近 N 分钟 bot 发言 + 被 @ 过"的显式状态 |

---

## 7. 本轮不做

- 不重写 `message_assembly`（渲染层已定型，本轮只消费）。
- 不恢复 `llm_prompt` 模块（已删，配置项只保留 `structured_assembly`）。
- 不动 `llm_context` 的 `get_context` 协议（只保证它启用后可用）。
- 不做"参与度学习/自适应"（先用固定档位跑通）。
