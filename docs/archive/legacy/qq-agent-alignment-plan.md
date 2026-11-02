# 与 QQ-agent-main 对齐「消息组装」的实施方案

> 前置阅读：`docs/qq-agent-prompt-comparison.md`（提示词全文与差距清单）。
> 本文回答一个问题：**要把消息组装（提示词 + 轮次对话）真的对齐到那套开源实现，
> 本项目要动什么、按什么顺序动、怎么验收、哪些不该搬。**
>
> 写作时间：对齐方案的第一个版本，未落地任何代码。行号以写入时源码为准。

## 0. 核心判断

1. **提示词不是独立的**。QQ-agent 的 14 个段落里，至少 6 段直接描述运行时行为：
   「文本输出不会发出去，要发言必须调 send_message」「不回是正常选项」「普通对话默认 1 条」
   「已读信息里的旧消息不要求你回应」「最后 3 轮还一条没发就催你」「工具调用轮次只剩 N 轮」。
   这些句子在**没有对应能力**的运行时里是负资产：模型会调用不存在的工具、或者认为
   "自己不发言也行"而整轮哑火。
2. **必须先有能力层，再有表达层**。顺序反了，等于给模型写一份它做不到的岗位说明书。
3. **轴心是「发言通道」**。`send_message` + `finish` + 出稿箱（outbox）一次性解决四件事：
   能不能不说话、能不能只发 1 条、能不能边想边说、历史里记什么。其余段落都是围着它转的。
4. **对齐要可灰度和可回退**。建议引入「组装档位」`assembly_profile = legacy | qq_agent`，
   一套配置切换新旧两套组装，允许 A/B 与一键回退——否则一次改动无法归因。
5. **有三处不能照搬**（本项目更强或场景不同，见 §5）。

## 1. 对齐边界：1:1 / 适配 / 新建 / 不搬

| QQ-agent 机制 | 本项目现状 | 处置 |
|---|---|---|
| 一整条连续 system prompt（14 段） | `BLOCKS` 十条 system 或 `single` 折叠 | **适配**：新增 `behavior` 块，`single` 折叠实现"一整条" |
| 文本=思考，发言=工具调用 | 文本=发言（流式按句连发） | **新建**：`send_message` + outbox + 静默 |
| `finish`（看完不说话） | 无；空回复→兜底话术 | **新建** |
| 【已读信息】/【未读信息】 | 会话历史 + 群聊环境记录两块混合 | **适配**：以 group_log 为已读源，触发批为未读 |
| 4 档唤醒 + 拍一拍召唤 | 仅 `@ / 关键词`，触发即必答 | **适配**：移植档位到 `trigger.py` |
| 未触发消息标已读沉历史 | group_log 已记录**全部**群消息 | **已有**（省一半工作量） |
| 提示词锚点（前缀缓存） | 无；时间在 user 首行 | **新建**（P4） |
| 活跃期（activeTopic + 偏了就 finish） | 无 | **新建**（P4） |
| 收藏表情包库 + `send_sticker` | 只有 `set_msg_emoji_like`（贴表情）+ 表情词表 | **部分新建**：先写"贴表情"策略段，图库留 P4 |
| 群友印象记忆（memberImpression） | 事实型记忆（置信度/过期/去重） | **适配**：提示词格式化 + 可选印象类型 |
| 主人专属人设 | 有 `is_bot_owner`，无主人提示词 | **新建**（P5，可后置） |
| 安全规则"你没有本地工具" | 有一条 OneBot 工具（含群管理） | **改写**：本项目确实有本地/群管工具，改成权限边界话术 |
| 【系统提醒】剩 N 轮还未发言 | 无（只有工具结果的"回应要求"） | **新建** |
| 数据 store（消息+已读标记+表情） | group_log store（事件流，无已读标记） | **适配**：水位线代替已读标记 |
| —— 本项目独有，须保留 —— | 上下文未展开标记/按需展开、指代消解、工具结果"回应要求"、回复打断、上下文压缩、prompt_dump | **保留**：接进新段落，不要在对齐时丢掉 |

