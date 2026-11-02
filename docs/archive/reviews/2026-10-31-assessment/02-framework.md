# 02 · 框架侧盘点：提示词装配与输出路径的判定点

> 盘点员：framework-path · 2026-10-31 20:5x–21:0x
> **只读审计**：未修改任何 `.py`，未运行任何测试、未连真实账号（`venv\Scripts\python.exe` 仅用于一次 `hasattr` 类属性探测，见 §6.3 脚注）。
> 行号以本次读取时为准（HEAD = `1dd9d987`）。旧文档 `docs/reviews/2026-10-31-prompt-assembly-analysis.md` 的结论**已不适用**，本文不复述它。
> **路径简写约定**：`loop/…` = `app/agent/loop/…`；`assembly/…` = `app/agent/assembly/…`；`delivery/…` = `app/agent/delivery/…`；`persona/…` = `app/agent/persona/…`；`group_log/…` = `app/agent/group_log/…`；`plugin/…` = `app/plugin/…`；`llm_output/…`、`llm_assembly/…`、`llm_proactive/…`、`llm_schedule/…`、`llm_message_meta/…` = `app/plugins/…`。
> 交付路径：`logs/review/assessment/02-framework.md`（`/logs/` 在 `.gitignore:58`，不进仓库）。

---

## 0. 结论速览（先给答案）

1. **五层装配通了，但只在 chat 一条路径上通。** 五层（L1 系统提示词 / L2 人格 / L3 注入 / L4 历史 / L5 本轮）在
   `assembly/layers.py:41` 定义、`sections.py:200` 排序、`assembler.py:274` 合并 L1，代码自洽且有架构测试（`tests/architecture/test_prompt_layers.py`）。
   但 `PromptRequest.config` 决定了"有没有行为段"：**chat 路径传 `policy_view`（拿到三套方案），主动消息/定时任务传的是模块自己的 `ModuleConfig`（拿不到 `prompt_preset`）→ 行为规则段整段消失**。
   即旧文档说的"行为段消失"在 chat 路径已修好（`presets.resolve` 哨兵写法，`presets.py:486-514`），**在主动/定时路径仍然存在，且是常态**。
2. **"是否发言"这个概念一共 17 个判定/落点**：7 处"通道判定"（文本算不算发言）+ 8 处"结果判定"（到底发没出去）+ 2 处**该有却没有**的判定（`run_turn`、主动/定时路径）。其中 5 处已收敛到同一个函数 `delivery/speech.py:314 speech_via_tool()`（本期改造的成果），1 处（`capabilities.from_config`）只在输出槽为空时生效、属兜底；**真正的缺口是完全没有判定的那 2 处**（见第 2 节 D1–D3）。
3. **发现一条与用户症状逐字对应的静默链（纯代码推导，未实测）**：
   任何模块用 `llm_turn()` / `client.llm_chat_turn()` 跑一轮时 → `turn.py:306` 的 `stream` 只看 `stream_output`（llm_output 模块默认 **False**）→ 走**非流式** `run_turn` → 但同一次请求里 `prompt_prep.py:247` 已按 `mode=speech` 挂上 `send_message/finish` 工具、`assembler.py:484` 已把「文本只是思考」贴进 user 尾 →
   模型只调 `send_message` 不写正文 → `run_turn` **从不 flush outbox**（`turn.py` 全文无 `speech` 字样）→ `result.text=""` → **整轮静默，气泡被丢弃**。
4. **发送出口不是唯一一条**：`delivery/channel.py:42 send_item` 自称"唯一发送出口"，但 chat 流水线走 `pipeline.py:752`，主动/定时走 `bot.send_*` 裸发送（`llm_proactive/src/manager.py:391-396`、`llm_proactive/src/stream.py:110-114`）。后两条**没有 pre_send/post_send、不解析 `[reply]`/`[@QQ]`、不记 msg_out、不回填 message_id**，而主动消息的提示词却教模型写 `[reply]`（`prompt.py:47-51` 经 `builtin_sections.py:216-222` 注入）。
5. **`runtime` 传错对象是主动/定时路径的总病根**：`build_for(self.module, req)` / `apply_post_assemble(self.module, …)` 传的是 **BaseModule 实例**，不是 `AgentRuntime`。已探测该类**没有** `assembler / prompt_sections / llm_hooks / memory_service / provider_chain` 属性 → 于是插件注册的 section、`llm_assembly` 的装配器、`post_assemble` 钩子、prompt dump、token 预算、记忆块、工具源在主动/定时路径**全部静默失效**（第 6.3 节有逐项对照）。
6. **同一键不同层不同值是常态，且有 1 个已确认的生效差异**：`emoji_react_level` 在 agent 默认是 `1`（`config.py:43`）、在 `llm_assembly` 模块默认是 `0`（`app/plugins/llm_assembly/main.py:51`）→ 模块装好的账号**默认整段【可用表情】不注入**（`builtin_sections.py:189-193`），而行为段仍在说"可用标签见【可用表情】"（`persona/rules.py:385`）→ 指涉悬空。这一条与日志取证侧观察到的"`emoji_hint` 在 191 与 211 之间跳"吻合（`logs/review/assessment/03-logs.md` §0.4）。

---

## 1. 实际装配顺序（五层的真实落点）

### 1.1 总链路（chat 路径，逐跳 file:line）

