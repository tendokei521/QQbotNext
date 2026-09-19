# 上下文补全（Context Expand）设计定稿

> 状态：P0~P4 已完成并测试通过。本文档为设计基准，后续调整按第 6 节的约定走。

## 1. 解决的问题

QQ 消息进入 LLM 时经常只剩"骨架"：`@123`、`[引用]`、`消息 id`。模型既不知道 123 是谁、
被引用的是什么，也不知道"这里缺东西"。本仓库此前的具体错配是：

| 骨架来源 | 改动前 | 现在 |
|---|---|---|
| 触发消息的 `@id` | 已反查成 `昵称(QQ)`（`enhance._collect_at_info`） | 不变 |
| **群聊背景块的 `@id`** | **裸 `@123`**（`group_context._segment_text`） | `@三哥(123)`；取不到 → `【未展开:用户123】` |
| 引用消息 | 触发消息已展开；背景块只有 `[引用]` | 背景块 `【未展开:引用456】`；触发消息的引用若没有正文（合并转发/图片/已过期）也转成同一标记 |
| 合并转发 | 只有 `[合并转发]`；触发消息的引用甚至只渲染成英文段名 `[forward]` | `【未展开:合并转发576048059】`（带**承载转发的那条消息 id**，可直接展开） |
| 图片/语音/文件 | `[图片]` 占位 | 不变（**无展开手段，就不标记**） |
| message_id | 完全没有进上下文 | 不变（由 `expand_context` 的 `messages` 参数按需取） |

关键背景：**非 @ 的群消息不入会话历史**，所以群聊背景块是"群里其他人在聊什么"的唯一可见
窗口——而它恰恰是改动前唯一输出裸 `@123` 的地方。

## 2. 设计原则

1. **能预展开的绝不留给模型**：@ 昵称反查管道本来就有，接到渲染层即可（零模型成本、零工具往返）。
2. **只标记可被解决的缺口**（marker must be actionable）：图片/语音无法转文字就不标记；
   标了却解决不了，只会让模型空转或谎称"无法完整回答"。
3. **缺口由代码算出来告诉模型，不靠模型自省**：渲染层与工具头部直接给出缺口摘要。
4. **标记与手段同开同关**：`context_expand_enable` 同时控制标记与三个 `expand_*` 工具。
5. **提示词只负责最后一段**：能预展开/能预扫描的都做完了，提示词才讲得通、才有人信。
6. **结果口吻也是有影响的输入**：工具结果用"我看到的"第一人称、不写"已展开 N 项"这类汇总头
   ——汇总头 + 字段清单读起来像待总结的报告，会把模型带向"复述/汇报"。

## 3. 分层

| 层 | 实现 | 作用 |
|---|---|---|
| 反查层 | `app/llm/nicknames.py` | id → 昵称（群名片优先），共享缓存 + 批量并发；`enhance` 与渲染层共用 |
| 渲染层 | `group_context._segment_text / extract_msg_text / format_online_history / fetch_group_online_history` | 预展开 `@123`；展不开才落 `【未展开:…】`；已取回的落 `【已展开:… → 摘要】`；末尾追加缺口摘要 |
| 摘要层 | `group_context.unresolved_items / unresolved_summary` | `【本段含 N 处未展开内容（M 类）：…；可调用 expand_message 展开后再回答】` |
| 工具层 | `app/llm/context_tools.py` | 三个意图工具：`expand_recent`（按位置）/ `expand_message`（按 id）/ `expand_user`（按 QQ）；公共取回 API `fetch_entities` / `fetch_recent` |
| 指代层 | `app/llm/focus.py` + `referent.py` | 会话焦点表 + 指代判定 + 确定性预取（见 [referent-resolution-design.md](referent-resolution-design.md)） |
| 提示词层 | `prompt.build_proactive_instruction` | 环境行 + 未展开标记语义 + 指代解析三条 + footer 例外条款；按本轮实际可用工具裁剪 |
| 机制层 | `tool_loop`（并发执行、参数失败不静默、结果尾附回应要求）、`max_tool_rounds`、`chat.build_initiative_tools` | 让"一次展开多个 id"不会超时、不被静默降级、不写成长串汇报；主动消息/定时任务路径也有工具 |

### 工具结果的实际形态

```
（我翻到了这条消息）老师…(1901691195)：转发内容 —— 无聊的阿忧: 真让他调成了 ｜ NlKO: … [id 576048059] [关系：当前消息引用的消息]

（上面这些是给你自己看的资料，不是要你转述的稿子：直接用你自己的口吻回应用户，
  不要复述、不要列条目、不要以“根据记录/已展开”开头；一两句就够。若还需要更多信息，可以继续调用工具。）
```

第二段是「回应要求」（`tool_result_directive*`），拼在结果末尾而非新增 system 消息——
`anthropic` / `gemini` 适配器会把所有 system 上提到顶层参数，中途加的会静默失效。

## 4. 接入点

| 入口 | 工具来源 | 提示块 |
|---|---|---|
| 普通对话（`chat.prepare_prompt` → `generate_response` / `stream_response`） | `chat._collect_llm_ext` | `assembly.BLOCKS` 的 `proactive` 块（取 `_proactive_instruction`） |
| 主动消息（`proactive._check_and_chat`） | `chat.build_initiative_tools`（`event=None`） | 同上（无用户提问 → 不做意图补强） |
| 定时任务（`scheduler._generate_reply` / 流式分支） | 同上（`scheduler._collect_tools`） | 同上 |

