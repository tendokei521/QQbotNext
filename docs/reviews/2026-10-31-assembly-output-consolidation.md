# 消息组装 / 输出插件的碎片化评估与收敛方案

> 2026-10-31 ｜ 只读评估，未改动任何产品代码。
> 快照：HEAD `43001a5d` + 工作区在改 WIP（`assembly/context_data.py` 新增、`assembler/builtin_sections/inputs/prompt_prep/providers` 有未提交改动，20:47–20:48 仍在被另一会话编辑）。
> 三份底账（已随本文归档，可独立复核）：
> [01-plugins.md](2026-10-31-assessment/01-plugins.md)（插件侧 494 行，109 键逐键溯源）、
> [02-framework.md](2026-10-31-assessment/02-framework.md)（框架侧 398 行，17 个判定点逐处举证）、
> [03-logs.md](2026-10-31-assessment/03-logs.md)（日志取证 443 行，真实账号证据 + 测试噪声甄别）。
> 本文是这三份的合并判断，**具体证据以那三份的 `file:line` 为准**。

---

## 0. 结论（一页）

**判断：机制层是好的，策略层崩了；问题不是"插件写错了"，而是"边界切错了"。**

现在的切法按**功能名**切插件（组装一个、元信息一个、输出一个、调试一个、主动一个、定时一个…），
但真实耦合是**一轮请求的生命周期**：谁决定这轮发什么 → 谁决定这轮怎么发 → 谁决定这轮为什么发。
按功能切，就把同一件事切成了 4 个插件、3 个配置源、2 套词表，于是：

| 用户症状 | 机制层的直接原因 | 证据 |
|---|---|---|
| **"不同方案的提示词组装不统一"** | 提示词组装横跨 3 个插件 + 4 个 `PromptRequest` 入口；主动/定时路径传错 config → **行为规则段整段消失**，且出口口径与 chat 相反 | 02 §0.1、§2.4 D3 |
| **"开关哪个有什么作用、什么效果都不明确"** | 109 个 schema 键里 **12 个不按预期工作**（3 僵尸 / 2 被旁路 / 4 词表失配 / 1 读错源 / 2 条件不可达）；另 2 个仅兼容；模块配置在 `ChainedConfig` 里被**静默短路** | 01 §1、§3；本文 §3.1 |
| **"send_message 回复就不回"** | 真实发生：模型产出 46/47 字符正文、0 次工具调用 → 正文按"文本=思考"丢弃 → 静默；补一轮机制也失败 | 03 §2.2 B1/B2，`logs/user.log:22463–22465`、`:22576–22578` |

**核心矛盾一句话**：**"这一轮怎么说话"被 17 个判定点分别回答**（7 个通道判定 + 8 个结果判定 + 2 处根本没有判定），
其中"是否发言"的唯一事实源 `mode` 甚至不在配置归属表里（`config_partition.py:224-249` 没有 `mode`）。

**建议**：不推倒重写机制，**重写边界**——把"组装/输出/触发"收成 **3 个核心插件**，并用一个
**TurnPlan（一轮请求的单一决策对象）** 取代 17 个判定点。P0 的 5 个修法可独立上线，不必等重构。

---

## 1. 现状全景（一张图）

```
                        ┌──────────── 配置源（同一个键可能有 4 个值）────────────┐
                        │ agent 旧配置 186 键（库里仍在，schema 只剩 57）         │
                        │ 9 个插件 bindings.config_json（5 个是 {}，从未保存过）   │
                        │ DEFAULT_LLM_CONFIG 硬编码默认 / 各插件 default_config   │
                        │ app/domain/pace.py 等代码内 fallback                    │
                        └────────────────────────────────────────────────────────┘
                                          │
   ┌──────────────┬───────────────────────┼────────────────────────┬─────────────────┐
   │ 被动 chat     │ 主动 proactive         │ 定时 schedule          │ 模块 llm_turn    │
   │ prompt_prep   │ manager.py:238,286     │ scheduler.py:448,516   │ turn.py:141      │
   │ config=       │ config=模块自己的       │ config=模块自己的        │ config=policy_   │
   │ policy_view   │ ModuleConfig           │ ModuleConfig           │ view             │
   └──────┬───────┴───────────┬───────────┴────────────┬───────────┴────────┬─────────┘
          │                   │                        │                    │
          ▼                   ▼                        ▼                    ▼
   ┌─────────────────────────────────────────┐   ┌──────────────────────────────┐
   │ 组装：llm_assembly(7键)                  │   │ 出口 A pipeline._send         │
   │      + llm_message_meta(14键)            │   │  （唯一带 pre_send/指令解析）  │
   │      + llm_enhance(1键, 调试)             │   ├──────────────────────────────┤
   │      + llm_proactive 的 5 个 *_prompt* 键 │   │ 出口 B channel.send_item      │
   │      + 框架 builtin_sections 14 段        │   │  （只有 plugin.send_to 用）   │
   └──────────────┬──────────────────────────┘   ├──────────────────────────────┤
                  │                               │ 出口 C 裸 bot.send_*          │
                  ▼                               │  （主动/定时，无钩子/无指令）  │
   ┌─────────────────────────────────────────┐   └──────────────────────────────┘
   │ 输出：llm_output(24键, mode 唯一事实源)   │
   │      + speech outbox / stream pool       │
   │      + capabilities 声明                  │
   └─────────────────────────────────────────┘
```