## 2. 分层修改方案

### 2.1 system prompt 层（表达层）

**目标形态**：一条 system，段落顺序与 QQ-agent 一致（除人格段），段落内容按本项目工具重写。

新增 `app/llm/persona_rules.py`：

```python
# 能力探测：段落里点名的工具必须真实存在于本轮
def detect_caps(available_tools: set[str] | None, config) -> dict:
    return {
        "speech_by_tool": bool(config.get("speech_via_tool", False)),
        "has_send": "send_message" in tools,
        "has_finish": "finish" in tools,
        "has_poke": "send_poke" in tools,
        "has_emoji_react": "set_msg_emoji_like" in tools,
        "has_search": "tavily_search" in tools,
        "has_expand": bool({"expand_recent","expand_message","expand_user"} & tools),
        "has_history": "get_chat_history" in tools,
        "has_memory": bool({n for n in tools if n.startswith("memory_")}),
        "has_schedule": "schedule_task" in tools,
        "vision": supports_image(modalities),
        "speech": config.get("speech_via_tool"),
    }

# 段落函数（每个都只读 caps/config，纯函数，可单测）
def security_rules(caps) -> str
def work_protocol(caps) -> str          # 全篇最依赖 caps：发言通道/分条/静默/工具语义
def anti_ai_flavor() -> str
def subjectivity() -> str
def speak_or_not(participation: str) -> str
def not_a_queue() -> str
def human_rhythm() -> str
def not_moderator() -> str
def quote_and_at(caps) -> str
def memory_rules(caps) -> str
def emoji_rules(caps, level: int) -> str
def qq_scene_rules(caps) -> str
def report_ban(caps) -> str
def render(runtime, available_tools, config) -> str   # 按固定顺序拼成一段
```

**段落 → 依赖的本地工具**（这是"提示词对齐本地 tool"的落地表）：

| 段落 | 段落里点名的能力 | 本项目工具名 | 现状 |
|---|---|---|---|
| 工作方式 | 发言 | `send_message`（新） | 新建 |
| 工作方式 | 沉默结束 | `finish`（新） | 新建 |
| 工作方式 | 翻聊天记录 | `get_chat_history` | 已有 |
| 工作方式/引用与点名 | 引用、@ | `send_message(reply_to_message_id, at_user_id)`（新） | 新建 |
| 工作方式/引用与点名 | 找活跃成员 | `get_group_member_list` + group_log 活跃度 | 部分（可加 `get_active_members`） |
| QQ 场景 | 看图 | `expand_image` / 触发消息内联图片 | 已有 |
| QQ 场景 | 看视频 | 无（本机未实现） | 段落里**不出现** |
| QQ 场景 | 展开合并转发 | `expand_message` | 已有 |
| QQ 场景 | 联网搜索/抓网页 | `tavily_search`（无 web_fetch 等价物，除非加） | 部分 |
| 表情 | 贴表情 | `set_msg_emoji_like` | 已有（无"发表情包"） |
| 拍一拍 | 戳一戳 | `send_poke` | 已有 |
| 轻量记忆 | 记印象 | `memory_save/recall/delete/correct/deny` | 已有 |
| 定时/提醒 | 闹钟 | `schedule_task` | 已有 |

**组装接线**（`app/llm/assembly.py`）：

- 新增 `Block("behavior", build_behavior)`，位置紧跟 `persona`（对齐 QQ-agent 的
  「身份句之后立刻是安全规则 → 工作方式 → …」）。
- `build_behavior` 调 `persona_rules.render(...)`，`available_tools` 用 `req.available_tools`。
- `system_prompt_style="single"` 时全部 system 折叠成一条 → 与 QQ-agent 形态一致。
- **覆盖模式**：`behavior_rules_override`（用户文本可整体替换行为段），但安全段与工具协议
  强制追加在后（照抄 QQ-agent 的防呆设计）。

