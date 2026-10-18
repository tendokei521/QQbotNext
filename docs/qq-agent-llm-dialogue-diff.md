# QQ-agent vs 本项目：LLM 对话逐条差异清单

> 依据：`docs/research/qq-agent-prompt-assembly.md`（`1/QQ-agent-main` 只读代码调研，全部带 `文件:行号`）、
> `docs/research/qq-agent-tools-and-config.md`（工具/配置面调研），以及本文 §0 的**真机对照实验**。
> 本仓库一侧的事实都标了 `app/...` 出处。
>
> 阅读顺序建议：先看 §0（静默的根因与实测结论），再看 §1 起的分组差异，最后 §6 是采纳清单。

---

## 0. 先回答「模型为什么偏好不回复」——实测，不是推测

线上症状：开了发言通道后，bot 3437542570 对每条消息都只输出文本、一次 `send_message` 都没调，
连 @ 都没反应；@ 场景的补救轮（`_force_speak_round`）也空跑，日志 `静默（看过但没说话）`，
token 账 `输出 50 / 回复 34 字符`。

把**线上那一轮的真实请求**（27 条 messages + 162 个工具 schema，共 51.8 KB）原样落盘，
逐个改一个变量重发（模型 `deepseek-v4-flash`，同一 key / base）：

| # | 变量 | 结果 |
|---|---|---|
| 1 | 基线（发言协议只写在 system 行为段里） | 输出纯文本 → 静默 |
| 2 | 协议作为**尾部 system** 消息追加（"你必须调用"） | 仍然静默 |
| 3 | 删掉历史里的 assistant 纯文本示例 | 仍然静默 |
| 4 | 清掉人设里「输出：以第一人称回答…输出纯文本段落即可」那一句（单独做） | 仍然静默 |
| 5 | 协议贴在 **user 消息**里（同一消息尾部 / 另起一条 user） | **稳定调用 `send_message`** |
| 6 | 协议只写"你必须调用"（祈使、不带因果） | 仍然静默 |
| 7 | 协议写"你输出的文本不会发出去"+ 两种正常结局 | **稳定调用 `send_message`** |
| 8 | 精简工具表（162 → 7 个） | 单独做无效；配合 #5/#7 依然稳定调用 |

### 结论（两条，都很反直觉）

1. **位置决定成败，不是措辞强度。** 关于本程序的**事实**（"你输出的文本不会发出去"）
   必须贴在消息旁边（user 段）。写进 system 段它只是"另一条规则"，而人设块里
   「输出：以第一人称回答…输出纯文本段落即可」更靠前、更具体，模型跟着人设走。
   — 这正是 QQ-agent 那边**不会**发生这件事的结构性原因：它的人设正文
   （`roleText`）里**没有输出格式条款**，且人设本身也在 user 段（`prompt.js:788-790`）。
2. **纯祈使句会失效。** "你必须调用 send_message"反而让模型写文本；必须带上
   "文本不会发出去"这个因果事实（#5 vs #6）。我原来的 `FORCE_SPEAK_REMINDER`
   正是那句无效写法，所以补救轮也救不回来。

### 已修（commit `80e4525`）

- `app/llm/assembly.py::apply_speech_protocol()`：发言通道生效时把 `SPEECH_PROTOCOL`
  贴到最后一条 user 消息末尾（多模态 content 追加 text 块）；只动最后一条，
  历史前缀逐字不变，前缀缓存不受影响；文本即发言的档位一个字都不加。
- `app/llm/speech.py`：`SPEAK_REMINDER` / `FORCE_SPEAK_REMINDER` 补上因果句。
- `persona_output_conflicts()`：扫人设里「输出：/纯文本/以第一人称」这类条款 → 一次性告警，
  提示挪到行为层（**运行时不改用户的人设文本**）。
- 验证：同一份真实请求复跑，全量 162 工具与精简 7 工具**两次都调用了 `send_message`**；
  全量 1384 passed。

> 注意：#1~#8 只是"哪一层的话更管用"的机制验证，不是"人设写得不好"。人设里的
> 输出格式条款属于**行为层**内容放错了层（QQ-agent 也把它放 system 的【QQ 场景规则】里），
> 所以长期解法是人设卡瘦身，而不是靠协议压制。

