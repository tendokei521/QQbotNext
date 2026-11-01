# 参与度回复模式 · 收口计划

> 面向：新版回复模式（`reply_mode=participation`）从"骨架"到"可以上真实群"。
> 依据：HEAD `ba6e55f2` 的代码实测（2026-11-01，含一次只读审计 + 一次全量 pytest 基线），不是设计稿推测。
> 规则同 [`README.md`](README.md)：一个提交只做一件事、清单活维护、改完补 hash。

**修订记录**

- v1（20:0x）：按交接清单核对，确认 6 项阻塞。
- v2（20:4x）：审计修正两处口径 —— ①沉默失效**取决于 `speech_via_tool` 的解析值**，当前环境回退的是流式发言通道（沉默成立），坏组合是"`llm_output` 启用 + 输出形态 text/stream"；②**只把键加进白名单不够**，配置写入在 API 层就被静默丢弃。另新增 4 项实测阻塞（`is_at` 硬编码、`prompt_prep` 二次会话门、引用判定、配置静默丢弃）。补充 pytest 基线。

---

## 0. 结论先行：现在不能上真实群

| 编号 | 阻塞项 | 证据（实测） | 后果 |
|---|---|---|---|
| **P0-1** | **参与度配置写不进库（静默）** | `reply_mode` / `participation_*` / `structured_assembly` / `include_sent` 都不在 `CORE_KEYS`（`config_partition.py:215-219`）→ 被 `config_schema.py:1260` 从 `SCHEMA` 过滤掉 → `app/webui/api/agent.py:218-221` 对 `SCHEMA.get(key) is None` 的键**静默 `continue`**（不写、不报错）。7 键无归宿也是 `test_config_partition` 的失败原因 | 用户把开关切成"参与度驱动"，2 秒后保存成功、刷新**回到 legacy**；`structured_assembly` 的"高级选项"开关同样空转 |
| **P0-2** | **参与度分支根本进不去** | `pipeline.py:459` 用 `_effective_config()` 取配置，该函数只回填 `SCOPE_OVERRIDABLE["agent"]` 的 **9 个白名单键**（`pipeline.py:129-153` + `domain/dialogue_policy.py:45-57`），`reply_mode` 不在其中 → `config.get("reply_mode")` **恒为 `"legacy"`**。实测：`_effective_config()` 只返回 `['trigger_mode','trigger_probability']` | 即使配置进了库，群聊路径仍逐字走旧触发（只在 @/关键词时请求）；而提示词层按账号级配置已渲染参与度约定 → **提示词说"你自己决定说不说"、系统却只在被叫时才叫它** |
| **P0-3** | **消息永久丢历史** | `pipeline.py:468-469` 先 `observe_message()` 再**无条件** `ctx.state["user_recorded"]=True`；`observe_message` 在 `get_or_create_session()` 返回 `None` 时返回 `False`（`participation.py:286-288`，且它传 `ctx=None` → @ 豁免永不成立），`turn_input.py:178` 在「群聊 + bot 刚回复过 `reply_cooldown` + 非 @」时返回 `None`；失败路径只 `logger.debug` | 该条消息**本轮仍在请求里**（`format_user_context` 渲染 `ctx.user_text`），但**之后永久不在历史**（`prompt_prep.py:141` 见标记就跳过补写）；失败无用户可见痕迹 |
| **P0-4** | **通过所有门后整轮静默消失** | `prompt_prep.py:135` **第二次**调 `get_or_create_session()`，此时 `ctx.state["is_at"]` 仍是 False（见 P0-5）→ 冷却期内返回 `None` → `prompt_prep` 返回 `(None,None,None)` → `chat.py:223-224` **直接 return，无 log_skip、无日志**。且冷却/预算已在 `pipeline.py:483/500` 被扣掉 | 真被 @ 也可能一句话没有；`participation_cooldown_seconds=0` + `high` 可稳定复现。这是"新版不敢上群"的直接原因之一 |
| **P0-5** | **`is_at` 被硬编码 False** | `pipeline.py:501` `triggered, is_at, user_text = True, False, ...` → 后续 `pipeline.py:531-536` 的赋值被跳过 | ① `_should_force_speak`（`assistant_output.py:75-88`）为假 → 群里被 @ **没有**"补一轮必须发言"的兜底（"叫了不理"无补救）；② `turn_input.py:178` 的 @ 冷却豁免永不生效 |
| **P0-6** | **冷却顺序错、@/回复被误伤** | `pipeline.py:482-491`：先 `_cooldown_ok()` 再 `gate()`；`_cooldown_ok()` 在**通过检查的那一刻**就写 `last[key]=now`（`participation.py:232-234`），且不看 `is_at`/reply 段 | 被门拒绝的消息也消耗冷却；群里无意义消息把冷却刷满，真正 @ 到达时被 `participation_cooldown` 拦掉 |
| **P0-7** | **引用判定过宽** | `_is_reply_to_me`（`participation.py:185-190`）看到**任意** reply 段就返回 True（注释也承认） | `very_low`/`low` 承诺的"只被 @/回复才回"不成立：任何引用了别人的消息都会进 LLM，既烧钱也乱接话 |
| **P0-8** | **观察早于场景/免打扰/用户例外** | 参与度块在 `pipeline.py:457-541`，场景拒绝 `561`、免打扰 `592-600`、用户例外 `601-611` 都在其后；预算在 `500`、api_key 在 `552` 之后才检查 | 已禁用群 / 免打扰时段 / 黑名单用户的消息被写进会话历史（污染上下文、隐私面），并消耗冷却与整账号预算 → **一个热闹但被禁用的群能把它群的额度耗光** |
| **P0-9** | **token 预算永远为 0** | `budget.record(0)`（`pipeline.py:500`）+ `Budget.record` 只在 tokens 为真时入队（`participation.py:155-159`）→ `used_tokens` 恒 0，`participation_max_tokens` 永不生效（请求数上限仍生效） | 成本闸门形同虚设；默认 10,000,000 等于"不限" |
| **P0-10** | **沉默语义取决于输出通道，且矛盾告警不可达** | `speech.py:314-320` 参与度强制 `speech_via_tool=True`；实际回退链 `output.py:304-317 → 241-253 → capabilities.py:65-83,160-167` 读**原始键**（agent 默认 True，`config.py:51`）→ 当前环境回退 `StreamOutput`（发言通道，沉默成立）。坏组合：`llm_output` 启用 + `mode=text/stream`（`llm_output/service.py:75-79`），或库里写了 `speech_via_tool=False` → 正文当回复发、`send_message` 气泡被丢（`run_turn` 无 flush，只有 `chat.py:463/539` 有）。而 `prompt_prep.py:276-284` 的"配置矛盾"warning 只覆盖 `speech is None` 分支，**参与度下不可达** | 同一份配置可能"能沉默"或"无法沉默"，取决于输出模块形态，且**没有任何日志**说明当前是哪一种 |