```
pipeline._run                                   loop/pipeline.py:423
  └ resolve_output(runtime)                     delivery/output.py:294
      └ OutputStrategy.respond()                llm_output/service.py:70（或 output.py:67 / :126）
          ├ StreamOutput  → chat.stream_response        output.py:140-154 → chat.py:213
          └ BlockingOutput→ generate.generate_response  output.py:67    → generate.py:29 → turn.py:141
              ↓
          prepare_prompt                        loop/prompt_prep.py:94      ← 唯一取值入口
              ├ config = policy_view(runtime)   prompt_prep.py:244-246     ← 关键分岔（见 §5）
              ├ speech = _build_speech(policy_cfg)  prompt_prep.py:247
              ├ all_specs += build_speech_tools(speech)  prompt_prep.py:251
              ├ extra/user blocks = _collect_extra_blocks  prompt_prep.py:297-305
              └ PromptRequest(config=policy_cfg)     prompt_prep.py:332-361
              ↓
          assembly.build_for(runtime, req)      assembler.py:628
              └ PromptAssembler.build(req)      assembler.py:403
                  ├ _built → build_all_blocks   assembler.py:398/241
                  │    ├ resolve_blocks         assembler.py:224  → sections.registry_of(runtime).resolve()  sections.py:172
                  │    ├ 逐段 section.builder(req)   assembler.py:262-270
                  │    └ merge_layer_blocks(L1) assembler.py:271 → 274
                  ├ flatten                     assembler.py:446
                  ├ place_images                assembler.py:561
                  ├ sanitize（模态兜底）         assembler.py:605
                  ├ _remember_blocks(ctx.state) assembler.py:416
                  └ apply_speech_protocol       assembler.py:414 → 451
              ↓
          _append_round_images                  chat.py:285 / turn.py:248
          apply_post_assemble(runtime, …)       assembler.py:686 → hooks.run_stage("post_assemble") :710
              ├ token 预算收口 apply_request_policy   assembler.py:727
              ├ messages.before_send 中间件           assembler.py:742
              └ prompt dump record                   assembler.py:750 → dump.py:114
              ↓
          provider 请求（含工具多轮）            loop/tool_step.py:224 / :452
              ↓
          pipeline._drive: pre_send → _send → post_send  pipeline.py:652-684
```

### 1.2 段落 × 生效条件 × 文案来源

| 层 | order | 段 | builder（file:line） | 生效条件（读哪个键/探测） | 文案来源 | 空段行为 |
|---|---|---|---|---|---|---|
| L1 | 20 | `behavior` | `builtin_sections.py:133` | `presets.resolve(req.config)` 非 None（`prompt_preset` 键**存在**）；`behavior_rules_override` 可整体替换 | 三套预设（`presets.py:438/446/458`）/ `persona_rules.SEGMENTS`（`rules.py:458`）/ 设置页覆盖（`prompt_preset_service.get_override`，`builtin_sections.py:153-157`） | 返回 `[]`，不产生消息；**整段消失** |
| L1 | 40 | `schedule` | `builtin_sections.py:124` | `req.schedule_enable`（来自 `policy_get(runtime,"schedule","schedule_enable")`，`prompt_prep.py:234`）；主动/定时路径显式 `False` | `SCHEDULE_INSTRUCTION`（`prompt.py:11`）或预设版（`presets.py:346/355`） | `[]` |
| L1 | 50 | `proactive` | `builtin_sections.py:208` | `build_proactive_instruction` 里任一行成立（`prompt.py:265`）：有 history/poke/quote 工具或命中意图补强 | `prompt.PROACTIVE_LINES`（`prompt.py:84`）+ 预设覆写 | `None`→`[]` |
| L1 | 60 | `message_meta` | `builtin_sections.py:260` | `ctx.state["message_meta_injected"]`（`builtin_sections.py:237`，由 `llm_message_meta/src/service.py:166` 置位）+ `meta_instruction_mode != off` | `MULTI_LINE/MASKED/SINGLE_LINE_*`（`prompt.py:301/308/317`）或预设版 | `None`→`[]` |
| L1 | 65 | `safety_recap` | `builtin_sections.py:273` | **无（恒有）** | 常量 `SAFETY_RECAP`（`builtin_sections.py:267`） | 恒非空 |
| L2 | 10 | `persona` | `builtin_sections.py:77` | 恒有；`persona_preset_id` 可活引用预设 | 角色预设 `system_prompt` / `req.cfg("system_prompt")` | 恒非空（有默认人设） |
| L3 | 30 | `emoji_hint` | `builtin_sections.py:175` | ①`presets.resolve` 非 None ②有 `set_msg_emoji_like` ③`emoji_react_level > 0`（`:189`） ④词典非空 | `emoji_hint_line`（`prompt.py:325`）或预设版 | `[]` |
| L3 | 70 | `skills` | `builtin_sections.py:278` | `req.skill_blocks` 非空 | `runtime.skills.prompt_blocks()`（`tool_source.py:80`） | `[]` |
| L3 | 80 | `memory` | `builtin_sections.py:283` | `req.memory_text` 非空（**chat 路径恒空**，见 `prompt_prep.py:290`：记忆已改走 user 数据块） | 旧通道，实际无人写 | `[]` |
| L3 | 90 | `background` | `builtin_sections.py:288` | `referent_text` 或 `pre_history_text` 非空；后半段受 `history_background_enable`（**已删键**，见 §5.3） | 指代 provider + 群环境文本 | `[]` |
| L3 | 100 | `group_log` | `builtin_sections.py:309` | `req.group_log_text` 非空 | `group_log/context.py:build_context_text` | `[]` |
| L3 | 105 | `extra` | `builtin_sections.py:319`（`EXTRA_SECTION`，`:402`；**不在默认段表**，装配器每次插回，`assembler.py:232-233`） | `req.extra_blocks` 非空（`context_providers` 里 `role="system"` 的贡献） | 模块 | `[]` |
| L4 | 110 | `history` | `builtin_sections.py:304` | 非空；前面拼角色预设 `begin_dialogs`（`:102`） | 会话历史渲染（`context_blocks.py:139`）+ 压缩摘要 | `[]` |
| L5 | 120 | `user` | `builtin_sections.py:350` | 恒有 | `req.user_text`（元信息由 llm_message_meta 注入，见 §4） | 恒非空 |
| — | — | user 数据块 | `builtin_sections.py:341` + `assembler.py:582-584` | `req.user_blocks` 非空（`role="user"` 的贡献，如记忆） | 模块，带 `<memory_data>`（`context_data.py`） | 并入 user content |

**排序口径**：`layers.sort_key`（`layers.py:109`）= 层序 → 段自己的 `order` → 名字；由 `sections.resolve`（`sections.py:200`）与 `resolve_blocks`（`assembler.py:237`）两次使用，口径一致。
**L1 合并**：`merge_layer_blocks(built, "system_prompt")`（`assembler.py:274-323`）把 L1 里所有 `role=system` 的字符串按 `built` 顺序拼成**一条**，挂在层内第一个有内容的段上，段名变成 `system_prompt`、`contributors` 记录各段字数（`:312-319`）。因此最终 messages 的形态是：**1 条 L1 system + 1 条 L2 persona system + N 条 L3 system + 历史 + user**。

### 1.3 失败/空段语义（写死的行为）