**规模事实**（全部来自 01/02）：

| 指标 | 数值 |
|---|---|
| `app/plugins/llm_*` 插件 | 9 个（`llm_knowledge` 代码已删但归属表仍占 3 键） |
| 插件 schema 键 | **109**（+归属表实际 111，文档写 107） |
| "开关不按预期工作" | **12 / 109**（另 2 个仅兼容迁移） |
| "是否发言"判定点 | **17 处**（7 通道 + 8 结果 + 2 缺失） |
| 装配入口 | **4 条**（`PromptRequest` 构造点 3：chat / proactive / schedule；外加模块 `llm_turn` 执行入口 1） |
| 发送出口 | **3 条**，只有 1 条完整 |
| 配置读取层 | 4 层（AgentConfig → ConfigView → ChainedConfig → 代码内 fallback） |
| 装配时间线 | 一天内 `[Prompt]` 打了 **5 种 system 前缀指纹**，同一人格下 behavior 段长 2522–2993 |
| 真实账号"不回" | 今日 **2 轮**、10-29 **2 轮**，**全部是发言通道回合**；`errors.log` 中真实账号 **0 条** |

> 日志污染警告（影响所有人对"率"的判断）：`静默` 命中 34 条里真实账号只有 2 条，`流式空回复` 48 条里真实 0 条，
> `insufficient credits` 66 次全部是夹具。跨账号统计必须先按 `[123]` 过滤（03 §1）。

---

## 2. 为什么"分散"就是根因（结构性判断）

### 2.1 三份名单互不一致，键的归属没有唯一来源

| 名单 | 位置 | 问题 |
|---|---|---|
| 归属表 `MODULE_KEYS` | `app/agent/config_partition.py:222-358` | 9 个模块名（含已删除的 `llm_knowledge`）；**缺 `mode`** |
| 插件自述 `OWNED_KEYS` | 各 `main.py` | 8 个插件有，`llm_enhance` 没有 |
| 表单 `config_schema` | 9 个 `config_schema.py` / `schemas.py` | 109 键，含 `mode`，不含 `speech_via_tool` |

由此产生 6 处漂移（01 §3）：`mode` 无归宿、`speech_via_tool` 用户改不到、
`debug_prompt` 被判"已删除"但插件在用（迁移会静默丢值）、`llm_knowledge` 3 键无插件持有（知识库工具**永久不可用**）、
`onebot_tools_*` 5 键改在别的页面、键数口径 107/108/109/111 四种说法。

### 2.2 迁移兼容层变成了永久层，且会静默吃掉新配置

设计意图是"模块配置优先、agent 兜底"（`delivery/output.py:256-278`），
但 `ChainedConfig`（`policy.py:70-85`）是"按顺序问，第一个非 None 就赢"，
而每个 `ConfigView` 对**非自己所有**的键会回退到 agent 配置 —— 于是**排在最前面的视图会把所有键都答掉**。

实测复现（本地最小脚本，`ConfigView` + `ChainedConfig` 真实类）：

```
views 顺序 = [ConfigView(output), ConfigView(assembly)]
ChainedConfig.get('user_message_format') = 'legacy'     ← agent 旧值
        assembly_options 里用户真实保存的值 = 'single_line'  ← 永远读不到
ChainedConfig.get('participation')      = 'medium'     ← 同上
ChainedConfig.get('emoji_react_level')  = 1            ← 同上
```