补充（不阻塞灰度，但需在同轮修）：

- `reply_rules_section`（`participation.py:63-110`）读账号级 `req.config`，**读不到群级 policy**；旧版分支直接读 `trigger_mode` 而不走 `trigger_mode_from()`，只配了 `trigger_at` 的账号永远显示"仅 @"。
- `participation_section` 定义两次（`participation.py:113-115`、`118-130`），全仓**零引用**；`__all__` 仍导出 → 按旧名接入的一方在 legacy 下拿到空列表（段静默消失）。
- 判定链记旧模式：通过参与度门后仍写 `trigger_check(mode="at", allowed=True)`（`pipeline.py:518-529`），门的结论（`at_or_reply`/`level_low`）只进 debug 日志（`skip_log` 默认 warning 才用户可见）→ "为什么不回"对用户不可见。
- 打断代际失效：`_would_trigger_llm`（`pipeline.py:329-366`）无参与度分支 → 非 @ 消息不抬高代际，`interrupt_enable` 在 `high/very_high` 下不生效（只剩 pool 的 `superseded` 兜底）。
- 私聊无参与度分支（全仓无 `message_private` 侧判定）。
- 全量写入后 `MAX_HISTORY_MESSAGES=200`（`session/manager.py:434`）滚动加快；但模型实际只看 `history_recent_messages=12`（`config.py:67-68`），被折叠部分只能靠 `llm_context.get_context` 取回。
- `app/agent/tools/state.py:360` 引用已删除的 `search` 变量（web_search 删除残留），异常被吞 → `resolve_all_tool_states()` 跳过后续登记，**`recall_tool_result` 不在工具清单里**。与参与度无关但干扰验收，排 S0-1。
- `budget_of`（`participation.py:162-170`）`setattr` 失败时每次 new 一个 Budget，预算静默失效。
- 新版链路**零测试覆盖**（`grep participation|reply_mode tests/` 只命中已删的旧键）。

