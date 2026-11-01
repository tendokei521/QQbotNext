# 参与度回复模式 · 收口计划

> 面向：新版回复模式（`reply_mode=participation`）从"骨架"到"可以上真实群"。
> 依据：HEAD `ba6e55f2` 的代码实测（2026-11-01，含一次只读审计 + 一次全量 pytest 基线），不是设计稿推测。
> 规则同 [`README.md`](README.md)：一个提交只做一件事、清单活维护、改完补 hash。

**修订记录**

- v1（20:0x）：按交接清单核对，确认 6 项阻塞。
- v2（20:4x）：审计修正两处口径 —— ①沉默失效**取决于 `speech_via_tool` 的解析值**，当前环境回退的是流式发言通道（沉默成立），坏组合是"`llm_output` 启用 + 输出形态 text/stream"；②**只把键加进白名单不够**，配置写入在 API 层就被静默丢弃。另新增 4 项实测阻塞（`is_at` 硬编码、`prompt_prep` 二次会话门、引用判定、配置静默丢弃）。补充 pytest 基线。
- v3（用户口径落定）：①token 预算按**该账号所有 LLM 调用**计（含压缩/摘要/记忆蒸馏）；②**私聊不接入**参与度——这是群聊特有方案；③**档位差异主要写在系统提示词**（`LEVEL_PROMPT`），本地门只当成本闸门，不按档位再写一套语义谓词；④节奏：**一步一提交、只保证代码正确，本轮不写/不跑 pytest**，测试与全量验收挪到最后一次性做。

---

## 0. 结论先行：现在不能上真实群