| 情形 | 行为 | 依据 |
|---|---|---|
| 段注册表解析失败（>1 个 `complete` 段） | `logger.error` 后**重抛**，整轮不请求 LLM | `sections.py:201-207` + `assembler.py:254-260` |
| 单个段 builder 抛异常 | `logger.error` 后**跳过该段**（不中止本轮） | `assembler.py:264-270` |
| `post_assemble` 钩子置空 messages / `job.skip` | 本轮不请求 LLM（返回 None） | `assembler.py:710-715` |
| `messages.before_send` 中间件拦截 | 本轮不请求 LLM | `assembler.py:742-745` |
| `pre_send` 钩子返回 False 或置 `msg.skip` | 该条不发送 | `pipeline.py:667-670` |

### 1.4 文档 ↔ 代码的漂移（以代码为准）

| 文档说法 | 代码事实 |
|---|---|
| `docs/prompt-assembly-redesign.md` §9.2.3「`speech_via_tool` 需要 `stream_output=True`，否则告警并回落」 | **已删除该门槛**：`speech.py:317-324` 明确写"与 `stream_output` 无关"、"`stream_output` 参数仅为向后兼容保留"。告警也随之只剩 `_speech_intent` 一处（`prompt_prep.py:263-271`）。**文档陈旧** |
| 同文档 §9.4「非流式路径的发言通道当前明确不支持，只告警」 | 部分仍真：`run_turn` 无 speech 分支（§2.3 C1），但**连"告警"也没有**了（`speech.py` 里那段降级逻辑已不存在，只剩 `chat.py:316` 的"工具不可用"降级） |
| `assembler.py` 模块 docstring 的 12 段表（`:40-54`，无 `safety_recap`） | 实际默认 **13 段**（`builtin_sections.py:376-394`，`safety_recap` order=65 于 `:387`）。少列一行，`layers.SYSTEM_PROMPT_SECTIONS`（`layers.py:62`）才是全的 |
| `docs/prompt-assembly-redesign.md` 通篇的「组装档位 / `assembly_profile`」 | 已删除（提交 `45f99495`）；`prompt.py:146-150` 留了说明。`layer` 模型取而代之 |

---

## 2. "是否发言 / 文本发不发出去" —— 全部判定点（17 处）

### 2.1 通道判定：文本到底算不算发言（7 处）

| # | 位置 | 读什么 | 作用 | 谁覆写谁 |
|---|---|---|---|---|
| A1 | `delivery/speech.py:314-331` `speech_via_tool(config)` | `config.get("speech_via_tool", False)`，**唯一原始读点** | 供下面 A2/A3/A4/A5 调用 | — |
| A2 | `persona/rules.py:106-112` `detect_caps → caps["speech_by_tool"]` | 经 A1 | 行为段/引用段/禁令段的**措辞**两套（`rules.py:172-210`、`:336-346`、`:444-448`）；预设版在 `presets.py:121-150`、`:260-283` | 预设可换措辞，**不能改这个事实**（`presets.py:22-23` 纪律） |
| A3 | `assembly/assembler.py:473-489` `apply_speech_protocol` | 经 A1（`req.config`） | 决定「【发言协议】」是否贴到**本轮 user 消息末尾** | 若 A1=False 则整段不贴 |
| A4 | `loop/assistant_output.py:47-65` `_build_speech` | 经 A1 | 决定 `SpeechOutbox` 是否存在 → 决定 `send_message/finish` 两个工具挂不挂（`prompt_prep.py:247-251`） | 这是"工具是否存在"的唯一开关 |
| A5 | `loop/assistant_output.py:76-81` `_speech_intent` | 经 A1 | **仅用于"配置开了但没生效"的告警**（`prompt_prep.py:263-271`） | 无行为影响 |
| A6 | `capabilities.py:65-83` `OutputCapability.from_config` | **直读配置的 `speech_via_tool`**，不经 A1、不经任何视图 | 决定 `kind`（`speech/stream/blocking`）→ 决定 `streams` → 决定 `select_default_output` 返回 Stream 还是 Blocking（`output.py:241-253`）。**仅在 `runtime.output` 槽为空时才会被走到**（`output.py:294-307`）；`llm_output` 接管后本函数不再参与选实现（只剩声明查询用途） | 被 A7 的**声明**覆盖（`capabilities.py:160-167`：声明优先） |
| A7 | `llm_output/src/output_mode.py:38-62` `resolve_mode` + `:73-76` `OutputOptionsView.get("speech_via_tool")` | 模块配置的 `mode`（`text/stream/speech`），旧开关只做迁移推导 | ①`OutputOptionsView` 把 `speech_via_tool` 的答案**覆写**成 `mode=="speech"`，从而让 A2/A3/A4 全部跟着 `mode` 走 ②`llm_output/main.py:127-132` 按 `mode` 声明 A6 的能力 | **这是全系统唯一真正的"单一事实源"**；`output_mode.py:1-13` 的 docstring 明确写了它解决的就是用户症状 |

> **A1–A5 已收敛到同一个函数**，这是本期改造最有价值的成果；**A6 仍是独立读法**（直读 agent 配置），只在 A7 的声明存在时被覆盖。这是唯一的结构性分叉口。

### 2.2 结果判定：这一轮到底发没发出去（8 处）

| # | 位置 | 判据 | 后果 |
|---|---|---|---|
| B1 | `delivery/speech.py:229-232` `SpeechOutbox.silent` | `spoken==0 and not bubbles` | 定义"静默=正常结局" |
| B2 | `loop/chat.py:561-575` | `speech.spoken == 0` | 打 `静默（看过但没说话）` 或 `流式失败且未开口`；置 `ctx.state["silent"]`；**不写 assistant 历史、不 mark_replied** |
| B3 | `loop/chat.py:676-703` `_stream_turn_reason` | `speech_by_tool ? (spoken? COMPLETED : SILENT) : (text? COMPLETED : reasoning? MAX_TOKENS : SILENT)` | 写进 `ctx.state["turn_reason"]` / 会话事件日志 / 遥测 |
| B4 | `loop/chat.py:625-628` `stream_success` | speech 模式只看 `not stream_error_text` | 静默被记为**成功**（"哑火"不算故障） |
| B5 | `loop/turn_runner.py:72-97` `classify_step` | 只看 `kind/finish_reason/text/concluded_by`，**不看 speech** | `run_turn` 路径的结局：静默与空回复不可区分 |
| B6 | `delivery/output.py:78-79,86-88` | `generate_response` 返回 None / `outbound is None` | BlockingOutput **不产出任何消息** |
| B7 | `loop/pipeline.py:667-670` | `pre_send` 返回 False 或 `msg.skip` | 单条被拦（业务钩子可静默吞掉一条） |
| B8 | `delivery/pool.py:51-68,145-149` | `stream_queue_full_policy` / `should_cancel` | 已产出的句子可被 `drop_newest/drop_oldest` 或打断丢弃 |