### 0.1 测试基线（实测 `venv\Scripts\python.exe -m pytest -q`）

| 命令 | 结果 |
|---|---|
| `pytest -q` | `1 warning, 9 errors in 2.28s` → **`Interrupted: 9 errors during collection`（0 个用例执行）** |
| `pytest -q --continue-on-collection-errors` | **`56 failed, 2693 passed, 19 skipped, 37 errors in 109s`** |
| `python -m compileall -q app` | exit 0，无语法错误 |

- 37 errors = **9 个收集错误**（模块 import 即挂）+ **28 个 setup 错误**（`tests/agent/context/test_llm_context_expand.py` 的 autouse fixture）。
- 9 个收集失败文件里共 **135 个用例从未被收集**；加 28 个 setup error ≈ **163 个用例根本没跑** → "56 failed"是低估。
- 根因分组（上一轮改造遗留，**不是**新装配架构的副作用）：llm_prompt 已删(4 文件) / llm_context 旧 focus·referent·context_tools 已删(5 文件，含 28 setup error) / history_search 已删(1) / get_chat_history 已删(18 例) / 提示词文案与历史行格式期望过期(8) / 框架仍要求 llm_prompt 存在(9) / 配置分区孤键(3) / 文案与架构卫生(6) / 测试桩漂移(3)。
- **结论：S0 清零收集错误之前，任何"跑全量 pytest 判断回归"都是假信号。** 参与度改造的验证只能靠新契约测试（§4）+ 定向用例。

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
                            · gate(level, event) → allow/deny + reason + checks
                            · 冷却（@/回复豁免）→ 预算（请求数 + 真实 token）
                            · 产出 Decision(kind=respond|silent, reason=...)
                            · 同一份表同时生成提示词文案（reply_rules 段）
                                           │
                         ④ LLM（能力检查通过才进入）
                                           │
                         ⑤ Sender（发言通道）
                            · participation 下必须确认"当前生效策略 = 发言通道"，否则 fail-loud
                            · 未调用 send_message = 沉默；被 @/回复必须有发言
```

### 1.1 判定顺序契约（P0-3/P0-8 的核心，别做漂移）

| 顺序 | 阶段 | 失败时 |
|---|---|---|
| 1 | `#llm` 指令 | 走命令分支 |
| 2 | 场景：账号启用 / 场所 / 权限 / 免打扰 / 用户例外 | `return`，**不写历史、不消耗冷却/预算** |
| 3 | **Observer**（仅 `participation`）：写会话历史 | 失败记 warning（用户可见级别），不置 `user_recorded` |
| 4 | 触发判定（legacy）或 **Decider**（participation：gate → 冷却 → 预算） | `log_skip` + `return`（消息已入库） |
| 5 | api_key / debounce / pre_request / 会话锁 / LLM | 同现状 |

日志语义：**场景拒绝 > 触发拒绝 > 无 API Key**；api_key 检查留在门之后、debounce 之前。

三条硬契约（写进测试）：