| 编号 | 阻塞项 | 证据（实测） | 后果 |
|---|---|---|---|
| **P0-1** | **参与度配置写不进库（静默）** | `reply_mode` / `participation_*` / `structured_assembly` / `include_sent` 都不在 `CORE_KEYS`（`config_partition.py:215-219`）→ 被 `config_schema.py:1260` 从 `SCHEMA` 过滤掉 → `app/webui/api/agent.py:218-221` 对 `SCHEMA.get(key) is None` 的键**静默 `continue`**（不写、不报错） | 用户把开关切成"参与度驱动"，2 秒后保存成功、刷新**回到 legacy**；`structured_assembly` 的"高级选项"开关同样空转 |
| **P0-2** | **参与度分支根本进不去** | `pipeline.py:459` 用 `_effective_config()` 取配置，该函数只回填 `SCOPE_OVERRIDABLE["agent"]` 的 **9 个白名单键**（`pipeline.py:129-153` + `domain/dialogue_policy.py:45-57`），`reply_mode` 不在其中 → `config.get("reply_mode")` **恒为 `"legacy"`**。实测：`_effective_config()` 只返回 `['trigger_mode','trigger_probability']` | 即使配置进了库，群聊路径仍逐字走旧触发；而提示词层已渲染参与度约定 → **提示词说"你自己决定说不说"、系统却只在被叫时才叫它** |
| **P0-3** | **消息永久丢历史** | `pipeline.py:468-469` 先 `observe_message()` 再**无条件** `ctx.state["user_recorded"]=True`；`observe_message` 在 `get_or_create_session()` 返回 `None` 时返回 `False`（`participation.py:286-288`，且传 `ctx=None` → @ 豁免永不成立），`turn_input.py:178` 在「群聊 + bot 刚回复过 `reply_cooldown` + 非 @」时返回 `None`；失败只 `logger.debug` | 该条消息**本轮仍在请求里**，但**之后永久不在历史**（`prompt_prep.py:141` 见标记就跳过补写）；失败无用户可见痕迹 |
| **P0-4** | **通过所有门后整轮静默消失** | `prompt_prep.py:135` **第二次**调 `get_or_create_session()`，此时 `ctx.state["is_at"]` 仍是 False（见 P0-5）→ 冷却期内返回 `None` → `prompt_prep` 返回 `(None,None,None)` → `chat.py:223-224` **直接 return，无 log_skip、无日志**。且冷却/预算已在 `pipeline.py:483/500` 被扣掉 | 真被 @ 也可能一句话没有；`participation_cooldown_seconds=0` + `high` 可稳定复现 |
| **P0-5** | **`is_at` 被硬编码 False** | `pipeline.py:501` `triggered, is_at, user_text = True, False, ...` → 后续 `pipeline.py:531-536` 的赋值被跳过 | ① `_should_force_speak`（`assistant_output.py:75-88`）为假 → 群里被 @ **没有**"补一轮必须发言"的兜底；② `turn_input.py:178` 的 @ 冷却豁免永不生效 |
| **P0-6** | **冷却顺序错、@/回复被误伤** | `pipeline.py:482-491`：先 `_cooldown_ok()` 再 `gate()`；`_cooldown_ok()` 在**通过检查的那一刻**就写 `last[key]=now`（`participation.py:232-234`），且不看 `is_at`/reply 段 | 被门拒绝的消息也消耗冷却；群里无意义消息把冷却刷满，真正 @ 到达时被拦掉 |
| **P0-7** | **引用判定过宽** | `_is_reply_to_me`（`participation.py:185-190`）看到**任意** reply 段就返回 True（注释也承认） | "只被 @/回复才回"的档位承诺不成立：任何引用别人的消息都进 LLM，既烧钱也乱接话 |
| **P0-8** | **观察早于场景/免打扰/用户例外** | 参与度块在 `pipeline.py:457-541`，场景拒绝 `561`、免打扰 `592-600`、用户例外 `601-611` 都在其后；预算在 `500`、api_key 在 `552` 之后才检查 | 已禁用群 / 免打扰时段 / 黑名单用户的消息被写进会话历史（污染上下文、隐私面），并消耗冷却与整账号预算 → **一个热闹但被禁用的群能把它群额度耗光** |
| **P0-9** | **token 预算永远为 0** | `budget.record(0)`（`pipeline.py:500`）+ `Budget.record` 只在 tokens 为真时入队（`participation.py:155-159`）→ `used_tokens` 恒 0，`participation_max_tokens` 永不生效 | 成本闸门形同虚设；默认 10,000,000 等于"不限" |
| **P0-10** | **沉默语义取决于输出通道，且矛盾告警不可达** | `speech.py:314-320` 参与度强制 `speech_via_tool=True`；实际回退链 `output.py:304-317 → 241-253 → capabilities.py:65-83,160-167` 读**原始键**（agent 默认 True，`config.py:51`）→ 当前环境回退 `StreamOutput`（发言通道，沉默成立）。坏组合：`llm_output` 启用 + `mode=text/stream`（`llm_output/service.py:75-79`），或库里写了 `speech_via_tool=False` → 正文当回复发、`send_message` 气泡被丢（`run_turn` 无 flush）。而 `prompt_prep.py:276-284` 的告警只覆盖 `speech is None` 分支，**参与度下不可达** | 同一份配置可能"能沉默"或"无法沉默"，取决于输出模块形态，且**没有任何日志**说明当前是哪一种 |

补充（不阻塞灰度，但需在同轮修）：

- `reply_rules_section`（`participation.py:63-110`）读账号级 `req.config`，**读不到群级 policy**；旧版分支直接读 `trigger_mode` 而不走 `trigger_mode_from()`，只配了 `trigger_at` 的账号永远显示"仅 @"。
- `participation_section` 定义两次（`participation.py:113-115`、`118-130`），全仓**零引用**；`__all__` 仍导出 → 按旧名接入的一方在 legacy 下拿到空列表（段静默消失）。
- 判定链记旧模式：通过参与度门后仍写 `trigger_check(mode="at", allowed=True)`（`pipeline.py:518-529`），门的结论只进 debug 日志（`skip_log` 默认 warning 才用户可见）→ "为什么不回"对用户不可见。
- 打断代际失效：`_would_trigger_llm`（`pipeline.py:329-366`）无参与度分支 → 非 @ 消息不抬高代际，`interrupt_enable` 在 `high/very_high` 下不生效。
- 全量写入后 `MAX_HISTORY_MESSAGES=200`（`session/manager.py:434`）滚动加快；但模型实际只看 `history_recent_messages=12`（`config.py:67-68`），被折叠部分靠 `llm_context.get_context` 取回。
- `app/agent/tools/state.py:360` 引用已删除的 `search` 变量，异常被吞 → `recall_tool_result` 不在工具清单里（与参与度无关，一并修）。
- `budget_of`（`participation.py:162-170`）`setattr` 失败时每次 new 一个 Budget，预算静默失效。