**后果**：只要 `llm_output` 装着（当前就是），**`llm_assembly` 保存的任何配置都不生效**，
而 UI 会显示"保存成功"。这是"开关改了没反应"最直接的机制。

### 2.3 声明是装饰性的

`llm_output` / `llm_assembly` 声明了 `provides=("output"/"assembly",)` + `supersedes`，
但 FeatureRegistry 的接管要求 owner 自身实现 `respond()` / `build()`（`plugin/features/controllers.py:221,261`），
两个 `Module` 都没有 → **租约被拒，只打一条 warning**；真正接管发生在 `on_load` 里手工赋值（`main.py:105-132`、`:59-66`）。
所以 WebUI 上"功能接管"的状态与事实不符，卸载路径靠各模块自己的 `on_unload` 兜底。

同类装饰：`on_load` 里 `if runtime is None: return` 不留痕（9 个插件全有）、`on_load` 抛异常只 warning 而模块仍显示"已加载/已启用"（`plugin/loader.py:357-361`）。

### 2.4 三个默认值体系并存

同一个键在四处有不同默认值：`DEFAULT_LLM_CONFIG`、插件 `default_config`、`config_schema` 的 `default`、
代码内 fallback（如 `app/domain/pace.py`）。已实测的静默差异（02 §5.2）：

- `emoji_react_level`：agent `1` vs 模块 `0` → **默认整段【可用表情】不注入**，但行为段仍写"可用标签见【可用表情】"（悬空指涉，与日志里 `emoji_hint` 191/211 跳动吻合）；
- `speech_max_bubbles/chars/reminder`：4/300/3 → 5/200/2；
- `prompt_time_position`：模块默认 `user_head` 语义是"头"，代码只认 `head`，其余一律当"尾"；
- `onebot_tools_enable`：**False → True**（安全相关的方向变化，迁移时被悄悄改掉）。

### 2.5 配置面已经和 UI 脱节

库里的 agent 配置 **186 个键**，而 `config_schema` 只剩 **57 键**；`config_partition.stale_keys()` 在快照时刻返回
**147 个键**（布局表还认、schema 已删），其中 **127 个仍然存在于账号 123 的 agent 配置里**；
反过来，那 186 键里有 **131 个已不在 schema 中**——用户改不到，代码仍在读。
其中 `assembly_profile`、`behavior_rules_enable` 已经**全仓 0 读取**（`grep` 无命中），
`speech_fallback_enable` 也是 0 读取 —— 三个"幽灵开关"仍在库里（当前值 `assembly_profile="legacy"`、`behavior_rules_enable=true`、`speech_fallback_enable=false`）。

> 口径说明：这些数字是 2026-10-31 20:5x 快照时算出来的，工作区当时有另一会话在改 `assembly/`，
> 复算时可能有 ±1 的漂移；结论（"配置面与 UI 已脱节"）不随 ±1 变化。

> 另外：模块配置现在存在 `bindings.config_json`（`infrastructure/config/binding_store.py:47` 注明旧的
> `module_config` 表"只在迁移期读，运行时不再写"，该表确实为空）。账号 `123` 的实际存档只有 4 个插件有值：
> `llm_output`(818B) / `llm_proactive`(591B) / `llm_tools_extra`(290B) / `llm_enhance`(22B)；
> **`llm_assembly` / `llm_message_meta` / `llm_context` / `llm_memory` / `llm_schedule` 全是 `{}`** ——
> 也就是"组装/元信息"这两个直接决定提示词长什么样的插件，**从来没有被保存过任何配置**，
> 全部跑在默认值上（再被 §2.2 的短路吃掉一部分）。"改开关没反应"没被更早发现，这是原因之一。

---

## 3. 三个症状的根因链

### 3.1 "组装不统一"——不是三套方案不统一，是四条路径不统一

三套提示词方案（chat / roleplay / assistant）本身是产品设计，OK。
不统一的是**同一次装配的输入口径**：