1. **观察先于门**：`participation` 下，通过入站门控的每条群消息都进会话历史；门/冷却/预算只决定"要不要请求"。
2. **规则单一来源**：门的谓词、提示词文案、冷却与预算策略都由 `ReplyPolicy` 派生，禁止第二处判定。
3. **沉默可证伪**：`silent` 必须有 reason；`participation` 下若当前生效输出策略不是发言通道，必须拒绝进入并记 error（不允许"文本即回复"的静默回退）。

---

## 2. 阶段与提交

状态：`[ ]` 未开始 / `[~]` 进行中 / `[x]` 已完成（补 hash） / `[!]` 阻塞

### S0 基线归零（前置：不然后面"跑了什么"都不可信）

上一轮删模块时没有同提交删掉对应测试（违反 AGENTS.md 硬性底线 #2），9 个文件在**收集阶段**就挂。
S0 全是"删/改测试 + 1 行生产修复"，不改功能语义：

| 编号 | 提交信息 | 改动 | 验收 |
|---|---|---|---|
| S0-1 | `fix(tools): remove stale search reference in tool state` | `app/agent/tools/state.py:357-366` 删掉 `search` 残留（360-362） | `tests/agent/tools/test_recall_tool.py` 转绿；工具清单能列出 `recall_tool_result` |
| S0-2 | `test: drop llm_prompt tests for the removed module` | 删 `tests/plugins/test_llm_prompt.py`；`tests/agent/assembly/test_llm_user_format.py`、`tests/agent/session/test_llm_history_h5.py`、`tests/agent/session/test_llm_history_unified.py` 改为断言**框架内置组装** | 4 个 collection error 消失 |
| S0-3 | `test: drop llm_context focus/referent tests for the rewritten module` | `test_llm_context_tools.py`、`test_llm_referent.py`、`test_llm_focus.py`、`test_llm_history_image_expand.py`、`test_llm_context_expand.py`（28 个 setup error 来源）按新 `get_context` 契约重写或删除 | 收集/装配错误清零；`get_context` 保留契约测试 |
| S0-4 | `test: drop history_search and get_chat_history tests` | 删 `tests/agent/tools/test_history_search.py`；`test_llm_session_tools.py`、`test_llm_initiative_tools.py` 去掉已删工具断言 | 18 例恢复或删除 |
| S0-5 | `test: align module/capability tests with built-in assembly` | `test_builtin_module_pages`、`test_modules_end_to_end`、`test_module_config_integration`、`test_single_service_declaration`、`test_framework_capabilities`、`test_capability_prompt_sections`、`test_llm_prompt_proactive`、`test_llm_assembly_paths`、`test_group_log_integration`、`test_prompt_preset_service`、`test_persona_rules` | 失败仅剩已知项（文案/分区） |
| S0-6 | `docs(agent): document message_assembly and register its sections` | 新增 `app/agent/message_assembly/README.md`；`app/agent/assembly/layers.py:66` 的 `SECTION_LAYERS` 登记 `reply_rules` | `test_architecture_layout` / `test_prompt_layers` 绿 |

> S0 目标：`pytest -q` **无 collection / setup error**，失败集合只剩"参与度相关 + 文案"。

### P0 止血（必须全部完成才可开灰度）