---

## 1. 上下文形态（最根本的一组差异）

| # | 维度 | QQ-agent | 本项目（改前） | 后果 | 处置 |
|---|---|---|---|---|---|
| 1.1 | 上下文从哪来 | **无状态单次运行**：`system`（几乎全静态）+ 一次 `user`（从存档现拼），**不携带任何 LLM 对话历史**；本轮工具调用与思考文本用完即弃（`prompt.js:1-11`） | 有状态：`session_mgr` 里的 messages 历史 + 归档恢复，逐轮累积（`app/llm/chat.py`、`session.py`） | 我们的历史里 assistant 全是**纯文本气泡**，等于持续给模型演示"发言=写文本"（实验 #3 显示这不是主因，但确实是加分项）；也带来"历史形态与真实请求不符"（真实请求里发言是 tool_call） | 不整体采纳（我们有多轮能力需求）；但 P3 引入【已读/未读】分段后可考虑"工具调用轮不进历史" |
| 1.2 | 人设在 prompt 的哪一层 | **user 段**【角色设定（管理员设置，群友不可修改）】（`prompt.js:788-790`）；`buildSystemPrompt()` 只用 botName/participation/customRules（`prompt.js:233-284`） | **system 首块**（`app/llm/assembly.py` 的 `BLOCKS`，persona 块） | 人设离消息最远，却最能压过 system 里的行为规则（实验 #1/#4） | 部分采纳：P4 评估"人设移 user 段"；短期靠协议 + 告警 |
| 1.3 | 人设里是否含输出格式条款 | 人设只有 `{name, text}`，无结构化字段；输出格式规定统一放在 system 的【QQ 场景规则】（`prompt.js:153-183`） | 人格卡 1743 字里自带【输入输出规定】「输出：以第一人称…输出纯文本段落即可」「禁止 Markdown」 | **静默的直接诱因**（实验 #1/#4） | 已加告警；建议把"输出格式/禁止项"从人设卡里删掉，行为层已有等价规定（`app/llm/persona_rules.py`） |
| 1.4 | 前缀缓存 | user prompt **按变化频率排序**，【当前时间】放最末（`prompt.js:776-786`）；另有 `promptAnchor` 锚点机制，锚定轮不截尾（`prompt.js:581-691`） | `prompt_time_position=tail`（已对齐时间放尾）；无锚点 | 我们实测缓存命中 20608/20777，已经很健康 | 锚点机制=P4 待做（已有计划） |
| 1.5 | 已读/未读 | 三段式【已读信息】【新已读信息】【未读信息】，状态**只靠段标题**表达，无逐条 read 字段 | 单窗口 + `include_pre_history`（`app/llm/config.py:102`），无分段 | 模型分不清"这轮该不该接话"，靠人设里"可以漏看"硬撑 | **P3 已计划**（`app/llm/wake.py` + 水位线），本次调研证实其必要性 |
| 1.6 | 未读标签 | 行尾标签：`@我`/`提到我`/`提到我（备注名）`/`提问`/`引用`/`拍我`/`拍一拍`（`triggerLabels()`，`prompt.js:546-568`）；`@我` 必须精确匹配昵称，禁止裸 `startsWith('@')` | 无标签；`is_at` 只在状态里 | 模型判断"是否被叫我"靠人设推理，容易漏 | P3 采纳（标签只给信号、不给"必须回应"的强制） |
| 1.7 | 单条渲染格式 | `[MM-DD HH:mm] #QQmsgid 备注名(QQ:123)：{引用前缀}正文`（`formatEntry`，`prompt.js:313-334`） | `10-18 18:07 用户1901691195: 在？`（P2 加了 `#id` 前缀） | 已基本对齐；我们缺"引用前缀"渲染与"机器人自己渲染为我" | 小改，P3 顺手做 |
| 1.8 | 上下文条数 | 统一 `store.historyCount=80`，**四档相同**（档位只管触发方式，不管条数） | `history_rounds=50`（`config.py:68`）+ 超限摘要压缩（`context_compress_enable`） | 我们的压缩摘要会丢细节；QQ-agent 直接截断 | 保持现状（我们有 token 预算需求），但可把默认值向 80 靠 |
| 1.9 | 跨轮"记忆" | 不注入 LLM 历史，只注入【记忆】（memberImpression）与三段聊天记录 | 注入完整 messages 历史（LLM 语义天然连贯） | 各有取舍：我们连贯但贵、易被历史带偏 | 不采纳 |