| 入口 | `PromptRequest.config` | 拿得到 `prompt_preset`？ | 结果 |
|---|---|---|---|
| 被动 chat（`prompt_prep.py:246,340`） | `policy_view(runtime)` | ✅ | 五层完整（有行为段） |
| 主动消息（`llm_proactive/src/manager.py:238,286`） | `self.module.config` | ❌ | **行为规则段消失**，措辞回落"文本=发言" |
| 定时任务（`llm_schedule/src/scheduler.py:448,516`） | `self.module.config` | ❌ | 同上 |
| 模块 `llm_turn`（`turn.py:141`） | `policy_view` | ✅ | 但**没有发言通道判定**（见 3.4） |

**总病根**：`build_for(self.module, req)` / `apply_post_assemble(self.module, …)` 传的是 **`BaseModule` 实例，不是 `AgentRuntime`**。
已探测 `BaseModule` 没有 `assembler / prompt_sections / llm_hooks / memory_service / provider_chain`（02 §6.3），于是主动/定时路径上
插件装配器、注册的 section、`post_assemble` 钩子、prompt dump、token 预算、记忆块、工具源**全部静默失效**。
而 `self.ctx.services.runtime(self.bot_id)` 就在手边（`llm_proactive/main.py` 的 `_runtime()` 已经这么写了），只是 manager/scheduler 没用。

同一条链还有一个可见 bug：主动消息的 prompt 仍教模型写 `[reply]`/`[@QQ]`（`prompt.py:47-51` 经 `builtin_sections.py:216-222`），
但它的出口 `manager.py:391` 只 `strip_all_tags`、不解析指令 → 用户会看到字面的 `[reply]`。

### 3.2 另一半：9 个插件里当前只开了 3 个

账号 `123` 的 `bindings`（启用位）：`llm_output=1`、`llm_tools_extra=1`、`llm_enhance=1`，
其余 **6 个全关**（`llm_assembly` / `llm_message_meta` / `llm_context` / `llm_memory` / `llm_proactive` / `llm_schedule`）。

这带来一个"不报错但语义全错"的组合：

- `llm_message_meta` 关着 → 本轮 user 消息**没有发送者、时间、引用、@ 渲染**，`include_*` 那 14 个键全部是死的；
- 更隐蔽的是：消息格式图例（"`昵称(QQ): 正文` 里冒号前是什么"）在 `builtin_sections.py:237` 被
  `ctx.state["message_meta_injected"]` 门控，而这个标志**全仓只有一个写入点** ——
  `app/plugins/llm_message_meta/src/service.py:166`。插件关着 → 标志永不置位 → **图例永不注入**；
- 于是模型看到的是一条条裸文本，却连"这是谁说的"手册都没有；而 UI 上这些开关还在（只是那个插件页是灰的）。
- `user_message_format`（归 `llm_assembly`，也关着）此时只剩"选哪份图例"的作用，**改它不会有任何可观察变化**。

这就是"开关多、效果不明"的完整闭环：**能改的键有一半没有读取者，有读取者的那个插件没开，开了的那个又被短路。**

### 3.3 "开关不明确"——12 个失效键 + 3 个口径分裂

失效键清单（01 §1，均带读取点）：

| 类型 | 键 |
|---|---|
| 🔴 全仓 0 读取 | `stream_send_by_sentence`、`stream_keep_order`、`meta_mask_nickname`（本插件）、`speech_fallback_enable` |
| 🚫 被旁路（开关在这里、渲染读那里） | `user_message_format`、`prompt_time_position`（写在 `llm_assembly`，真实渲染读 `runtime.config`） |
| 🔤 词表失配 | `meta_sender_style`、`meta_sent_style`、`meta_instruction_mode`、`stream_send_curve.linear` |
| 📕 读错来源 | `onebot_tools_max_result`（`executor.py:148` 读 `runtime.config`） |
| ⛔ 条件不可达 | `stream_proactive_enabled`、`stream_scheduled_enabled`（与"已迁走的 agent `stream_output`"相与 → 恒 False） |

口径分裂最典型的一处：**时间行由 4 个地方分别表达** —— `prompt_time_position`（位置，写侧在 assembly）、
`include_time`（有无，写侧在 message_meta）、`meta_sender_style`（历史行风格）、以及主聊天历史里**硬编码 True**（`turn_metrics.py:383-391`）；
而位置判定只认 `head`，所以"头部/尾部/不注入"三个选项实际只有"尾部"生效。

### 3.4 "send_message 回复就不回"——真实日志 + 三段代码

**真实证据**（`logs/user.log`，账号 `[123]`、群 466052056、有人 @ 提问）：