### 2.3 该有却没有的判定（2 处）—— 用户症状的核心

| # | 位置 | 缺什么 | 直接后果 |
|---|---|---|---|
| **C1** | `loop/turn.py`（`run_turn`，全文 700 行） | **没有一行 `speech`**（`grep "speech\|flush" app/agent/loop/turn.py` → 0 命中）。docstring `turn.py:29` 自己写着"不负责：发言通道…（`stream_response` 专有语义，本函数不搬）" | 见下 D1/D2 |
| **C2** | `llm_proactive/src/manager.py:238,286-305`、`llm_schedule/src/scheduler.py:448,516-536` | `PromptRequest.config = self.module.config`（模块自己的 `ModuleConfig`），**不是 `policy_view(runtime)`** | 见下 D3 |

### 2.4 它们能否不一致？—— 三条已确认的不一致链

**D1（静默，最像用户症状）**：`run_turn` 非流式 + 发言通道
```
prompt_prep.py:247   _build_speech(policy_cfg) → outbox 存在
prompt_prep.py:251   all_specs += [send_message, finish]
assembler.py:484     user 尾贴「你输出的文本只是思考，不会发到聊天里」
turn.py:306-308      stream = policy_cfg.get("stream_output", False) → llm_output 模块默认 False → 走 _chat_once
turn.py:419-500      _chat_once 只回 text；模型没写正文 → text="" （send_message 的入稿留在 outbox）
turn.py:358-395      result.text=""；outbox 从未 take() → 气泡丢弃
```
→ 表现：**模型"想好了也调了工具"，群里一条都没有；日志里连"静默"都不会打**（静默日志在 `chat.py:561`，非流式路径根本没有这段）。
**可达性**：`llm_turn`（`plugin/base.py:560-630`）、`client.llm_chat_turn`（`client.py:197`）都走 `run_turn`；默认配置下 `stream=None` → 取 `stream_output=False` → **必然走这条**。

**D2（思考外泄）**：同一入口 + `stream=True`
```
turn.py:541-544   _stream_once 把每个句子交给 on_sentence
plugin/base.py:610-615  send=True 时 on_sentence = self.send_to → 思考被当消息发出去
```
→ 表现：**群里出现模型的内心独白**；同时 outbox 里的正式气泡仍然丢弃。

**D3（主动/定时口径反过来）**：`PromptRequest.config = 模块自己的配置`
```
presets.resolve(module_config) → module_config 无 prompt_preset 键 → None → builtin_sections.py:148 → 行为段返回 []
speech_via_tool(module_config) → 无该键 → False → builtin_sections.py:158-172 走"文本=发言"措辞（但行为段根本没渲染）
manager.py:322 / :391-396 → 直接 bot.send_group_msg(strip_all_tags(resp.text))
```
→ 表现：主动消息/定时任务的 prompt 里**没有行为规则段、没有发言协议**，出口是"文本直接发"，与同一个账号 chat 回合的"文本=思考"**完全相反**。
→ **附带 bug**：主动消息的 prompt **仍然教模型写 `[reply]`/`[@QQ]`**（`prompt.py:47-51` → `builtin_sections.py:216-222`，因为 `has_quote` 默认 True），但该出口**不解析指令**（`manager.py:391`）→ 用户会看到字面的 `[reply]`。

**结构性风险（当前两侧恰好对齐，未触发）**：A6 直读 agent 配置、A7 声明优先；但 A6 只在输出槽为空时才参与选实现，而 `llm_output` 已接管槽位（`llm_output/main.py:72-73`），所以产品路径上 A6 实际是兜底代码。风险落在"第三方模块接管 `output` 槽、但 `declare_output` 与 `respond()` 实现不一致"这一种情形上——`llm_output/main.py:117-119` 的注释自己也承认"必须与 adapter 真正选中的实现一致——否则框架按 speech 处理，输出层却装 BlockingOutput（静默死局）"。

---

## 3. 输出三形态分叉全景

### 3.1 从 `runtime.output` 到"发出去"

| 步骤 | text（Blocking） | stream | speech |
|---|---|---|---|
| 策略选择 | `select_default_output`：`capability.streams==False` → `BlockingOutput`（`output.py:253`）；模块接管时 `llm_output/service.py:75-76` 按 `mode=="text"` | `StreamOutput`（`output.py:106`） | 同 stream：`mode in ("stream","speech")` → `StreamOutput`（`service.py:78-79`），因为 flush 需要轮次边界 |
| 生成 | `generate_response`（`generate.py:29`）→ `run_turn`（`turn.py:141`） | `chat.stream_response`（`chat.py:213`） | `chat.stream_response` |
| 模型文本去向 | 直接作为回复正文 → `build_outbound_builder().build(text)`（`output.py:85`）解析 `[reply]`/`[@QQ]` | `STEP_TEXT` → 逐句 `yield`（`chat.py:429-437`） | `STEP_TEXT` → `continue`，**只累积不发送**（`chat.py:431-433`） |
| 真正产出消息 | `ctx.response_messages`（`output.py:89`） | 每句一个 `Message`（`as_message`，`output.py:213-228`；speech 的 `Message` 不再过指令解析，`:220-224`） | 轮边界 `_flush_speech`（`chat.py:480`、`:556` → `assistant_output.py:100-127`） |
| 写历史 | `run_turn._write_history`（`turn.py:672-700`），`record_history=True` 时 | 先写后发：`_write_partial_history`（`chat.py:366-383`）→ `upsert_streaming_assistant` → 收尾定稿（`chat.py:600-605`） | `_flush_speech` 内**先写历史再 yield**（`assistant_output.py:126-127`，顺序是刻意的，见 `:101-107`） |
| 拦截/改写 | `post_response` 钩子（`output.py:91`）→ pipeline `pre_send` | pipeline `pre_send`（`pipeline.py:667`） | pipeline `pre_send` |
| 发送 | `pipeline._send` → `bot.send_group_msg/send_private_msg`（`pipeline.py:752-757`） | 同 | 同 |
| 回填 message_id | `_mark_sent_message_id`（`pipeline.py:762-783`） | 同 | 同（历史已写好，句柄落在气泡上） |
| 收尾 | `post_response` 后无 `post_stream` | `post_stream`（`pipeline.py:677-681`） | 同 + `_force_speak_round`（`chat.py:548-558`） |
| 静默判定 | 无（空文本 → `None` → 不发，`output.py:78-79`） | `chat.py:582-597`：空回复→静默，**不补兜底话术** | `chat.py:561-575`：`spoken==0` → 静默 |