---

## 2. LLM 对话循环

| # | 维度 | QQ-agent | 本项目 | 差异后果 | 处置 |
|---|---|---|---|---|---|
| 2.1 | 轮数上限 | `api.maxRounds` 默认 **12**（UI 1~40，`config.js:64`） | `max_tool_rounds` 默认 5（`app/llm/chat.py:391`；线上配成 20） | 12 轮足够"展开→回答"；我们 5 轮偏紧、20 轮偏松 | 建议默认 8~12，配置项保留 |
| 2.2 | 工具结果回填 | `role:"tool"`，**不追加任何指令**（`orchestrator.js:1173`） | `role:"tool"` + `append_reply_directive()` 追加"该说话了"指令（`app/llm/tool.py:246`），发言工具除外 | 我们每轮工具结果都带一句催促，会污染"可以不说话"的语义 | 部分采纳：`source=="speech"` 已排除；可考虑只在对内工具上保留 |
| 2.3 | 媒体回填 | 图片/视频**绝不进 tool 消息**，另发一条 `role:"user"` 多模态（先 tool 后 user，`orchestrator.js:1182-1188`） | `place_images` 归位到 user 块（`app/llm/assembly.py`） | 已等价 | 已对齐 |
| 2.4 | 工具结果截断 | 发给模型的 content **不截断**（只有 `web_fetch` 自身 ≤20000；UI 副本截 2000） | `TOOL_RESULT_MAX = 2000`（`app/llm/tool.py:24`） | 长记录/长搜索结果会被截，模型可能答不全 | 可调大（如 4000）或按工具 `max_result` 分级 |
| 2.5 | 模型把工具调用写在正文里 | 核心自带 `parseInlineToolCalls()`，命中时改写最后一条 assistant（`orchestrator.js:1039-1085`） | **无**（`app/llm` 里没有 inline 解析） | 弱模型（deepseek-flash 尤其）偶尔把 `{"name":"send_message",...}` 写进正文 → 我们当成思考丢掉 → 静默 | **建议采纳**（P3，成本低、直接减少静默） |
| 2.6 | 回复抢救 reply-rescue | 代码在，但**本仓库不生效**（`skills/`、`plugins/` 目录缺失 → cap 全 null → 恒返回 `[]`） | 无 | 参照价值≈0，别照着抄 | 不采纳 |
| 2.7 | 重试策略 | 三层：单请求每模型 3 次（1s/2s 退避）→ `fallbackModels` 换模型 → **会话级重试仅当"一条都没发出"**（`sessionRetryAttempts=2`） | `retry_attempts` + `max_empty_retries` + `max_tool_rounds`；空回复重试已有 | QQ-agent 的"零发送才重试"判据很值得抄：避免重复发言 | 可采纳（P4） |
| 2.8 | "不说话"的表达 | 三法：直接结束正文 / 调 `finish({summary})` / 提示词明文许可；`status="noreply"` 是**一等正常终态**（不重试、不补救） | `finish` 工具 + 静默分支（`ctx.state["silent"]`）；**@/私聊会强制补一轮**（`_force_speak_round`） | 我们的强制补轮与 QQ-agent 设计相反，但我们的定位更"陪伴"，被点名必须回是合理的 | 保留，但措辞已按实验 #7 修正（带因果句） |
| 2.9 | 轮次提醒 | 剩余 **3** 轮且尚未发言时注入【系统提醒】 | `speech_reminder_rounds=3` 注入 `SPEAK_REMINDER`（已对齐） | 已对齐 | 已对齐 |
| 2.10 | 状态机 | `done`/`noreply`/`error` 三态显式落库 | 有 `silent` 标记 + 日志，无三态落库 | 排障时可读性差 | 可选（P4） |
| 2.11 | 工具表规模 | **28 个**精选工具 + 七层可用性过滤（`tool-registry.js:126-212`） | **162 个**（全部 OneBot API 直出，schema 51.8 KB ≈ 19.9k input tokens） | 成本高、选择面过宽；实验 #8 显示"精简工具表本身"不解决静默，但会显著省 token | **建议采纳**：默认只暴露常用子集（发言/展开/搜索/表情/戳），其余按需开关 |
| 2.12 | 工具可见性 | 分类开关 + 单工具 overrides + `requires*` 能力门控（`vision`/`webSearch`/`browseLock`） | `permission` + `scopes`，无分类/单工具开关 | 我们不能"关掉一堆 OneBot 工具" | 与 2.11 合并处理 |
| 2.13 | 思考文本 | `result.reasoning` 从不进 messages、从不发群；内联命中时 assistant content 置 `null` | 同样不进历史；`finish` 的 summary 只落状态 | 已对齐 | 已对齐 |