**去重纪律**（重要，源码里明确记录的教训："同一主题两处引导会左右脑互搏"）：

- 新 profile 下，`prompt.build_proactive_instruction()` 里与行为段重复的行（引用用法、
  拍一拍用法、展开标记说明）要**合并到 behavior 段**，不再单独成块；
  只保留"意图补强"这类每轮变化的行。
- `tool_loop.DEFAULT_REPLY_DIRECTIVE` 在 `speech_via_tool` 下换文案：
  "上面是资料，不是稿子；要说话就调 send_message，别复述。"

### 2.2 user prompt 层（表达层）

**目标段序**（按"变化频率"排，稳定在前）：

| # | 段 | 来源 | 说明 |
|---|---|---|---|
| 1 | 【可用表情】 | `qq_faces` / `emoji_lexicon` | 本项目只有"贴表情"能力，段名与内容按此写（不假装有表情包库） |
| 2 | 【引导说明】 | 静态 4 条 | 扫标题→判断要不要开口→`reply_to_message_id` 从 `#id` 取→不回是正常选项 |
| 3 | 【记忆】 | `memory.recall_block_async` | 按群友分组 + "别为用记忆硬聊旧话题" |
| 4 | 【已读信息】 | **group_log store**（水位线之前） | 单行 `[HH:MM] #id 名字(QQ:号)：[引用…]正文` |
| 5 | 【新已读信息】 | 水位线之后、触发批之前 | 锚定模式才有（P4）；P3 可先并进已读 |
| 6 | 【未读信息】 | 本轮触发批 | 每条附标签：`@我 / 提到我 / 提问 / 引用 / 拍我` |
| 7 | 【新加入成员】 | 追加入场者的印象 | P4 |
| 8 | 【活跃模式】 | session.active_topic | P4 |
| 9 | 【当前时间】 | 精确到秒 | **必须最末** |

**行格式统一**：`enhance.format_user_context` 的多行元信息（`(时间)/(当前群号)/发送者：/发送了：/提到了/引用了`）
与 `message_meta` 消歧段，在 `qq_agent` profile 下整体替换为单行形态：

```
[12:03] #12345 摸鱼小能手(10001)：晚上吃啥（@我/提问）
```

配套：`trigger_labels(entry)`（移植 `prompt.js:546 triggerLabels`）。历史行与本轮行**同一形态**，
消歧段可删（单行形态里"名字(QQ): 正文"本身无歧义，保留一句半行说明即可）。

**历史来源切换**（关键设计，见 §3 D2）：

- 已读窗口读 group_log（唯一全量来源），不再把 `session_history` 整段塞进 messages；
- `session_history` 继续维护（压缩、记忆蒸馏、UI、`get_chat_history`），但**不再作为提示词历史**；
- 旧 `background`/`history` 块在 `qq_agent` profile 下关闭（`history_background_enable=False`、
  `profile` 内互斥），避免同一批消息两处出现。

### 2.3 轮次对话层（能力层，本方案主体）

#### 现状

```
事件 → check_trigger(@/关键词) → prepare_prompt（写会话历史/背景/记忆/工具）
     → assembly.build → LLM
     → 文本流式按句 yield → pipeline 逐句发送 → 结束写 assistant 历史
     → 空文本 → "抱歉，我暂时无法回答，请稍后再试。"
```

#### 目标

```
事件 → resolve_wake（档位/随机/拍一拍；未命中→标已读沉 log、不调模型）
     → prepare_prompt（已读窗口/未读批/记忆/工具，注入 send_message + finish）
     → assembly.build（新段落结构） → LLM
     → 模型输出 = 思考（不发送）
         ├─ 调 send_message(messages=[...]) → outbox 入稿
         ├─ 调 finish(summary)             → 记录决定，不出稿
         └─ 调其它工具                      → 结果回传（可继续多轮）
     → 每个 round 边界：flush outbox（真发 + hooks + 历史）   ← 拟人化关键
     → 剩 N 轮且 outbox 空 → 追加一条 user【系统提醒】催发言
     → 循环结束：outbox 空 = 静默（不发、不写 assistant 历史、记一次"看过没说话"）
```