### 0.1 测试基线（实测 `venv\Scripts\python.exe -m pytest -q`）

- `pytest -q`：**`Interrupted: 9 errors during collection`（0 个用例执行）**。
- `--continue-on-collection-errors`：**`56 failed, 2693 passed, 19 skipped, 37 errors in 109s`**；约 **163 个用例根本没跑**（9 个收集错误掩盖 135 个用例 + 28 个 setup 错误）。
- 根因全是上一轮改造遗留（llm_prompt 已删、llm_context 旧 focus/referent 已删、history_search/get_chat_history 已删、文案与分区期望过期），**不是**新装配架构的副作用。
- **本轮不修不跑**（用户口径：先保证代码正确，最后一次性验收）；阶段 E 统一处理。

---

## 1. 目标形态：Observer → Decider → Sender

```
入站(AgentNode) ── 账号/场所/权限（INBOUND_STAGES）──► LlmPipeline._run
                                           │
                         ① 场景/免打扰/用户例外判定（与 legacy 同序，先于一切写入）
                                           │
                         ② Observer（仅 participation）
                            · 无条件写会话：不看冷却、不看触发、不看预算
                            · 返回 Observed(ok/reason)；只有 ok 才置 user_recorded
                                           │
                         ③ Decider（唯一规则来源 app/agent/reply/rules.py）
                            · 成本闸门（不是发言决策）：代价太高的消息不进 LLM
                            · 冷却（@/回复豁免）→ 预算（请求数 + 真实 token）
                            · 产出 Decision(kind=respond|silent, reason=...)
                                           │
                         ④ LLM（能力检查通过才进入）
                                           │
                         ⑤ Sender（发言通道）
                            · participation 下必须确认"当前生效策略 = 发言通道"，否则 fail-loud
                            · 未调用 send_message = 沉默；被 @/回复必须有发言
```

### 1.1 档位语义只在提示词（v3 决策）

- **档位差异 = `LEVEL_PROMPT` 文案**（"哪些话题值得接、什么情况必须沉默"），由模型执行。
- 本地门**只做成本闸门**，回答的是"这条消息值不值得花一次请求"，**不回答"要不要发言"**。
  所以 gate 连档位都只做三档映射：`very_low/low → 仅 @/回复`、`medium → 再加"我正在参与的会话"`、`high/very_high → 全放行`（与现状一致，但明确其性质）。
- 禁止再往 gate 里加语义判断（关键词相关度、情绪、话题匹配…）——那些属于提示词。

### 1.2 判定顺序契约（P0-3/P0-8 的核心，别做漂移）

| 顺序 | 阶段 | 失败时 |
|---|---|---|
| 1 | `#llm` 指令 | 走命令分支 |
| 2 | 场景：账号启用 / 场所 / 权限 / 免打扰 / 用户例外 | `return`，**不写历史、不消耗冷却/预算** |
| 3 | **Observer**（仅 `participation`，且仅群聊）：写会话历史 | 记 warning（用户可见级别），不置 `user_recorded` |
| 4 | Decider（participation：gate → 冷却 → 预算）或 legacy 触发判定 | `log_skip` + `return`（消息已入库） |
| 5 | api_key / debounce / pre_request / 会话锁 / LLM | 同现状 |

日志语义：**场景拒绝 > 触发拒绝 > 无 API Key**；api_key 检查留在门之后、debounce 之前。

三条硬契约（阶段 E 落成测试）：