| 编号 | 提交信息 | 改动 | 验收 |
|---|---|---|---|
| P0-1 | `fix(config): persist participation reply-mode keys` | `config_partition.py`：`reply_mode`/`participation_*` → `PANEL_GROUPS` 新增组（编辑入口就是 AI 面板触发卡）；`structured_assembly` → 新 surface `advanced_options`（编辑入口是高级选项页）；`include_sent` → `DELETED_KEYS`（无消费方）。**注意**：`CORE_KEYS` 目前只由 `PANEL_KEYS + UI_PAGES + REPLY_KEYS` 派生，新 surface 不会自动进入 → 需把 `SURFACE_GROUPS` 的各组并入派生（并同步 `test_core_keys_are_derived_from_pages_and_surfaces` 的公式），否则键仍被过滤；跑 `scripts/gen_sections.py` 重新生成 `sections.json` / `ai-panel.json` | 单测：`POST /api/agent/config` 写入后 `GET` 能读回；`test_config_partition` + section 契约绿；`test_ui_copy`（说明 ≤40 汉字）绿 |
| P0-2 | `fix(reply): read the reply mode from the effective config` | `SCOPE_OVERRIDABLE["agent"]` 加 `reply_mode`/`participation_level`/`participation_max_requests`/`participation_max_tokens`/`participation_cooldown_seconds`（`test_overridable_whitelist_is_explicit` 只断成员，不会红）；`pipeline.py:459-500` 用生效配置 | 单测：`_effective_config` 含 `reply_mode`；日志出现 `reply_mode=participation` |
| P0-3 | `refactor(reply): add reply observer after scene checks` | 新增 `app/agent/reply/observer.py`；把「触发/参与度」块整体后移到场景判定之后（§1.1），块内先观察再判定；删除分支内观察与误置的 `user_recorded`；`turn_input.get_or_create_session(..., observe=True)` 绕开 `can_reply` | 单测：冷却期消息仍写入；observe 失败不置 `user_recorded`；禁用群/免打扰不写历史 |
| P0-4 | `fix(reply): bypass the legacy session gate in the participation path` | `prompt_prep.py:135` 二次 `get_or_create_session`：参与度分支传入已观察的 session / `observe=True`，不再被 `can_reply()` 静默吞掉；`chat.py:223-224` 的 None 分支补 `log_skip` | 单测：bot 刚回复过（`reply_cooldown` 内）+ 被 @ → 仍能进入 LLM 且发请求；返回 None 时日志可见 |
| P0-5 | `fix(reply): derive is_at from the gate result` | `pipeline.py:501` 用 `gate()` 给出的 `at_me`/`reply_me`，删除硬编码 False；`ctx.state["is_at"]` 与 `ctx.user_text` 去 @ 逻辑保持 | 单测：被 @ 时 `is_at=True`、强制发言路径可达；非 @ 时不受影响 |
| P0-6 | `fix(reply): gate before cooldown and exempt at/reply` | `_cooldown_ok` 拆成 `check()`/`mark()`：门通过后才计时；`@`/`reply` 豁免（用 P0-5 的 `is_at`/`reply_me`） | 单测：门拒绝不消耗冷却；@ 不被冷却挡住；冷却只在真请求后推进 |
| P0-7 | `fix(reply): resolve reply targets instead of any quote` | `_is_reply_to_me` 用 reply 段的目标 message_id 反查发送者（会话历史 / `get_msg`），查不到时按"非我"保守处理 | 单测：引用别人 ≠ 回复我；引用我 = 回复我；查不到时保守 |
| P0-8 | `fix(reply): single reply policy table` | 新增 `app/agent/reply/rules.py`（`ReplyPolicy`：mode/level/cooldown/budget/exempt）；`participation.py` 删除或降为兼容壳（含重复定义的 `participation_section`）；`reply_rules_section` 读生效配置 + `trigger_mode_from()` | 单测：五档 gate 表驱动；两种模式文案；群级覆盖生效 |
| P0-9 | `fix(pipeline): record participation decisions with the new gate` | 通过门后写 `Decision(kind=respond, reason=gate.reason)`，不再套 `trigger_check` 旧模式；`_would_trigger_llm()` 复用同一 gate 谓词（修打断代际） | 单测：判定链记录参与度来源；`high` 下新消息抬高代际 |
| P0-10 | `fix(reply): require a speech output strategy in participation mode` | 能力判定按**实际生效策略**（`select_default_output`/`capabilities.from_config` 的解析结果，不是 `runtime.output` 是否存在）；不满足 → `log_skip("participation_needs_speech", level=error)` + return，**不进入 LLM**；把 `prompt_prep.py:276` 的告警条件改成"参与度 && 非发言通道" | 单测：`speech_via_tool=False` / `llm_output mode=text` → 0 次请求 + error skip；发言通道 → 正常；坏组合日志可见 |
| P0-11 | `fix(usage): charge participation budget with real tokens` | 挂点 `usage/metered.py::_record`（覆盖流式/非流式/回退/摘要）；`Budget` 改为模块级 `bot_id → Budget` 表；删除 `budget.record(0)` | 单测：一次请求后 token 预算减少；token 耗尽 → `silent(budget)`；`setattr` 失败不再静默失效 |