#### 新增 `app/llm/speech.py`

```python
@dataclass
class SpeechOutbox:
    bubbles: list[dict]        # [{text, reply_to, at: [qq...]}]
    finished: bool = False
    finish_reason: str = ""
    active_topic: str = ""
    sent: int = 0              # 已 flush 条数（round 边界 flush 用）

def build_speech_tools(runtime, tool_ctx, outbox) -> list[ToolSpec]:
    # send_message: messages(str|list[str]), reply_to_message_id, at_user_id
    #   → 入稿（不发送），返回 "ok: 已排队 N 条（本轮结束前会按顺序发出）"
    # finish: summary, active_topic
    #   → 标注 finished（不一定终止循环；由 orchestrator 决定）
```

**工具契约（描述文本直接进提示词，必须和 QQ-agent 对齐语义）**：

- `send_message`：`messages` 传字符串=一条，传数组=多条；`reply_to_message_id` 只在需要
  "我在回哪条"时传；`at_user_id` 只在需要点名时传；**不要用空格分句**。
- `finish`：看完不打算说话时调用，`summary` 写一句给自己的理由；说话后也可用来收尾。
- 两者都**不产生"已发送"的语义**——真发由 pipeline 的 flush 完成，因此：
  - `pre_send` / `post_send` 钩子、`SendPolicy` 间隔、`[reply]/[@QQ]` 出口指令、
    `mark_last_assistant_message_id`、group_log 的 `@send_hook`（"我的发言"入账）**全部照旧生效**
    （`@send_hook` 在 onebot client 层，任何发送路径都会触发）。

#### flush 时机（建议：round 边界，而不是最后一轮）

- **round 边界 flush**：模型一旦调 `send_message`，本轮工具执行完就发出去，模型可以继续
  "想下一句"；体感接近真人"想一句发一句"，也不会因为工具循环太久而让用户干等。
- 全部结束才发：实现最简单，但交互延迟高，且"想很多最后只发一句"时用户等待更长。
- 落库口径：**每 flush 一条就写一条 assistant 历史**（不是整轮合并），这样下次
  已读窗口里"我"的发言是真实气泡序列，与 QQ-agent 一致。

#### 静默语义（`finish` / 空 outbox）

- 不发送、不写 assistant 历史；
- 在 `wake_state` 里记 `last_silent_ts`（给"参与度/冷场判断"用，也可在下次提示里说一句
  "上一轮你看过但没说话"——按需）；
- **但要防"叫了不理"**：踩坑保护三条
  1. tier 1（被 @/拍一拍）视为"必须回应"，循环结束 outbox 仍空 → 走一次"强制出稿"重试
     （追加一条 user 消息："对方在叫你，你必须发一条"），仍空才静默并记 warning；
  2. 兜底话术从"抱歉，我暂时无法回答"改为可配置，默认在本 profile 下**关闭兜底**（静默更拟人）；
  3. 日志/遥测记录 `wake_silent` 计数，便于观察"是不是哑火太多"。

#### 【系统提醒】催发言

在 `chat.stream_response` / `operators` 的工具循环里：`rounds_left <= 3` 且 outbox 空时，
`messages.append({"role": "user", "content": "【系统提醒】工具调用轮次只剩 N 轮。如果你打算回应，"
"请立刻调用 send_message 把话发出去，不要再调用其它工具——轮次耗尽后群友收不到任何内容。"})`
（**必须用 user 角色**：Anthropic/Gemini 适配器会把中途 system 上提到最前，导致提醒失效。）

### 2.4 唤醒层（提示词依赖的数据，P3）

新增 `app/llm/wake.py`（移植 `prompt.js:446 resolveContextTier` + `:546 triggerLabels`）：