1. **观察先于门**：`participation` 下，通过入站门控的每条群消息都进会话历史；门/冷却/预算只决定"要不要请求"。
2. **规则单一来源**：门谓词、提示词文案、冷却/预算策略都由 `ReplyPolicy` 派生。
3. **沉默可证伪**：`silent` 必须有 reason；`participation` 下若当前生效输出策略不是发言通道，必须拒绝进入并记 error。

---

## 2. 阶段与提交

状态：`[ ]` 未开始 / `[~]` 进行中 / `[x]` 已完成（补 hash） / `[!]` 阻塞
节奏：**一步一提交**；每步只做 `compileall` / 定向冒烟，不跑 pytest；测试与全量验收统一放阶段 E。

**进度（2026-11-01 晚，HEAD `49e18422`）**

| 编号 | hash | 一句话 |
|---|---|---|
| A-1 | `cc79a9b5` | 删 `state.py` 里的 `search` 残留，`recall_tool_result` 重新出现在工具清单 |
| A-2 | `6186bfff` | 7 键入配置体系（`CORE_KEYS` 派生并入 `SURFACE_GROUPS`；`include_sent` → 已删），重新生成 `ai-panel.json`；**实测 `/api/agent/config` 保存后能读回** |
| A-3 | `3d38c52f` | `SCOPE_OVERRIDABLE` 加 5 键 → 参与度分支**首次可达**（实测 `reply_mode=participation`） |
| A-4 | `4ef419e3` | 新增 `app/agent/reply/observer.py`；群聊准入后移到场景判定之后，先观察再判定，`user_recorded` 只在真写入时为真 |
| A-5 | `db3790cb` | `prompt_prep` 二次会话门不再吞掉已放行的轮次；`req is None` 补 `log_skip` |
| A-6/7/8 | `3d4cebd6` | `GateResult` 带 `at_me/reply_me`；冷却拆 `check`/`mark` 并豁免 @；引用目标反查（引用别人 ≠ 回复我） |
| A-9 | 部分 | `participation.py` 重复的 `participation_section` 已删；规则表仍集中在 `participation.py`，未拆出 `reply/rules.py`（功能等价，拆包留作后续） |
| A-11 | `e4c1cddd` | 输出形态不是发言通道时 `participation_needs_speech`（error 级）拒绝本轮 |
| A-12 | `8c7eae26` | `usage/metered.py` 回填真实 token；预算改模块级 `bot_id → Budget` |
| 文案/打断 | `3e425b75` | 提示词段读生效配置 + `trigger_mode_from()`；参与度消息参与打断代际 |
| B-1/B-2 | `94e2e6fe` / `49e18422` | 触发卡补 token 上限；高级选项页标题修正；dist 重建；**1440/390 截图 + metrics `overflow=0`** |

仍待做：A-10（判定链把参与度结论写进 Decision，目前只在 debug 日志）、A-9 拆包、阶段 C 真群手测、阶段 E 全量验收。


### 阶段 A：止血（代码，按依赖顺序）