- 三条路径都在**切换会话配置档案之后**收集工具（与 `chat.handle` 顺序一致）。
- 主动/定时路径的 `ToolContext` 没有 `event`，会话目标由 `bot` / `user_id` / `group_id` 显式给出。
- 块顺序与清洗统一由 `app/llm/assembly.py` 的块表决定（见该模块文档）。

## 5. 配置项

| 键 | 默认 | 作用 |
|---|---|---|
| `fetch_at_nickname` | True | @ 昵称反查总开关（同时管渲染层预展开） |
| `context_expand_enable` | True | 未展开标记 + 三个 `expand_*` 工具 + 三态渲染（同开同关） |
| `max_tool_rounds` | 5 | 工具循环轮数上限（流式/非流式共用） |
| `tool_result_directive_enable` | True | 工具结果末尾附「回应要求」 |
| `tool_result_directive` | 空 | 自定义回应要求文本（空=内置默认） |
| `referent_*` | 见指代设计文档 | 焦点表 / 预取 / 歧义策略 / 指代提示行 |
| `proactive_prompt_enable` | True | 「主动性」整块 |
| `proactive_env_prompt_enable` | True | 环境行 |
| `proactive_unresolved_prompt_enable` | True | `【未展开:…】` 语义行（需展开工具可用） |
| `proactive_env_intent_nudge` | True | 环境意图补强 |

## 6. 约定（改动时必须遵守）

1. 「主动性」仍然只有一块 system（`### 主动性`），`tests/test_llm_prompt_proactive.py` 锁着这条不变量。
2. 新增标记必须**可被解决**；标记格式与 `group_context._UNRESOLVED_RE`（摘要扫描）同源，改了要一起改。
3. 渲染层不引入新语法：预展开复用既有 `昵称(QQ)` 约定；标记不要写成 `[@123]` 形状
   （那是输出侧指令语法，见 `actions._AT_RE`，且 `strip_outbound_directives` 是全文替换）。
4. 无对应能力时提示词整行不注入（按 `available_tools` 裁剪）。
5. **默认不截断内容**：三个展开工具 `max_result=0`（不受全局 `TOOL_RESULT_MAX` 限制）、
   单条正文与转发条数均不设上限、摘要（`summarize_*`）也不裁剪——这些工具的任务就是
   "把骨架补成血肉"，截断等于把血肉再削掉一块（实测摘要被砍成「能花那么多时」导致答错）。
   需要保护上下文时从**候选数量**入手（`referent_prefetch_max`、`expand_recent` 的 `count`），
   或显式传 `limit`；不要恢复默认截断。
6. **合并转发的展开入口要哪个 id**：`get_forward_msg(id=...)` 要的是**承载转发的那条消息
   的 id**（消息自身的 id），**不是**转发节点内部的 `data.id`——后者是超长整型字符串
   （如 `7686537322889496857`），超出 int32 与 JS 安全整数范围，传进去会被 NapCat 拒为
   「1200 消息已过期或者为内层消息」。因此：
   - 渲染标记时由调用方把整条消息 id 传进来（`format_online_history` 取 `message_id`，
     `enhance` 取 `reply_id`）；
   - 取回时先用消息 id，失败才退回段内 id 兜底（`expand_message` 与 `expand_recent` 一致）；
   - 一律按**字符串**传（`IBot.get_forward_msg(id: str)`）。
7. 工具必须**如实汇报**：只取到发送者、正文仍是占位时不能算"翻到了"
   （`has_real_content` 判定 + `（这条只翻到一半）`/`（正文没拿到）` 措辞），
   否则模型会以为拿到了内容就直接作答。
8. **结果文本口吻**：第一人称"我看到的"，不写"已展开 N 项"这类汇总头；
   摘要抽取（`summarize_user` / `summarize_message`）必须与新格式同源，改了格式要一起改。
9. 位置敏感的风格约束（如「回应要求」）**不能靠新增 system 消息**——Anthropic/Gemini 会把
   中途 system 上提到最前面；要么拼进已有内容（tool 结果/user 文本），要么用 user 角色。

## 7. 验证

- 回归：`venv\Scripts\python.exe -m pytest -q`
- 单轮 prompt 全量：`ctx.state["debug_prompt"]`
- 调用率：`GET /agent/telemetry?bot_id=<qq>`（`tool_calls` / 耗时 / 成功率）
- 消融：`logs/prompt_ablation/user_context_*.json`（裸 ID vs 预展开的现成对照格式）
- 观测指标：prompt 里裸 `@数字` 的出现次数（应趋近 0）、`expand_*` 调用率、
  同一 id 的重复取回次数（应为 0）、兜底话术（"抱歉，我暂时无法回答"）出现率

## 8. 其他参考

- 指代消解设计：[docs/referent-resolution-design.md](referent-resolution-design.md)
- 长期记忆设计：[docs/memory-design.md](memory-design.md)
- 模块开发：[docs/MODULE_DEV.md](MODULE_DEV.md)