### 3.2 speech 模式下"静默"与"故障"如何区分（日志口径）

| 情形 | 日志 | 代码 | 结局 |
|---|---|---|---|
| 没入稿、无错误 | `[LLM] 静默（看过但没说话）-> …(finished=False/True, reason=…)` | `chat.py:572` | `TurnReason.SILENT`，`ctx.state["silent"]=True`，**记成功** |
| 没入稿、但流式有错 | `[LLM] 流式失败且未开口 -> … error=…` | `chat.py:570` | `TurnReason.ERROR` |
| 被 @/私聊却没开口 → 补一轮 | 先 `_force_speak_round`（`:553`）；仍没开口再 `[LLM] 被直接点名但补一轮后仍未开口（模型可能没调用 send_message）` | `assistant_output.py:158-161` | 仍静默 |
| 剩 N 轮还没开口 | 注入 `SPEAK_REMINDER`（user 角色，`chat.py:484-489`） | `speech.py:80-84` | — |
| 工具不可用（模型不支持 tool calling） | `[LLM] 发言通道已开启但本轮工具不可用…已降级为「文本即回复」` | `chat.py:316-324` | 降级文本 |
| **非流式路径（run_turn）** | **什么都不会打** | — | **最隐蔽的一类静默** |

### 3.3 三条发送出口（与 `channel.py` docstring 的"唯一出口"不符）

| 出口 | 谁在用 | pre_send/post_send | `[reply]`/`[@QQ]` 解析 | msg_out 记账 | message_id 回填 | bot_sent 钩子 |
|---|---|---|---|---|---|---|
| `pipeline._drive → _send`（`pipeline.py:652-760`） | chat 三形态 | ✅ | ✅（`directives.py`，`output.py:85/228`） | ❌（走 `pipeline._send`，不经 channel） | ✅ | ✅ |
| `channel.send_item`（`channel.py:42`） | **只有 `plugin/base.py:644 send_to`** | ✅ | ✅（`:76-77`） | ✅（`:178-215`） | ❌ | ❌ |
| 裸 `bot.send_*` | 主动消息非流式（`manager.py:393-396`）、主动/定时流式（`stream.py:110-114`，池直接发） | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## 4. user 消息的组装

### 4.1 谁拼、拼成几种格式

| 环节 | 位置 | 产物 |
|---|---|---|
| 原始正文 | `domain/trigger.raw_user_text(event)` → `turn_input.py:136` | `segment_text` |
| 元信息渲染（唯一实现） | `llm_message_meta/src/service.py:65-166` `format_user_context`，通过 `pre_request` 钩子 order=20（`llm_message_meta/main.py:82`）；采集在 order=-100（`llm_message_meta/main.py:81`→`service.py:37-62`） | `ctx.user_text` |
| 单行格式（默认） | `service.py:95,152-163` → `session/model.build_current_turn_text` | `时间 昵称(QQ): 正文 (时间：…)` 单行 |
| 多行格式（legacy） | 同上，`single_line=False` | `(时间：…)/(当前群号: …)/发送者：/发送了：/提到了/引用了` 六行块 |
| 进 PromptRequest | `turn_input.py:145` → `prompt_prep.py:346` | `user_text` |
| 装配成消息 | `builtin_sections.py:350-368` | `content` 为 str，或 `[user数据块…, 正文, extra块…]` |
| 图片归位 | `assembler.py:561-598` | `content` 变数组：**数据块 → 正文 → 额外块 → 图片** |
| 发言协议 | `assembler.py:473-489` | 在**最后一条 user** 末尾追加（str 拼接或追加 text 块） |
| 轮次提醒 | `chat.py:486-489` / `turn.py:481` | 追加一条新的 `role=user`（**不合并**进上方那条） |

### 4.2 时间/群号/发送者/引用/@ 分别由谁注入

| 信息 | 注入者（框架 or 模块/插件） | file:line | 受哪个键控制 |
|---|---|---|---|
| 时间 | **模块**（`llm_message_meta`） | `service.py:107-111,145-146` | `include_time`、`prompt_time_position` |
| 私聊对方 QQ | 模块 | `service.py:114-117` | `include_private_qq` |
| 当前群号 | 模块 | `service.py:121-124` | 无（群聊恒注入） |
| 发送者昵称(QQ) | 模块 | `service.py:49-51,126-134` | `include_sender`、`meta_sender_style` |
| 提到了谁 | 模块 | `service.py:53-54,135-137`（反查 `fetch_nickname`，`:198-211`） | `include_mentioned`、`fetch_at_nickname` |
| 引用了哪句 | 模块 | `service.py:56-60,139-144,231-268` | `include_quote`、`include_quote_sender`、`fetch_quote_content` |
| `@` 段剥除 | **框架**（流水线） | `pipeline.py:488-493` | — |
| 历史行内的 `@`/占位符 | 框架（背景块渲染） | `context_blocks.py:115-129`（`_context_expand_flags`，`tool_source.py:28-42`） | `fetch_at_nickname`、`context_expand_enable` |
| 消息格式说明（告诉模型"冒号前是昵称"） | **框架**（L1 `message_meta` 段） | `builtin_sections.py:226-262` | `meta_instruction_mode`/`meta_sender_style` + `ctx.state["message_meta_injected"]` |

### 4.3 多来源合并顺序（一处都不能颠倒）