| 编号 | 提交信息 | 改动 | 自检 |
|---|---|---|---|
| A-1 | `fix(tools): remove stale search reference in tool state` | `app/agent/tools/state.py:357-366` 删掉 `search` 残留（360-362） | `compileall`；工具清单能列出 `recall_tool_result` |
| A-2 | `fix(config): persist participation reply-mode keys` | `config_partition.py`：`reply_mode`/`participation_*` → `PANEL_GROUPS` 新组；`structured_assembly` → 新 surface `advanced_options`；`include_sent` → `DELETED_KEYS`；`CORE_KEYS` 派生并入 `SURFACE_GROUPS` 全部组（否则新 surface 的键仍被过滤）；跑 `scripts/gen_sections.py` | `POST /api/agent/config` 后 `GET` 能读回（手测）；`python -c` 断言 `reply_mode in SCHEMA` |
| A-3 | `fix(reply): read the reply mode from the effective config` | `SCOPE_OVERRIDABLE["agent"]` 加 5 键；`pipeline.py:459-500` 用生效配置读模式 | 手测日志出现 `reply_mode=participation`；`_effective_config` 含该键 |
| A-4 | `refactor(reply): add reply observer after scene checks` | 新增 `app/agent/reply/observer.py`；把「触发/参与度」块移到场景判定之后（§1.2），块内先观察再判定；删除误置的 `user_recorded`；`get_or_create_session(..., observe=True)` 绕开 `can_reply` | 手测：bot 刚回完 5s 内发普通消息 → 历史里仍有它；禁用群不写 |
| A-5 | `fix(reply): bypass the legacy session gate in the participation path` | `prompt_prep.py:135` 二次 `get_or_create_session` 走同一 `observe` 口径；`chat.py:223-224` 的 None 分支补 `log_skip` | 手测：`reply_cooldown` 内被 @ 仍能请求；返回 None 有日志 |
| A-6 | `fix(reply): derive is_at from the gate instead of hardcoding false` | `pipeline.py:491-536` 用 gate 的 `at_me`/`reply_me`；`ctx.state["is_at"]` 正确 | 手测：群 @ 时日志 `is_at=True`，强制发言路径可达 |
| A-7 | `fix(reply): gate before cooldown and exempt at/reply` | `_cooldown_ok` 拆 `check()`/`mark()`：门通过后才计时；@/reply 豁免 | 手测：门拒绝不推进冷却；@ 不被冷却挡 |
| A-8 | `fix(reply): resolve reply targets instead of treating any quote as mine` | `_is_reply_to_me` 用 reply 段目标 id 反查发送者（会话历史优先，查不到保守按"非我"） | 手测/日志：引用别人不进、引用我进 |
| A-9 | `refactor(reply): single reply policy table` | 新增 `app/agent/reply/rules.py`（`ReplyPolicy`：mode/level/成本闸门/冷却/预算/豁免 + 文案生成）；`participation.py` 降为薄壳或删除（含重复的 `participation_section`）；`reply_rules_section` 读生效配置 + `trigger_mode_from()` | `compileall`；段文案两种模式各跑一次 |
| A-10 | `fix(pipeline): record participation decisions with the new gate` | 通过门后写 `Decision(kind=respond, reason=gate.reason)`，不再套 `trigger_check`；`_would_trigger_llm()` 复用同一谓词（修打断） | 日志/判定链显示参与度来源；`high` 下新消息打断成立 |
| A-11 | `fix(reply): require a speech output strategy in participation mode` | 能力判定按**实际生效策略**（`select_default_output`/`capabilities.from_config` 结果）；不满足 → `log_skip("participation_needs_speech", level=error)` + return；`prompt_prep.py:276` 告警条件改成"参与度 && 非发言通道" | 手测：`speech_via_tool=False` → 0 请求 + error skip；正常配置可进入 |
| A-12 | `fix(usage): charge participation budget with real tokens` | 挂点 `usage/metered.py::_record`（覆盖流式/非流式/回退/摘要，即"该账号所有 LLM 调用"）；`Budget` 改模块级 `bot_id → Budget`；删 `budget.record(0)` | 手测日志：一次请求后预算余量下降 |

### 阶段 B：前端

| 编号 | 提交信息 | 说明 |
|---|---|---|
| B-1 | `feat(dashboard): expose participation token budget` | AI 面板触发卡补 `participation_max_tokens`；`AdvancedOptionsPage.vue:58` 标题「提示词组装」→「高级选项」；说明 ≤40 汉字、禁裸标识符 |
| B-2 | `build(dashboard): rebuild dist for reply-mode changes` | 单独提交 dist（与 src 分开）；改完在 9311 预览服务截图（1440/390） |

### 阶段 C：上线前手测（不写测试）

- 单账号、单群、`very_low` → `low` → `medium` 逐档各跑 30 条真实群消息；
- 检查：全量入历史、沉默原因分布、@ 必回、冷却不误伤、预算余量下降、无 `participation_needs_speech`；
- 回滚 = 切回 `legacy`。

### 阶段 D：指标（可选，上线后）

| 编号 | 提交信息 | 说明 |
|---|---|---|
| D-1 | `feat(telemetry): participation metrics` | 每群请求数 / 沉默原因分布 / 预算余量；把沉默原因从 debug 提到用户可见 |
| D-2 | `perf(session): bound group history under observe-all` | 评估 `MAX_HISTORY_MESSAGES=200` 与图片逐条落盘 |