```
20:39:40.156  [接收<-] [at:3437542570] 为什么不能问
20:39:40.192  WARNING  人格卡里有「输出格式」条款，会与发言通道（文本=思考）冲突：
                        ['- 输出：以第一人称"我"进行回答…输出纯文本段落即可…']
20:39:40.204  [Prompt] L1: behavior 2887+schedule 250+proactive 140+safety_recap 145 |
              L2 persona(1665) | L3 emoji_hint(191) | L5 user(6)
20:39:53.804  回复 46 字符 | 非流式        ← 模型写了正文
20:39:53.804  被直接点名但补一轮后仍未开口（模型可能没调用 send_message）
20:39:53.804  静默（看过但没说话）-> … (finished=False)     ← 群里什么都没有
20:40:11.067  [接收<-] … 怎么不回我        ← 用户当场察觉
20:40:19.025  回复 47 字符 → 再次静默
```

关键点，逐条说清（避免误判）：

1. **提示词并没有少贴协议**。那一轮 user 消息里确实带了【发言协议】（`logs/user.log:22459`：
   `"为什么不能问\n\n【发言协议】你输出的文本只是思考，不会发到聊天里；只有调用 send_message 的内容会发出去…"`）。
   所以这不是"忘了贴协议"的低级 bug。
2. **模型确实有能力调工具**。同会话 20:39:03 它刚调过 `set_msg_emoji_like` 并发了两条消息。
3. **真正的原因是两条相反指令打架**：人格卡写"输出：以第一人称…**输出纯文本段落即可**、回复字数必须在 50 字以内"，
   行为段/发言协议写"文本只是思考、必须调 send_message"。system 层里模型跟着**更近、更具体**的人格条款走。
   运行时的告警（`prompt_prep.py:259-262`）已经把这段文本指出来了，但**只告警、不处理**。
4. **补一轮也没用**：`_force_speak_round` 用非流式再要一次（`assistant_output.py:130-161`），
   但人格条款还在，模型照样只写正文 → 第二次静默。

对照组（同会话、切发言通道之前，行为段写"输出的正文会直接发到 QQ"）：20:24:19、20:24:45、20:38:54 三轮全部正常发言。
→ **所以"不回"与网络/账号/模型无关，是"提示词里的发言方式"和"输出层的发言方式"取了两套语义**。

另有一条更隐蔽的同类：**模块走 `llm_turn` / `client.llm_chat_turn` 且非流式时**（默认 `stream_output=False` 即此路径），
`turn.py` 全文没有一行 `speech`（grep 0 命中）：提示词说"文本不会发出去"、`send_message` 工具也挂上了，
但 outbox **从不 flush** → 气泡丢弃、`result.text=""` → **整轮静默，而且连"静默"日志都不打**（`chat.py:561` 那段不在这个路径上）。
这是纯代码推导（02 §2.3 C1/D1），**未在真实日志里观察到**，因为这条路径不打印任何东西。

---

## 4. 缺陷清单（按修复优先级）

| # | 级别 | 缺陷 | 位置 | 修法 |
|---|---|---|---|---|
| 1 | **P0** | `ChainedConfig` 被首个视图短路，`llm_assembly` 配置永不生效 | `policy.py:70-85` + `ConfigView.get` | 视图对非 owned 键返回哨兵（不是 base），链只在 owned 范围内兜底 |
| 2 | **P0** | 发言通道 + 人格卡"输出纯文本"条款冲突 → 整轮静默 | `persona` 预设 vs `speech.SPEECH_PROTOCOL` | 开 speech 时**自动剥离/改写**人格卡里的输出格式条款（不是只告警）；或把"输出方式"从人格卡里彻底移走 |
| 3 | **P0** | 主动/定时路径传 `BaseModule` 当 runtime，且 config 不是 `policy_view` | `manager.py:238,286`、`scheduler.py:448,516`、`build_for`/`apply_post_assemble` | 传真 runtime；模块配置用现成的 `facade_for(runtime, kind, module.config, OWNED_KEYS)`（`policy.py:148`，已写好没用） |
| 4 | **P0** | `turn.py` 无发言通道判定 → 静默无日志 / `stream=True` 时思考外泄 | `turn.py:306`、`:419-500` | `stream` 判定改看 speech；补 flush 或对 speech 明确 fail-fast |
| 5 | **P0** | `mode` 不在归属表；`debug_prompt` 被判已删除；`llm_knowledge` 3 键无主 | `config_partition.py:224-249,404-409,306-310` | 补 `mode`、把 `debug_prompt` 移出 DELETED、知识库键要么建插件要么并回本体 |
| 6 | P1 | 12 个失效开关（含 4 个僵尸） | 01 §1 | 删或补实现；词表按代码真实词表重写 |
| 7 | P1 | 三条发送出口，两条无钩子/不解析指令 | `pipeline.py:652-760`、`channel.py:42`、`manager.py:391`、`stream.py:110` | 收敛为 1 条（主动/定时也走 pipeline 的发送路径） |
| 8 | P1 | 近 150 个 schema 外残留键 + 3 个幽灵开关仍在库 | `config_partition.stale_keys()` | 一次性迁移清理；`unknown/stale` 加 CI 闸门 |
| 9 | P1 | 三套默认值 | `config.py` vs 各 `default_config` vs `pace.py` | 默认值只留模块侧一处，agent 侧删 |
| 10 | P2 | 声明装饰化（`provides/supersedes`） | `controllers.py:171-192` | 要么让 `Module` 实现契约，要么删掉声明，别让 UI 说谎 |