### P1 补齐（灰度期做）

| 编号 | 提交信息 | 说明 |
|---|---|---|
| P1-1 | `test(reply): contract tests for observe/gate/budget` | §4 的四支契约测试（S0 已清掉失效测试，这里只加新的） |
| P1-2 | `feat(dashboard): expose participation token budget` | AI 面板触发卡补 `participation_max_tokens`；`AdvancedOptionsPage.vue:58` 标题「提示词组装」→「高级选项」；说明文案 ≤40 汉字、禁裸标识符 |
| P1-3 | `build(dashboard): rebuild dist for reply-mode changes` | 单独提交 dist（与 src 分开） |
| P1-4 | `feat(reply): participation mode for private chats` | 私聊接入同一 Observer/Decider；默认沿用账号级配置 |
| P1-5 | `perf(reply): avoid rebuilding SessionManager in the gate` | `_bot_spoke_recently`（`participation.py:200-210`）每次 new 一个 `SessionManager`，改成复用 `runtime.session_mgr` |

### P2 观测与压力（上线后）

| 编号 | 提交信息 | 说明 |
|---|---|---|
| P2-1 | `feat(telemetry): participation metrics` | 每群请求数 / 沉默原因分布 / 预算余量；把 `level_low` 这类沉默原因从 debug 提到用户可见 |
| P2-2 | `perf(session): bound group history under observe-all` | 评估 `MAX_HISTORY_MESSAGES=200` 与图片逐条落盘；按会话配置历史上限或"最近 N 分钟 + N 条"滚动 |

### 灰度（P0 全绿后）

1. 单账号、单群开 `reply_mode=participation` + `participation_level=very_low`（最保守）；
2. 观察 24h：沉默原因分布、`user_recorded` 缺失次数、预算余量、`participation_needs_speech` 是否出现；
3. 逐档升到 `low` → `medium`；`high/very_high` 先非生产群；
4. 回滚 = 一键切回 `legacy`（新键默认值保持 legacy，不下发默认值变更）。

---

## 3. 改动位置（便于认领）

| 编号 | 文件 | 具体位置 |
|---|---|---|
| P0-1 | `config_partition.py:185-219,327-...`；`config_schema.py:246-277,468`；`scripts/gen_sections.py`；`src/agent/{sections,ai-panel}.json`；`webui/api/agent.py:218-221`（确认不再过滤） | 7 键须**恰好一个**归宿 |
| P0-2 | `domain/dialogue_policy.py:45-57`；`loop/pipeline.py:459-500` | 白名单 + 生效配置 |
| P0-3 | 新增 `app/agent/reply/observer.py`；`loop/pipeline.py:423-611`；`loop/turn_input.py:163-181`；`loop/prompt_prep.py:141-144` | §1.1 顺序 |
| P0-4 | `loop/prompt_prep.py:130-145`；`loop/chat.py:223-224` | 二次会话门 |
| P0-5 | `loop/pipeline.py:491-536` | is_at 由 gate 给 |
| P0-6 | `participation.py:220-257` | `check()`/`mark()` 分离 |
| P0-7 | `participation.py:185-190` | 反查 reply 目标 |
| P0-8 | 新增 `app/agent/reply/rules.py`；`participation.py`；`assembly/builtin_sections.py:149-153,268` | 段构建改调新模块 |
| P0-9 | `loop/pipeline.py:329-366`、`518-529` | — |
| P0-10 | `delivery/output.py:241-253,304-317`；`capabilities.py:65-83,160-167`；`loop/pipeline.py`（门前）；`loop/prompt_prep.py:276-284` | 能力探测函数放 `delivery/output.py` |
| P0-11 | `usage/metered.py:104-133`；`participation.py:136-170` | 模块级 Budget 表 |