1. `llm_message_meta` `pre_request(-100)` 采集 → `(20)` 渲染覆盖 `ctx.user_text`；
2. `prompt_prep` 取 `turn.user_text`（=`ctx.user_text`，见 `turn_input.py:145`）；
3. `build_user` 组装：`user_blocks → 正文 → extra_user_content_parts`（`builtin_sections.py:360-368`）；
4. `place_images` 把图片追加在最后，并把 `user_blocks` 前置一次（`assembler.py:582-593`）；
5. `apply_speech_protocol` 追加发言协议（`assembler.py:479-489`）；
6. 轮次提醒作为**独立** user 消息追加，位置在请求尾部（`chat.py:486`）。

> 注意：`place_images` 在 `content` 为空（无图片）时**不执行**，此时 user 块由 `build_user` 负责——两条路径都拼 `_user_block_parts`，顺序一致但有重复实现（`builtin_sections.py:341` 与 `assembler.py:582`）。

---

## 5. 配置读取链

### 5.1 关系图

```
AgentConfig (app/agent/config.py:353)            ← 用户库 module_config("agent") + DEFAULT_LLM_CONFIG 兜底
   .get(k) :366  已存值 → DEFAULT_LLM_CONFIG[k](若非 None) → default        ★ 对"已归模块所有"的键仍会给默认值
        ▲
        │ base
   ┌────┴──────────────────────────────────────────────────────────────┐
   │ ConfigView(base, module_config, owned_keys)   delivery/output.py:256 │
   │   owned 键先问模块（值非 None 才算），否则回退 base                  │
   └────┬──────────────────────────────────────────────────────────────┘
        │ 9 个槽挂在 runtime 上（policy.py:8-21 有全表）
        │  output_options / assembly_options / meta_options / context_options /
        │  memory_options / knowledge_options / tools_options / proactive_options / schedule_options
        ▼
   ChainedConfig(runtime)  policy.py:70        ← policy_view(runtime) 的返回物
     .get(k):77  按固定顺序问每个槽（output 在最前），都不答 → 问 runtime.config
        ▲
        │ 两条取用口径
   policy_view(runtime)      跨模块读键（chat 路径用）      policy.py:55
   policy_config(runtime,kind) 只读一个模块（元信息/工具用）  policy.py:98
   policy_get(runtime,kind,key,default)                     policy.py:115
```

### 5.2 "同一个键在不同层看到不同值"的具体路径

| 键 | agent 层默认（`config.py`） | 模块层默认 | 代码实际认的词表 | 生效值 / 后果 |
|---|---|---|---|---|
| `emoji_react_level` | `1`（`config.py:43`） | `llm_assembly` = **`0`**（`llm_assembly/main.py:51`） | `0..3`（`rules.py:375-380`） | **模块默认 0 → `emoji_hint` 整段不注入**（`builtin_sections.py:189-193`），但 `emoji_rules` 仍写"可用标签见【可用表情】"（`rules.py:385`）→ 悬空指涉 |
| `participation` | `"medium"`（`config.py:42`） | `"normal"`（`llm_assembly/main.py:50`） | `low/high/其它`（`rules.py:270-283`） | 两值等价（都落 default 分支），词表有第三值 |
| `user_message_format` | `"single_line"`（`config.py:46`） | `"default"`（`llm_assembly/main.py:52`） | 只有 `"legacy"` 特殊，其它一律 single_line（`prompt.py:161-164`） | 等价；但渲染侧读 agent 配置（`llm_message_meta/src/service.py:82,95`）而说明段读 assembly 视图（`builtin_sections.py:245` → `req.config`）→ **改模块值只影响"手册"，不影响"实际格式"** |
| `prompt_time_position` | `"tail"`（`config.py:48`） | `"user_head"`（`llm_assembly/main.py:53`） | 只有 `"head"` 特殊（`prompt.py:167-170`） | 模块默认 `"user_head"` 语义是"头"，代码解成"尾" → 词表与语义不符（结果恰好等于 agent 默认） |
| `speech_via_tool` | `True`（`config.py:51`） | `True` + `mode="speech"`（`llm_output/main.py:77,97`） | 见 A7 | **被 `OutputOptionsView` 覆写成 `mode=="speech"`**，所以模块的 `mode` 才是生效值 |
| `stream_output` | `False`（`config.py:74`） | `False`（`llm_output/main.py:78`） | bool | 一致 |
| `speech_max_bubbles` / `_bubble_chars` / `_reminder_rounds` | `4` / `300` / `3`（`config.py:53-56`） | **`5` / `200` / `2`**（`llm_output/main.py:98-100`） | int | 模块 wins：气泡更多、单条更短、提醒更早 |
| `behavior_rules_override` | `""`（`config.py:40`） | `""`（`llm_assembly/main.py:49`） | str | 一致 |
| `history_background_enable` | **已删键**（`config_partition.py:373`），但 `config.py` 仍有默认值 | 无人提供 | bool | `builtin_sections.py:297` 永远拿到 True → "关掉背景"这个能力事实上不存在 |
| `outbound_directive_enable` | **已删键**（`config_partition.py:375`），`config.py:252` 仍留默认 | 无人提供 | bool | `directives.py:161` 永远 True；`prompt.py:233 has_quote` 永远 True |
| `prompt_preset` | `"chat"`（`:32`），属 **UI 面板键**（`config_partition.py:190`） | 框架模块都没有这个键 | 三选一（`presets.py:471`） | 只有走 `policy_view`（chat 路径）的请求能拿到 → 见 §2.4 D3 |
| `schedule_enable` | —— | `llm_schedule` 持有（`config_partition.py:344`） | bool | `prompt_prep.py:234` 用 `policy_get(runtime,"schedule",...)` 读对；主动/定时路径显式 `False` |

**最危险的机制（明确写下来）**：`AgentConfig.get`（`config.py:366-378`）在第 376-377 行**对任何键**都会回落到 `DEFAULT_LLM_CONFIG`，包括那些"已经归模块所有"的键（`module_owned_keys()` 只在 schema 校验/迁移时用，`config.py:337`）。
所以：**模块被禁用/未安装时，框架仍会用 agent 层的旧默认值继续跑**（例如 `speech_via_tool=True`），但模块层如果再改过值，两边就永久不同步。这是"同一键不同层"最容易踩的一处。

### 5.3 已知踩坑（都带代码位置）