---

## 5. 目标架构：3 个核心插件 + 1 个 TurnPlan

**原则**：按**一轮请求的生命周期阶段**切，不按功能名切。每个阶段只有一个所有者，阶段之间只通过一个不可变对象通信。

```
  ①  谁决定"这一轮要不要开始"     →  llm_trigger     （被动 / 主动 / 定时，统一入口）
  ②  谁决定"发给模型的 messages"    →  llm_prompt      （段表 / 方案 / 人格 / 本轮消息 / 资料块 / dump）
  ③  谁决定"模型的话怎么发出去"     →  llm_delivery    （形态 / 发言协议 / 分条 / 指令 / 静默 / 唯一出口）
                    │
                    └── 三者共享一个 TurnPlan（每轮构造一次，只读）
```

### 5.1 `TurnPlan`：把 17 个判定点压成 1 个

```python
@dataclass(frozen=True)
class TurnPlan:
    source: str                      # chat | proactive | schedule
    runtime: AgentRuntime            # ★ 永远是真 runtime，不是 BaseModule

    # ② 组装侧（llm_prompt 产出）
    prompt_preset: str               # chat | roleplay | assistant（显式，可归因）
    sections: tuple[SectionSpec, ...]  # 本轮要哪几段、各自文案来源
    user_format: str                 # single_line | legacy（唯一词表）
    time_position: str               # head | tail | off（唯一开关）
    meta_fields: frozenset[str]      # sender/time/quote/at/...
    user_blocks: tuple[Block, ...]   # 记忆等 user 侧资料（WIP 已在此方向）

    # ③ 发送侧（llm_delivery 产出）
    speak_mode: str                  # text | stream | speech
    protocol_text: str | None        # 要贴进 user 尾的协议（None = 不贴）
    persona_conflicts: tuple[str, ...]  # 本轮必须从人格卡里剥掉的输出格式条款
    bubbles: SpeakLimits             # max_bubbles / max_chars / force_reply
    stream_pace: StreamPace          # 节奏（仅 stream/speech）
```

**硬性纪律**（写进架构测试）：

1. `speak_mode`、`protocol_text`、`persona_conflicts` **只能**由 `llm_delivery.plan()` 写；
2. `sections`、`user_format`、`time_position`、`meta_fields` **只能**由 `llm_prompt.plan()` 写；
3. 其它任何代码**不得**再读 `speech_via_tool` / `stream_output` / `user_message_format` / `prompt_time_position`（用 grep 闸门锁死）；
4. 四条入口（chat/proactive/schedule/llm_turn）**必须**共用 `build_plan(runtime, source)`；
5. 发送出口只有一条，`pre_send`/`post_send`/指令解析/`msg_out` 记账/`message_id` 回填全都在这一条上。

### 5.2 三个插件各吸收什么