### 阶段 E：一次性验收（用户口径：最后再检查）

1. **清理失效测试**：llm_prompt(4 文件)、llm_context 旧 focus/referent(5 文件)、history_search/get_chat_history(2 文件)、模块清单/能力(11 文件)——删或按新契约重写；`SECTION_LAYERS` 登记 `reply_rules`；`message_assembly/README.md`。
2. **补参与度契约测试**：观察优先（冷却期也写）/ 五档成本门 / @豁免冷却 / 引用判定 / 发言通道要求 / 预算扣减 / 文案与门同源。
3. **全量**：`pytest -q` 无收集错误 + `ruff check .` 无新增；前端 `npm run build` + 两尺寸截图量测（`overflow=0`）。

---

## 3. 改动位置（便于认领）

| 编号 | 文件 | 具体位置 |
|---|---|---|
| A-1 | `app/agent/tools/state.py:357-366` | 删 `search` 残留 |
| A-2 | `config_partition.py:185-219`；`config_schema.py:246-277,468`；`scripts/gen_sections.py`；`src/agent/{sections,ai-panel}.json` | 7 键**恰好一个**归宿；`CORE_KEYS` 派生加 surface |
| A-3 | `domain/dialogue_policy.py:45-57`；`loop/pipeline.py:459-500` | 白名单 + 生效配置 |
| A-4 | 新增 `app/agent/reply/observer.py`；`loop/pipeline.py:423-611`；`loop/turn_input.py:163-181` | §1.2 顺序 |
| A-5 | `loop/prompt_prep.py:130-145`；`loop/chat.py:223-224` | 二次会话门 |
| A-6 | `loop/pipeline.py:491-536` | is_at 由 gate 给 |
| A-7 | `participation.py:220-257` | `check()`/`mark()` 分离 |
| A-8 | `participation.py:185-190` | 反查 reply 目标 |
| A-9 | 新增 `app/agent/reply/rules.py`；`participation.py`；`assembly/builtin_sections.py:149-153,268` | 段构建改调新模块 |
| A-10 | `loop/pipeline.py:329-366`、`518-529` | — |
| A-11 | `delivery/output.py:241-253,304-317`；`capabilities.py:65-83,160-167`；`loop/pipeline.py`；`loop/prompt_prep.py:276-284` | 能力探测放 `delivery/output.py` |
| A-12 | `usage/metered.py:104-133`；`participation.py:136-170` | 模块级 Budget 表 |

---

## 4. 上线前置（人肉清单）

- [ ] 确认**当前生效输出策略 = 发言通道**（A-11 的能力检查可读；`llm_output` 若启用，形态必须是发言通道而不是 text/stream）。
- [ ] `llm_context` 在目标账号启用（`get_context` 取回被折叠的观察消息）。
- [ ] `participation_max_tokens` 调到真实预算（默认 10,000,000 等于"不限"）。
- [ ] 重启进程，前端 dist 强刷（`Ctrl+F5`）。
- [ ] `reply_mode` 保持默认 `legacy`；灰度按账号/群覆盖开启。
- [ ] 备份 `data/app.db`。

---

## 5. 已定口径（用户拍板）

| 编号 | 决定 |
|---|---|
| Q1 | token 预算按**该账号所有 LLM 调用**计（含压缩/摘要/记忆蒸馏）→ A-12 挂 `metered.py` |
| Q2 | **私聊不接入**参与度：群聊特有方案；私聊永远走 legacy 触发（A-4/A-9 的判定显式限定 `message_group`） |
| Q3 | **档位差异主要写在系统提示词**：gate 只做成本闸门、不写语义谓词（§1.1） |

---

## 6. 本轮不做

- 不重写 `message_assembly`（渲染层已定型，本轮只消费）。
- 不恢复 `llm_prompt` 模块。
- 不动 `llm_context` 的 `get_context` 协议。
- 不做"参与度学习/自适应"。
- 不做私聊参与度。