1. `ChainedConfig.get` 用 `value is not None` 判定"这一层答了"（`policy.py:79-82`）→ **无法表达"显式设为 None"**，模块想清空某个键只能写空串/False。
2. `ConfigView.get` 同样把 `None` 当"没给"（`output.py:272-278`）→ `speech_via_tool=None` 会透明穿透到 agent 层。
3. `_policy_views` 的顺序是硬编码的（`policy.py:36-46`），**output 在最前**——所以 `speech_via_tool` 永远由 `llm_output` 的视图回答；顺序改变即改变语义。
4. 5.2 表里 `user_message_format` / `prompt_time_position` 的**写侧与读侧不同层**：写侧在 `llm_assembly`（`llm_assembly/main.py:52-53`），读侧一半读 `runtime.config`（`llm_message_meta/src/service.py:82`）、一半读 `assembly_options`（`builtin_sections.py:245` ⇒ `req.config` 经 `AssemblyAdapter.view_for`，`llm_assembly/service.py:41-47`）→ 用户改模块值时，"消息长什么样"和"手册怎么说"会分家。
5. `AssemblyAdapter.build` 会**临时替换** `req.config`（`llm_assembly/service.py:44-52`），`finally` 里还原——所以 `apply_speech_protocol` 用的是被包的视图（正确），但 `post_assemble` / `dump` 用的是还原后的对象（`assembler.py:724,750`，走 `runtime.assembly_options`），两条读法不同源。

---

## 6. 已存在的收敛设施：够不够承载 2–3 个核心插件

### 6.1 三套设施各自的能力

| 设施 | 位置 | 粒度 | 排他？ | 已能承载什么 | 缺什么 |
|---|---|---|---|---|---|
| **capabilities 注册表** | `capabilities.py:94-193` | 一份"输出能力"声明 + 任意"工具能力" | 输出**排他**（一个 `declare_output`） | 谁声明谁决定 `stream/blocking/speech`；工具能力可按名字查 | 没有"通道"以外的维度（没有 `behavior`/`assembly` 能力位）；`from_config` 与声明是两套读法（A6/A7），缺一致性强校验 |
| **sections 注册表** | `sections.py:109-268` | 任意多段，`(scope,name)` 去重、layer+order 排序 | 只有 `complete=True` 互斥（>1 报错） | 插件加一段提示词不用接管装配器；`layer="system_prompt"` 可进 L1 | ①**按 runtime 实例隔离**，而主动/定时路径传的是 module 对象 → 注册的段看不见（§6.3）②没有"段内容的热取值"（builder 只拿 `req`，拿不到模块运行时服务）③空段不留痕 |
| **context_provider / tool_source** | `loop/providers.py:103-197` | 多贡献者，`order` 排序、`role=system/user` 分流 | 不排他（可组合） | 记忆/指代/OneBot/MCP 都是这么接的；`user` 角色解决了"资料不该占指令位" | ①同样依赖 `runtime` 对象正确（§6.3）②只覆盖 L3 与工具，**不覆盖 L1/L2/L5 与"发送/静默"**③单 provider 失败只 debug 留痕（`:164-168`），排障靠翻 debug.log |

另有**排他槽**（`assembly` / `output` / `provider_invoker`）：`llm_assembly` 接 `assembly`（`llm_assembly/main.py:46-47,63-66`）、`llm_output` 接 `output`（`llm_output/main.py:72-73,111-116`）。**两个核心插件都已这样接好，这是"2–3 个插件合并"的正解。**

### 6.2 结论：能承载，但有一处硬伤

- **能**：行为文案（`llm_assembly`）+ 输出形态（`llm_output`）+ 记忆/指代/工具（`context_provider`/`tool_source`）三条线已经各就各位，互不抢占；chat 路径上实测装配结果一致（日志侧 L1 分层摘要能逐段归因，见 03-logs §0.4）。
- **不能**（硬伤）：**"runtime 对象"是这套收敛的唯一锚点，而主动/定时路径传的是 `BaseModule` 实例**。所以同一个"核心插件"在 chat 路径生效、在主动/定时路径全部消失——这不是插件写得不对，是框架侧的调用点欠一个 `runtime` 解析（`self.ctx.services.runtime(self.bot_id)` 就在手边，`llm_proactive/main.py` 的 `_runtime()` 已经这么写了，只是 manager/scheduler 没用）。

### 6.3 证据：`build_for(self.module, …)` 导致的能力塌陷

探测（只读，一行 `hasattr`）：

```
venv\Scripts\python.exe -c "... from app.plugin.base import BaseModule as B; print(
  hasattr(B,'provider_chain'), hasattr(B,'memory_service'), hasattr(B,'llm_hooks'),
  hasattr(B,'assembler'), hasattr(B,'prompt_sections'))"
→ provider_chain False memory_service False llm_hooks False assembler False prompt_sections False
```

`BaseModule.__init__`（`plugin/base.py:352-363`）只设 `ctx/config/authority/module_name/bot_id/_module_hooks/_commands/permission_granted`；loader/registry 里没有任何 `setattr(instance, ...)` 补这些属性。于是主动/定时路径逐项失效：

| 能力 | 依赖的属性 | 在 `AgentRuntime` 上 | 在 `BaseModule` 上 | 主动/定时路径的结果 | 代码 |
|---|---|---|---|---|---|
| 插件装配器（`llm_assembly`） | `runtime.assembler` | `runtime.py:151` | ❌ | `build_for` 回退 `PromptAssembler()`（且**不打 warning**，因为 `assembler is None`） | `assembler.py:647-654` |
| 插件 section | `runtime.prompt_sections` | 动态 | ❌ → 在 module 上**新建**一个 | 每次调用得到独立注册表，插件注册的段全部看不见 | `sections.py:237-245` |
| `post_assemble` 钩子 | `runtime.llm_hooks` | `runtime.py:141` | ❌ | `run_stage` 拿不到 registry → **钩子一个都不跑** | `hooks.py:168-169` |
| prompt dump | `runtime.assembly_options` | 由模块设 | ❌ → 回退 `runtime.config` | `prompt_dump_enable` 读模块配置（无此键）→ dump 永不落盘 | `dump.py:130` / `assembler.py:724` |
| token 预算 | `policy_cfg` 同上 | — | ❌ | `context_token_budget` 读不到 → 主动/定时**无输入上限** | `assembler.py:724-733` |
| 记忆块 / 工具源 | `runtime.memory_service` / `context_providers` / `tool_sources` | 动态 / `runtime.py:165-166` | ❌ | 主动/定时 prompt **没有记忆、没有 context/tool provider 工具** | `inputs.py:62` / `loop/providers.py:152,184` |
| provider 链 | `runtime.provider_chain()` | `runtime.py:272` | ❌ | 走 `else` 分支：`chain=[dict(module.raw_config)]`（**无 api_key/provider/model**） | `manager.py:357-360`、`scheduler.py:582-624` |
| `policy_view` | 9 个 `*_options` 槽（`runtime.py:126-133` 初始化 8 个，`output_options` 由 `llm_output` 动态挂，`runtime.py` 里**没有**它的初值） | 同上 | ❌ → 回退自己的 config | `stream_sentence_max_length` 等读模块配置 | `stream.py:99-101` |