---

## 3. 人格与规则层

| # | 维度 | QQ-agent | 本项目 | 差异后果 | 处置 |
|---|---|---|---|---|---|
| 3.1 | 人设数据结构 | `{name, text}` 两字段，few-shot 是塞在正文里的伪示例（`personas.js:100-169`） | 角色预设 `role_presets`（单段正文，1743 字），无结构化字段 | 结构相似；区别在**内容分层的自觉**（见 1.3） | 保持 |
| 3.2 | 覆盖模式的底线 | `systemPromptOverride` 生效时**强制追加**安全规则 + 工具协议（`prompt.js:233-245`） | `system_prompt_style` 有 `blocks`/`single` 两档，`single` 时行为段是否保留由配置决定 | 用户自定义 system 时我们可能丢掉协议 → 静默复发 | 建议：`single` 档也强制追加协议段 |
| 3.3 | 参与度/表情档位改写 | 两档都改**system 段的行**（`participationText()`、`sticker.encourage`） | `participation`/`emoji_react_level` 同样改写行为段行（P1 已对齐） | 已对齐 | 已对齐 |
| 3.4 | 反 AI 味手段 | 段落级 12 条 + 结构性 9 条（见调研 §7） | P1 已照抄大部分段落（`app/llm/persona_rules.py` 的 14 段） | 段落已接近，**结构性**还没抄（见 §1/§2） | 见 §6 |
| 3.5 | botName | 永不随会话改，且身份行放 system 第一行 | persona 卡自带名字，无独立身份行 | 影响极小 | 不采纳 |

---

## 4. 记忆 / 表情 / 戳一戳

| # | 维度 | QQ-agent | 本项目 | 差异后果 | 处置 |
|---|---|---|---|---|---|
| 4.1 | 记忆分层 | **只有一层** `memberImpression`（`data/memory/group_<号>/<QQ>.json`），无话题/关系分层；旧 activeTopic 迁移时丢弃（`memory.js:124`） | 更复杂的记忆模块（`memory_enable`，多来源） | QQ-agent 更"胆小"：只记对个人的印象 | 不采纳（我们更强，但要防止记忆把话题拉回旧事） |
| 4.2 | 记忆检索 | 规则式：**只注入本轮出现在 prompt 里的群友**，每人最近 3 条，最多 15 人（`memory.js:455-469`） | 语义检索（向量/关键词，视配置） | 规则式更稳定、零成本；语义检索召回更宽 | 可采纳"只注入本轮出现的人"这个裁剪规则 |
| 4.3 | 记忆注入位置 | user 段【记忆】 | 独立 memory 块（system） | 同 1.2 | 随 1.2 一起评估 |
| 4.4 | 表情 | 工具 + **软引导**；`sticker.enabled=false` 并不禁用表情工具（只 `collect_sticker` 抛错） | `emoji_react_level` 改写行为段频率行 | 已对齐（P1） | 已对齐 |
| 4.5 | 拍一拍 | `send_poke`，提示词允许自然回应"？干嘛" | 已有 `send_poke` + 行为段描述 | 已对齐 | 已对齐 |

---

## 5. 配置面：它有我们没有 / 我们有它没有