| 新插件 | 吸收 | 保留的键（现状 → 目标） | 关键变化 |
|---|---|---|---|
| **`llm_prompt`**<br>消息组装与渲染 | `llm_assembly`(7) + `llm_message_meta`(14) + `llm_enhance`(1) + `llm_proactive` 的 5 个 `*_prompt*` + `llm_context.referent_prompt*` | **22 → 12**：方案 / 参与度 / 表情活跃度 / `user_format` / `time_position` / 元信息项多选 / 资料块开关 / dump+打印（两个调试开关合一） | 时间/格式/元信息**一对一**：一个"格式"枚举、一个"时间"三态、一个"元信息项"多选；渲染与说明**同源** |
| **`llm_delivery`**<br>发言与输出 | `llm_output`(24) + `speech` 协议生成 + `directives` + `stream_pace` + 5 个静默/补一轮判定 | **24 → 12**：`mode`、bubbles(3)、force_reply、节奏(4–6)、`speech.keep_persona_terms` | `mode` 成为唯一事实源并**进归属表**；`speech_via_tool`/`stream_output` 降级为一次性迁移键后删除；**接管发送出口** |
| **`llm_trigger`**<br>非被动触发 | `llm_proactive`(17) + `llm_schedule`(3) | **20 → 10**：主动(6–8)、定时(2–3) | 两条路径合并后共用 `build_plan(runtime, source)` 和唯一出口，D3 自动消失；顺手消掉 3 处插件间 import |

**保留不动**：`llm_memory`(23，唯一全部键都有正确读取点的插件)、`llm_context`(10)、`llm_tools_extra`(10，修 1 个读错源)。
它们已经挂在 `context_provider` / `tool_source` 上，接缝是对的——**它们是"能力"，不是"组装"**。

> 数量：9 个插件 → **3 个核心 + 3 个能力 = 6 个**；插件配置键 **109 → 77**（核心 46 → 34，能力 43 不动）；
> 装配入口 **4 → 1**；发送出口 **3 → 1**；发言判定点 **17 → 1（TurnPlan.speak_mode）**。

### 5.3 为什么不是"2 个"

2 个（`llm_prompt` + `llm_delivery`）能解决组装与输出的统一，但**解决不了路径分叉**：
主动/定时现在是独立的一套"自己拼 prompt、自己发消息"的实现，正是 D3 的来源。
要么合并成第 3 个，要么强制它们也走 `build_plan` + 唯一出口 —— 后者本质上就是同一个东西。**建议做第 3 个。**

---

## 6. 迁移路径（每一步独立可提交、可回滚）

**P0 修机制（不重构也值得做，约 5 个提交）**

1. `fix(policy): 视图只回答自己拥有的键，修 ChainedConfig 短路` → 让模块配置立刻生效（缺陷 1）。
2. `fix(speech): speech 模式自动剥离人格卡输出格式条款` → 直接止血"不回"（缺陷 2）。
3. `fix(proactive,schedule): 用真 runtime + facade_for 包模块配置`（缺陷 3）。
4. `fix(turn): 发言通道判定与 flush，或对 speech fail-fast`（缺陷 4）。
5. `chore(config): 补 mode 归属、移出 debug_prompt、给知识库键归宿`（缺陷 5）。

**P1 可观测（先让"哪轮用了什么"能回答）**

6. `feat(log): 每轮打一行 TurnPlan 摘要`（方案名 + speak_mode + 段表 + 出口结果 + 静默原因）。
   现状：`[Prompt]` 只打段长度和 `prefix` 指纹，**不打方案名、不打开关值**，日志无法归因（03 §0.4 / §3）。

**P2 合并（结构）**

7. 建 `llm_prompt`，把 `llm_assembly` + `llm_message_meta` + `llm_enhance` 的键与实现搬进去，旧插件置为 migration shim（读旧配置、写新配置、打 deprecated）。
8. 建 `llm_delivery`，接管发送出口；`llm_output` 变 shim。
9. 建 `llm_trigger`，合并 proactive + schedule，走同一 `build_plan`。
10. 删除 shim、删除近 150 个残留键 + 3 个幽灵键，加 CI 闸门（`unknown_keys`/`stale_keys`/`0 读取键` 必须为空）。

**P3 收尾**

11. 重生成配置页（3 个插件各一页，键数 ≤ 60），文档 `MODULE_DEV.md` / `config_partition` 注释同步。

---

## 7. 验收口径（可执行的契约，不是"看起来对"）