```python
def resolve_wake(trigger_entries, *, cfg, self_id, self_nickname, bot_name,
                 is_private, roll=None) -> WakeResult(tier, reason, should_respond)
def read_window(store, scope, *, watermark_ts, limit) -> list[LogEvent]
def unread_events(store, scope, *, watermark_ts) -> list[LogEvent]
def label_of(entry, ctx) -> list[str]      # @我/提到我/提到我(备注名)/提问/引用/拍我/拍一拍
```

- 档位：1 仅@ / 2 +关键词 / 3 +随机(`random_percent`) / 4 全响应；拍一拍=1 档；私聊恒 4。
- **未命中 → 不改历史、不调模型**；消息本来就在 group_log 里，天然"沉为已读"（不需要新功能）。
- 水位线：`data/llm/<bot_id>/wake_state.json`（`{scope: {last_ts, last_msg_id, last_silent_ts}}`），
  首次无水位时回落为"最近 N 条当作已读"。
- 与既有权重的冲突处理（必须写清楚）：
  - `reply_cooldown` 与 tier 3/4：**冷却优先**，冷却中不参与随机/全响应，但 tier1 可越过；
  - 群聊 `group_enable` 仍是硬开关；私聊不走档位。
- `message_private` 也要进 group_log（现在私聊只记拍一拍）——否则私聊没有已读窗口。

### 2.5 配置项（`app/llm/config.py` + `config_schema.py`）

```python
"assembly_profile": "legacy",            # legacy | qq_agent（总闸，先默认 legacy 灰度）
"behavior_rules_enable": True,           # behavior 段开关
"behavior_rules_override": "",           # 覆盖行为段（安全/工具协议仍强制追加）
"participation": "medium",               # low | medium | high（改写"该说/不该说"）
"emoji_react_level": 1,                  # 0~3（改写贴表情频率行）
"speech_via_tool": False,                # 文本=思考，发言=send_message
"speech_allow_silence": True,            # 允许"看完不回"
"speech_force_reply_on_tier1": True,     # 被@必回保护
"speech_empty_fallback": "",             # 空回复兜底话术（空=静默）
"speech_max_bubbles": 2,                 # 每轮气泡上限（超出丢弃并记日志）
"speech_flush_on_round": True,           # round 边界 flush（False=整轮结束才 flush）
"speech_reminder_rounds": 3,             # 剩 N 轮催发言
"wake_tier": 4,                          # 1~4
"wake_random_percent": 0,
"wake_keywords": [],
"wake_history_count": 60,                # 已读窗口条数
"wake_poke_as_summon": True,
"prompt_anchor_enable": False,           # P4
"prompt_anchor_max_extra_read": 5,       # P4
"active_topic_enable": False,            # P4
```

## 3. 需要拍板的三个设计决策

| # | 决策 | 选项 | 我的建议 |
|---|---|---|---|
| D1 | `send_message` 怎么发 | A 工具内直接 `bot.send_*`；B 入稿 + pipeline flush | **B（outbox）**：复用 pre_send/post_send/SendPolicy/出口指令/历史写入，且天然支持静默与分条上限；A 要重写整条发送链路 |
| D2 | 提示词历史主来源 | A group_log（对齐 QQ-agent）；B 会话历史（现状）；C 两者并存 | **A**（`qq_agent` profile 下）；会话历史保留做压缩/记忆/UI/`get_chat_history`。C 会导致同一批消息两处出现、token 翻倍 |
| D3 | system 形态 | A 单条（`system_prompt_style=single`）；B blocks | **A 作为对齐档默认**，blocks 保留给 legacy 档；理由：与 QQ-agent 同形态、段落顺序可控、便于整体覆盖 |

## 4. 分期实施（每期独立可提交、可回退）

### P1 · 表达层（1~2 天，风险低）