**QQ-agent 有、我们缺（按价值排序）**

| 配置键 | 作用 | 我们缺的后果 |
|---|---|---|
| `promptAnchor`（+`maxExtraRead`） | 锚定轮把"上次看完的位置"钉住，缓存与已读语义都靠它 | 上下文条数一改就全变，已读语义无从表达 |
| `sessionRetryAttempts` | **零发送**才重试会话 | 我们可能重复发言 |
| `fallbackModels` | 主模型失败换备用模型 | 我们只能整体失败 |
| `randomPercent` / 滑条四档 | 随机参与的频率控制 | 我们没有"偶尔自己接话"的档位（只有 proactive 概率） |
| `wakeDelayMs` / `drainDelayMs` | 攒消息再唤醒（固定 2000ms，不可重置） | 我们逐条唤醒，容易被多条消息打断 |
| `api.thinkingMode/Effort/Budget` | 思考强度 | 我们只有流式/非流式 |
| `security.browseLock` | 硬拦联网/发图 | 我们靠 permission |
| 分类开关 / 单工具 `overrides`、`requires*` | 工具可见性七层 | 我们 162 个工具全暴露（见 2.11） |

**我们有、它没有**：`assembly_profile`（`legacy`/`qq_agent` 灰度开关）、`speech_via_tool` 生态（
`speech_max_bubbles`/`speech_max_bubble_chars`/`speech_reminder_rounds`/`speech_force_reply_on_call`）、
`user_message_format`、`prompt_time_position`、`context_compress_enable`、`context_token_budget`、
`meta_sender_style`、`history_cross_query_enable`（私聊里查自己也在的群）。

**它的配置结构**：扁平 `DEFAULT_CONFIG` + 分群/峰谷覆盖链（`storeConfigForChat`，`config.js:682-718`）；
我们是 `module_config` 表 + `config_schema.py` 声明 + WebUI 热应用。**它没有的能力**：
热更新（要重启）、UI 外的灰度开关、按 bot 多实例配置。

---

## 6. 采纳清单（按「性价比 / 与静默的关系」排序）

**已落地（本次）**
1. 发言协议移到 user 段（§0 实验 #5/#7，commit `80e4525`）——直接治静默。
2. 提醒措辞带因果（同上）。
3. 人设输出格式条款告警（同上）。

**P3 建议采纳（成本低、直接相关）**
4. **内联工具调用解析**（§2.5）：模型把 `{"name":"send_message",...}` 写进正文时当成工具调用执行。
   这是 deepseek-flash 这类模型的常见退化方式，能直接减少剩余静默。
5. **未读标签**（§1.6）：`@我`/`提到我`/`提问`/`引用`/`拍我` 行尾标签，只给信号不强制。
6. **已读/未读三段式 + 水位线**（§1.5，原 P3 计划）。
7. **工具表瘦身 + 分类开关**（§2.11）：默认暴露常用子集，其余按需打开；预计省 6~10k input tokens/请求。

**P4 建议采纳（结构对齐）**
8. 人设卡瘦身指引 + 人设移 user 段（§1.2/1.3）。
9. `promptAnchor` 锚点（§1.4）。
10. 会话级"零发送才重试"（§2.7）+ `fallbackModels`。
11. 攒消息唤醒 `wakeDelayMs`（§5）。

**明确不采纳**
12. 无状态上下文（§1.1）——我们有多轮连贯需求，且已用 `group_log` 保底。
13. `reply-rescue`（§2.6）——源头仓库里它本身不生效，没有参照价值。
14. 记忆分层退化到单层（§4.1）——我们更强，只需采纳"只注入本轮出现的人"这一裁剪规则。

---

## 7. 复现方式（本次实验脚本，未入库）

`_scratch_dump_prompt.py` 用线上 bot 的**真实配置**跑 `prepare_prompt` 落盘一份请求；
`_scratch_replay.py` / `_scratch_matrix*.py` 对同一份请求逐变量重发。
结论表格见 §0。脚本是临时件，已在使用后删除；如需长期回归，可把 `_scratch_dump_prompt.py`
改造成 `tools/dump_prompt.py`（只读配置、不触网）纳入仓库。