| # | 契约 | 怎么测 |
|---|---|---|
| 1 | **模块配置保存后读得到** | 存 `llm_assembly.user_message_format=single_line`，`policy_view.get()` 必须返回 `single_line`（覆盖当前短路回归） |
| 2 | **发言语义单源** | `mode=text` 的请求里**不含**"文本不会发出去"、不含 `send_message` 工具；`mode=speech` 必须同时有协议 + 工具（两个断言绑在一起） |
| 3 | **人格冲突自动处理** | 人格卡含"输出纯文本段落" + `mode=speech` → 最终 messages 里该条款**被剥离**，且 `TurnPlan.persona_conflicts` 非空 |
| 4 | **四条入口一致** | 同一账号同一配置，`chat` / `proactive` / `schedule` / `llm_turn` 四条路径产出的 `TurnPlan.prompt_preset` 相同、段名集合相同 |
| 5 | **静默必留痕** | 任何路径"没发出去"必须有一条日志，字段含 `source` / `speak_mode` / `spoken=0` / 原因 |
| 6 | **前缀稳定** | 同一会话连续两轮、配置未变 → system 前缀逐字节一致（现有 `test_prompt_prefix_stability` 扩展到主动/定时路径） |
| 7 | **零僵尸键** | 静态扫描：schema 键 = 归属表键 = 有真实读取点的键，差集为空 |

**可量化目标**

| 指标 | 现在 | 目标 |
|---|---|---|
| 失效/僵尸开关 | 12 + 2 仅兼容 + 3 幽灵 | **0** |
| 配置键总数 | agent schema 57 + 插件 109 = **166**，另有 **131** 个化石键仍被读 | 插件 **≤ 77**；化石 **0** |
| 装配入口 | 4 | **1** |
| 发送出口 | 3 | **1** |
| "是否发言"判定点 | 17 | **1** |
| 真实账号静默可归因率 | 0（日志不打方案/开关） | **100%** |

---

## 8. 需要你拍板的 5 个问题（重构前必须定）

1. **三套提示词方案是否保留差异化？** 若保留，"方案"必须显式进 `TurnPlan` 并打进日志；若差异其实只在行为段措辞，建议收敛成"一套骨架 + 三个行为文案包"。
2. **发言通道（`mode=speech`）要不要做默认？** 现在的证据是它对国产模型不稳（人格条款一冲就静默），并且需要"自动剥离人格输出条款"配套。建议先修 P0-2，再灰度。
3. **人格卡里的"输出格式/字数/称呼"条款归谁？** 建议：人格卡只描述"是谁、什么语气"，**输出方式全部移到 `llm_delivery` 的协议**，并在保存人格时校验/提示。
4. **`emoji_react_level` 默认 0 还是 1？** 现在模块 0 / agent 1 打架，且行为段会指涉一个不存在的【可用表情】段。
5. **知识库还留不留？** 代码已删、归属表还占 3 键、工具永久不可用（`llm_knowledge`）。要么建回来，要么把 3 键从归属表删掉。

---

## 9. 不确定项（不要把这几条当结论）

| # | 事项 | 现状 |
|---|---|---|
| U1 | `turn.py` 非流式静默是否真实发生过 | 纯代码推导；那条路径不打任何日志，所以"没记录"不等于"没发生" |
| U2 | 主动/定时路径的 provider 链是否真的失败 | `BaseModule` 无 `provider_chain` 已证实；空 key 后是否被兜底未逐行跟完（若成立 = "从来不主动开口"） |
| U3 | 主动消息带 `[reply]` 是否真漏给用户 | 提示词会教、出口不解析；未实测 |
| U4 | 18:51:17 → 18:52:04 方案文本变化（47 秒） | 日志无配置审计，无法归因；可能是代码提交 + 重启所致，不能当"运行时自己换方案" |
| U5 | 工作区 WIP 的最终形态 | 评估时（20:47–20:48）另一会话仍在改 `assembly/`，本文按"HEAD + 当时 WIP"判断；落地前需重跑一次段表快照 |

---

## 10. 一句话总结

现在的 9 个插件不是"功能太多"，而是**把一轮请求切成了 9 段，每段各拿一份配置、各自判断"要不要说话"**。
`mode` 这个唯一事实源藏在第 9 段里，甚至不在归属表上；而真正该管"怎么说话"的人格卡，恰恰没人管。
**收敛成 3 个插件 + 1 个 TurnPlan，不是重写功能，是把边界改回它本来就该在的地方。**