> **状态：已落地**（`5df5366` 行为规则独立成段）。实际改动与本文一致：
> 新增 `app/llm/persona_rules.py`（`detect_caps` + 分段 `render`），改
> `assembly.py`（`behavior` 块 + `assembly_profile`/`behavior_rules_*`）、`config.py`、
> `config_schema.py`、`prompt.py`（`qq_agent` 档下 proactive 行去重）。
> `tests/test_persona_rules.py` **14 例**，本文 4 条验收逐条覆盖：
> ① `test_segment_order_is_stable` ② `test_rendered_tool_names_are_real_tools`
> ③ `test_speech_contract_follows_capability` ④ `test_override_keeps_safety_and_work_protocol`。
> 段落数在后续 prompt 重构中收敛（487 → 409 行），语义未变。

改动：
- 新增 `app/llm/persona_rules.py`（14 段 + `detect_caps` + `render`）
- `app/llm/assembly.py`：新增 `behavior` 块；`blocks` 顺序调整；profile 判断
- `app/llm/config.py` / `config_schema.py`：`assembly_profile` / `behavior_rules_*` / 档位配置
- `app/llm/prompt.py`：与 behavior 段重复的 proactive 行在 `qq_agent` 下不再单独注入

验收：
- 单测 `tests/test_persona_rules.py`：
  1. 段落顺序固定；2. **提示词里出现的工具名 ⊆ 本轮 `available_tools`**（防教模型调不存在的工具）；
  3. `speech_via_tool=False` 时**不出现**"文本不会发出"；4. 覆盖模式下安全段与工具协议仍在。
- `prompt_dump` 抓一次真实请求人工比对段落顺序。

风险：无结构风险（不动流水线）。这一步**单独就能改善拟人化**（反AI味/该说不该说等段落立即生效）。

### P2 · 发言通道（核心，3~5 天，风险中高）

> **状态：已落地**（`22415da` 记账本 + `19c8db6` 接入流式主循环）。与本文的差异见
> `prompt-assembly-redesign.md §9`：非流式路径明确不支持（只告警）、强制补救轮
> 只在 @/私聊时触发、`tool_loop.py` 的改动落在 `tool.make_executor`（按
> `source="speech"` 跳过「回应要求」）。验收 1/2/3/4/5/7 已有单测覆盖，
> 第 6 条（打断）沿用原有 `_should_break_stream` 判定，未新增用例。

改动：
- 新增 `app/llm/speech.py`（outbox + 两个工具 + flush）
- `chat.py`：`prepare_prompt` 注入 speech 工具；`generate_response`/`stream_response` 走 outbox；
  静默分支；兜底话术可配；系统提醒
- `pipeline.py`：`_run`/`_run_stream` 的 flush 与"未发言"分支；历史按条写入
- `tool_loop.py`：`speech_via_tool` 下的回应要求文案
- 新增 `tests/test_speech_outbox.py`

验收（必须全覆盖）：
1. 只调 `send_message` 一次 → 只发 1 条，历史 1 条 assistant；
2. 传数组 3 条但 `speech_max_bubbles=2` → 发 2 条且日志提示丢弃；
3. 只调 `finish` → **0 条发送**、无 assistant 历史、日志有 finish 原因；
4. tier1 空 outbox → 触发一次强制出稿重试；仍空 → 静默 + warning（开关可关）；
5. `reply_to_message_id` / `at_user_id` → 段序为 reply→at→text；
6. 打断（`interrupt_enable`）在 round 边界 flush 后仍生效；
7. `post_send` 钩子与 group_log 的"我的发言"入账仍然发生。

风险：这是唯一会改"用户能感知到的行为"的一期。必须带开关、灰度、可一键回 legacy。

### P3 · 已读/未读 + 唤醒（3~5 天，风险中）

改动：
- 新增 `app/llm/wake.py`（档位 + 标签 + 水位线）
- `pipeline.py`：准入改为 `resolve_wake`
- `module/modules/group_log/main.py`：增加 `message_private` 记录
- `assembly.py`：`read` / `unread` 块；`qq_agent` 下关掉 `background`/`history` 块
- `prepare_prompt`：改为从 group_log 取已读窗口
- `enhance.py`：`qq_agent` 下改用单行格式化 + 标签