---

## 4. 测试策略（契约，不是实现快照）

1. `tests/agent/test_reply_observer.py`
   - 冷却期、非 @ 的群消息**仍写入**历史（回滚 P0-3）；
   - 观察失败不返回 `user_recorded=True`，且日志可见；
   - 群被禁用 / 免打扰 / 用户例外时不写入（观察点在场景判定之后）。
2. `tests/agent/test_reply_gate.py`
   - 五档 gate 逐档断言；`@`/`回复` 豁免冷却；门拒绝不消耗冷却；
   - 预算：请求数用尽 → `silent(budget)`；token 用尽 → `silent(budget)`。
3. `tests/agent/test_reply_rules_text.py`
   - 同一 `ReplyPolicy` 生成的文案与 gate 结论一致（`very_low` ⇒ 文案含"仅被 @"）；
   - 旧版文案随 `trigger_mode_from()` 变化（只配 `trigger_at` 不当成"已配置触发方式"）。
4. `tests/agent/test_reply_speech_required.py`
   - 参与度 + 非发言通道（`speech_via_tool=False` / `llm_output mode=text`）→ 0 请求 + error 级 skip；
   - 参与度 + 发言通道 → 正常进入。
5. 架构：`tests/architecture/test_config_partition.py`、`tests/agent/test_agent_section_contract.py`、`test_ui_copy` 必须绿。

> 按 AGENTS.md：删/改模块时**同一提交**删掉对应测试——上一轮删 llm_prompt / llm_context 旧实现时没做到，S0 就是在补这笔账。

---

## 5. 上线前置（人肉清单，跑完再开灰度）

- [ ] `pytest -q` 无 collection / setup error（S0 完成）。
- [ ] 确认**当前生效输出策略 = 发言通道**（P0-10 的能力检查在前端可读；`llm_output` 若启用，形态必须是"发言通道"而不是 text/stream）。
- [ ] `llm_context` 模块在目标账号启用（`get_context` 工具；否则被折叠的观察消息取不回来）。
- [ ] `participation_max_tokens` 从 10,000,000 调到真实预算（当前默认等于"不限"）。
- [ ] 重启进程（改 `.py` 生效），前端 dist 强刷（`Ctrl+F5`）。
- [ ] `reply_mode` 保持默认 `legacy`；灰度按账号/群覆盖开启。
- [ ] 备份 `data/app.db`（沿用 `backups/` 惯例）。

---

## 6. 待确认（需要产品决定，不阻塞 P0 编码）

| 编号 | 问题 | 建议 |
|---|---|---|
| Q1 | token 预算口径：只算群聊请求，还是该账号**所有** LLM 调用（含压缩/摘要/记忆蒸馏）？ | 建议"该账号所有调用"——预算是成本闸门，越保守越好；P0-11 就是这个挂法 |
| Q2 | 私聊是否本轮接入参与度（P1-4）？ | 建议先只做群聊，私聊保持 legacy |
| Q3 | `medium` 档"正在参与"的判据（当前是"会话尾部 6 条内有 assistant 消息"）够不够？ | 先保留；P2 引入"最近 N 分钟 bot 发言 + 被 @ 过"的显式状态 |
| Q4 | 引用判定（P0-7）反查失败时怎么兜底？ | 建议保守按"非我"（宁可少回，别乱接话）；若 QQ 侧拿不到目标作者，就先只信"引用内容里出现我的昵称" |

---

## 7. 本轮不做

- 不重写 `message_assembly`（渲染层已定型，本轮只消费）。
- 不恢复 `llm_prompt` 模块（已删，配置项只保留 `structured_assembly`）。
- 不动 `llm_context` 的 `get_context` 协议（只保证它启用后可用）。
- 不做"参与度学习/自适应"（先用固定档位跑通）。