> **待实测**：上表最后一行（provider 链）如果成立，主动消息/定时任务会在 provider 层直接失败（`openai_compat.py:242` 无 key 即报错）→ "从来不主动开口"。日志里我只找到测试账号 `bot_act/acc_net` 的"启动恢复计时器"（22 条），**没有真实账号的主动发言记录**，所以只能标为**高疑点、未证实**。

---

## 7. 不确定项 / 未核实（写清楚，别当结论用）

| # | 事项 | 现状 | 建议怎么验 |
|---|---|---|---|
| U1 | D1（`run_turn` 静默）是否在真实账号上发生过 | 纯代码推导；日志里 `静默（看过但没说话）` 只来自 `chat.py:572`，**非流式路径不打这条日志**，所以"没记录"不能证明"没发生" | 在 `turn.py:358` 附近加一行临时日志（或看 `turn_completed` 的 `reason`），或让某插件调 `llm_turn` 复现 |
| U2 | 主动/定时路径 provider 链是否真的失败 | `hasattr(BaseModule,'provider_chain')=False` 已证实；但 `chat_with_fallback` 对空 key 的具体行为（是否被 extra_headers 救）未逐行跟完 | 开一个真实账号的主动消息，看 `[Provider]`/`[LLM]` 错误日志 |
| U3 | 主动/定时 prompt 里 `[reply]` 是否真会漏给用户 | `manager.py:391` 只 `strip_all_tags`，不解析指令；`has_quote` 由 `outbound_directive_enable`（已删键，恒 True）决定 → 提示词会教 | 发一条带 `[reply]` 的主动消息看原文 |
| U4 | 日志侧"同一账号 47 秒内 L1 从 chat 变 roleplay"的成因 | 框架侧 `presets.resolve` 是纯函数、只依赖 `config.get("prompt_preset")`；我能想到的唯一逐轮变量是 `prompt_preset_service.get_override(preset.key)`（`builtin_sections.py:153-157`，全局静态覆盖，按 preset key 区分）——不足以解释"换了另一套方案" | 查该时段是否有 WebUI 写入 `/prompt-presets`；或核对 03-logs 里那两轮是否其实属于不同 bot/不同 config_service |
| U5 | `emoji_react_level=0` 是不是有意的产品默认 | 只能看到值，看不到意图；`llm_assembly/main.py:51` 与 `config.py:43` 冲突 | 问 Lead / 看 `llm_assembly` 的提交说明 |
| U6 | `safety_recap`（L1 order=65）在 L1 合并里的实际位置 | 由 `sort_key` 决定，排在 `message_meta(60)` 之后；但 `layers.SYSTEM_PROMPT_SECTIONS`（`:62`）也这么写，且有架构测试 | 以 `[Prompt]` 日志的 `contributors` 顺序为准 |

---

## 8. 本报告核实过的提交（都在 HEAD 祖先链上）

| 提交 | 内容 | 与本次盘点的关系 |
|---|---|---|
| `adc81f94` | `presets.resolve` 改哨兵写法 | **旧文档的根因已修**：`presets.py:486,509` 用 `config.get(key, _MISSING)`，不再依赖 `in config`（会 `TypeError`） |
| `45f99495` | 删除 `assembly_profile` 总闸 | `prompt.py:146-150` 留了考古注释 |
| `9f1483f9` | 删除旧单函数装配器 | `assembler.py:355-380` 只剩 deprecated 兼容路径 |
| `1c600b81` | 五层模型 | `layers.py` 新增、`sections.py:198-200` 改为按层排序、`assembler.py:274` 层内合并、`tests/architecture/test_prompt_layers.py` 锁定 |
| `4b332cfb` | L1 末尾底线再声明 | `builtin_sections.py:267-275,387`（order=65，**未在 assembler docstring 的表里**） |
| `a36cae06` | 装配调试日志 + 预算 | `layers.summarize_layers`（`:114`）、`turn_metrics.estimate_layer_tokens`（`layers.py:136`） |

---

## 9. 一页速查（给 Lead 直接引用）

- **五层通不通**：chat 路径**通**（L1 一条 system + L2 一条 + L3 N 条 + L4 + L5，有架构测试）；主动/定时路径**通但残**（没有行为段、没有发言协议、没有记忆/钩子/预算/dump）。
- **发言判定点共 17 处**：7 通道 + 8 结果 + 2 缺失；其中 5 处已收敛到 `speech_via_tool()`，1 处是仅"槽为空"时生效的兜底（`capabilities.from_config`），**2 处完全没有判定**（`run_turn`、主动/定时路径读错 config）。
- **能不一致，且已确认三条**：D1 非流式静默、D2 思考外泄、D3 主动/定时口径反转。
- **最省力的三个修法（供决策，不含实现）**：
  1. 把 `run_turn` 的 `stream` 判定从"看 `stream_output`"改成"看 `speech`"（`turn.py:306`），或直接在 `_run_inner` 里复刻 `chat.py` 的 flush 语义；
  2. 主动/定时路径的 `build_for` / `apply_post_assemble` / `collect_history_materials` 改传 **真 runtime**，模块配置用 `facade_for(runtime, kind, module.config, OWNED_KEYS)` 包一层（`policy.py:148` 已有这个函数，现成没用）；
  3. 把 `emoji_react_level` / `speech_max_*` 等重复默认值收敛到一处（`DEFAULT_LLM_CONFIG` 与各模块 `default_config` 二选一），避免 5.2 表里的静默差异。