验收：
1. 随机档传入固定 `roll` → 结果确定（可测）；
2. 未命中 → 不调 LLM、无会话写入、消息仍在 group_log；
3. 已读窗口不含本轮触发批；窗口滚动后不重复（水位线落盘、重启恢复）；
4. 引用/拍一拍/撤回在已读窗口中的渲染正确（撤回不出现、拍一拍只作事件）；
5. token 对比：同一场景 P3 前后 user 消息字符数有记录（便于回归）。

风险：历史来源切换会改变"模型看到的对话连续性"，需要对着真实群聊跑一轮人工观察。

### P4 · 进阶（按需）

- 前缀缓存锚点（`app/llm/anchor.py`，移植 `resolvePromptAnchor`）
- 活跃期（`active_topic` 存 session，`finish` 回填，偏了自动退出）
- 收藏表情包库（目录 + 备注 + `send_sticker` + `list_stickers`）——独立功能，工作量最大
- 群友印象记忆（`memberImpression` 类型 + 按"窗口内出现的群友"注入）
- 主人识别（owner 专属人设 + 主人规则）

### P5 · 灰度与收尾

- `assembly_profile=qq_agent` 默认打开，`legacy` 保留一个版本周期；
- 收集 `wake_silent` / `wake_no_reply` / 气泡数分布 / 投诉率，决定默认档位参数；
- 文档更新 `docs/`（本文件 + comparison）与 dashboard 配置说明。

## 5. 三处不该照搬

1. **「你没有本地工具」**：QQ-agent 是纯聊天桥，本项目有一整套 OneBot 工具（含群管理）。
   照抄会让模型否认自己有能力，反而在真被要求时编造。应改写为**权限边界**话术：
   "群友让你禁言/踢人/改名片/改设置时，说明需要管理员在管理端操作，不要自己动手。"
2. **会话历史的角色**：本项目用 session history 做压缩摘要、记忆蒸馏、上下文补全、
   UI 回放。QQ-agent 是"每次新开会话 + 只靠已读窗口"。对齐后仍要保留这套存储，
   只是**不进提示词**——不要真的把存储删掉。
3. **按句拆条**：QQ-agent 明确禁止"把一句话拆两条"；本项目 `split_sentences` 是为**流式体验**
   设计的。对齐时把它降级为"仅当模型输出超过单条上限时才切"，而不是默认按句切。

## 6. 主要风险清单

| 风险 | 表现 | 缓解 |
|---|---|---|
| 提示词指向不存在的工具 | 模型反复调不存在的工具 / 整轮哑火 | P1 的单测护栏（工具名子集断言） |
| 静默过度 | "叫了不理" | tier1 强制出稿重试 + `speech_allow_silence` 开关 + 遥测 |
| 双套历史重复 | token 翻倍、模型重复内容 | profile 内互斥：`qq_agent` 下关 `background`/`history` 块 |
| 流式体验变差（文本不再发出） | 用户等待更久 | round 边界 flush + 首条气泡尽早发出 + 必要时先发"..."?（不做，非真人行为） |
| token 增加（behavior 段 ~2.5k 字 + 更大已读窗口） | 成本上升 | P4 锚点 + `wake_history_count` 可调 + 便宜模型跑判断 |
| 中途 system 提醒被上提 | 催发言失效 | 提醒一律用 `role=user` |
| 一次改太多无法归因 | 效果说不清 | P1/P2/P3 分开关、分提交、可回退 |

## 7. 建议的执行顺序

先做 **P1**（纯提示词，零风险，立即见效），再做 **P2**（发言通道，核心），
然后 **P3**（已读/未读 + 档位）。P1+P2 完成即可获得 QQ-agent 效果的主体；
P3 才是"混在群里"从"答话机器人"变成"群友"的那一步。
