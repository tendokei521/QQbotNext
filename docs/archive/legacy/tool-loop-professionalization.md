# Agent 工具循环专业化改造（对标 DSH）

> 状态：**方案稿（已被施工方案取代）**。本文只回答"差距在哪、为什么要改"；
> **怎么改、改到哪一步了**见 [`docs/tool-loop-refactor-plan.md`](tool-loop-refactor-plan.md)
> （P0–P6 已全部落地，看板与收尾核对在那一篇的 §7 / §8）。
> 正文保留当时的判断与证据，其中"现状"类描述已不再代表当前代码——例如工具循环现在
> 只剩 `app/agent/loop/tool_step.py` 一处、三个 provider 适配器只发单次请求。
> 基准实现：DeepSeek Harness（本地 `C:\Users\Admin\.dsh\profiles\node_modules\@deepseek-ai\*`，
> 下称 DSH）；引用格式 `包/lib/文件:行`。
> 范围：`app/agent/loop/`、`app/agent/tools/`、`app/agent/providers/` 的**调用约定与生命周期**，
> 不含 prompt 文案、不含业务模块（`app/plugins/llm_*`）。
> 相关：`docs/research/pipeline-interface-audit.md`（S29 / B-2）、`docs/context-expand-design.md`。

---

## 0. 一句话结论

**执行核心已经是专业水准，状态与生命周期还停在脚本水准。**

- 工具**执行**这一层（`tool_loop.normalize_and_execute_tool_calls`）该做对的都做对了：
  id 自洽、参数归一、并发、错误不静默、结果尾附回应要求 —— 剩下的差距都是"契约层"的：
  没有并发上限、没有结构化输出/错误码、超时靠强杀。
- 真正的差距在**循环之外**：谁拥有循环、一次轮次的状态怎么表达、取消怎么传播、
  历史是"每次有人想起来就写一笔"还是"从一份日志派生"。
- 具体表现：**6 处循环控制**、**12 处 assistant 历史写入点**、**0 个 `AbortSignal` 等价物**、
  **契约测试用源码字符串扫描**。

改造后预计：循环控制 6→1、历史写入 12→1、工具结果从 `str` 升级为结构化结果、
新增 1 条可在生产期开启的在线不变式、新增 3 个入口 × 2 种流的矩阵回归。
总工量约 **18 人日**，分 7 个可独立回滚的阶段（P0–P6），**每一阶段都能单独上线**。

---

## 1. DSH 的十一条硬规矩

下面每条都是"规矩 → DSH 证据 → 我们的现状 → 差距"。差距分三级：
**A = 缺能力**（做不到）、**B = 有但散**（做得到，靠纪律维持）、**C = 已达标**。

### R1 只有一个具象循环，插件只能订阅事件

- **DSH**：`dsh-agent-loop/README.zh.md:12` —— "它是 harness 唯一的具象循环——超出「调用模型、
  运行工具、重复」的所有内容都属于监听事件分类体系的插件"。适配器只提供 `stream()`，
  **循环在适配器之外**（`index.js:611 step()` 里 `llm.stream(request)` 拿到 chunk 流，
  工具调度由 `executeToolCalls` 负责）。
- **我们**：循环有 6 处 —— `providers/openai_compat.py:383`、`providers/anthropic.py:181`、
  `providers/gemini.py:204`（三个**适配器内部**各跑一遍工具循环）、`loop/chat.py:325`、
  `loop/turn.py:411`、`plugins/llm_proactive/stream.py:132`。
- **差距 A**。`docs/research/pipeline-interface-audit.md:471` 已把它记为 B-2 结构性阻塞。
  最刺眼的事实：**工具循环写在 provider 适配器里**，等于把编排职责下沉到了传输层。

### R2 会话日志是唯一真相，请求从日志派生

- **DSH**：每次 `session.append('tool/call' | 'tool/result' | 'assistant/message' | 'step/start' …)`
  都是带 seq 的持久事件；请求由 `session.deriveMessages()` 现算
  （`dsh-session/lib/types/surface.d.ts:64 deriveEventMessage` 是"逐节点投影规则"，
  `foldSurface` 重放整份日志得到模型可见面）。
  崩溃尾部有专门修复：`dsh-session/lib/types/repair.d.ts:9-21`
  （`TOOL_NOT_STARTED` / `TOOL_OUTCOME_UNKNOWN` / `interruptedTurnClosers`）。
- **我们**：`messages` 是一轮内的可变 list，在 6 处循环里就地 append；
  历史写入散落 **10 处**：`loop/pipeline.py:464`、`loop/chat.py:534`、`loop/chat.py:564`、
  `loop/assistant_output.py:126`、`loop/turn_input.py:215`、`loop/turn.py:576`、
  `plugins/llm_schedule/scheduler.py:365`、`:404`、`plugins/llm_proactive/manager.py:348`、`:404`。
  谁写、写多干净、写不写 `interrupted` 标记，各凭各处自觉。
- **差距 A**。这正是"流式修了、非流式没修"这类事故的土壤
  （见 `tests/architecture/test_turn_path_parity.py:8-16` 的事故记录）。

### R3 Turn / Step 两层状态机 + 显式停止原因

- **DSH**：`turn/end.reason` 是枚举 —— `completed` / `blocked` / `max-tokens` / `error` / `aborted`
  （`dsh-agent-loop/lib/index.js:521-610`）；step 是"一次请求 + 一轮工具"，
  turn 是"一个用户意图的生命周期"。停止条件由**协议语义**决定：
  模型不再要工具（`:691`）、工具声明 `concludesTurn`（`:186`）、`max-tokens`（`:689`）、
  `agent/turn-stopping` 钩子（`:570`）、取消（`:580`）、异常（`:587`）。
- **我们**：只有 `for _round in range(max_tool_rounds)` + `break`，停止原因只体现在日志文案里。
  一次对话的结局（正常结束 / 静默 / 兜底 / 超轮 / 流式失败）没有结构化表达，
  下游（遥测、记忆蒸馏、主动消息判定）只能靠猜字符串。
- **差距 A**。

### R4 工具并发是声明式分类 + 有界滚动池 + 模型序提交

- **DSH**：`ctx.tools.executionMode(exec)` 分类，**只认精确的 `true` 为 parallel，其余一律 exclusive**
  （fail-closed，`dsh-tools/lib/types/index.d.ts:689`）；独占调用构成排序屏障；并行安全调用
  走有界滚动池 `maxParallelToolCalls`（默认 10，`dsh-agent-loop/lib/types/constants.d.ts:5`）；
  **派发可以重叠，但策略、结果、结果上下文永远按模型顺序提交**（`index.js:164-274`）。
  取消时给未派发的调用补一条合成错误结果，保证重放合法（`:276-292`）。
  已知限制我们自己也要接受：**分类是一元的**（只能用"安全/不安全"两档）。
- **我们**：`tool_loop.py:157` 一个 `asyncio.gather` 全并发，**无上限**、无屏障、
  无"模型序提交"概念（gather 保序，但结果消息是执行完就写，不区分"已提交"与"已结算"）。
  模型一次展开 20 个 id 时，20 路并发 + 每个 20s 超时（`tools/spec.py:23`），
  与 provider 30s 请求超时正面冲突。
- **差距 B**（机制对了，边界没有）。

### R5 协作式取消 + 中断锚点

- **DSH**：每轮一个 `AbortController`，每个边界 `signal.throwIfAborted()`
  （`index.js:521-610` 里出现 8 次）；被中断的流**保留已交付前缀**并写
  `assistant/message` + `interrupted: true`（`:637-656`），"使下一次请求包含用户看到的内容"。
  工具侧：未派发的记 `ABORTED_BEFORE_DISPATCH`；已开始的**排空后再标 ABORTED**
  —— "取消从不抛弃已开始的 body"（`dsh-tools/lib/types/index.d.ts:718-731`）。
- **我们**：没有取消信号。`assembly/interrupt.py` 的 `interrupt_enabled` 只是把
  `runtime.interrupt_enabled` 置个标志位；`chat.py:428-432` 明确记下过"不能直接 return，
  否则用户已收到的正文不进历史"这次事故——修法是"走收尾逻辑"，不是"记中断锚点"。
- **差距 A**。

### R6 请求不可变 + 在线不变式

- **DSH**：请求 `deepFreeze`（`index.js:767`），且每次 `llm/stream` 前有一个**在线自检**：
  `dsh-agent-loop/lib/invariant.js:14-33` 断言"loop 造的请求必须冻结、必须带 session id、
  其 messages 必须等于日志派生结果、model/system/temperature/maxTokens/tools 必须等于折叠后的
  `request/header`"，任一条不成立就 `fail(...)`。这是**生产期也开着**的断言，不是测试。
- **我们**：有 `prompt_dump`（`assembly/dump.py`）能事后 diff，但没有任何"这事儿必须恒成立"的表达。
- **差距 A**，且这是把 R2 的保护真正落地的那一颗钉子——没有它，R2 只是设计意图。

### R7 生命周期扩展点代替配置开关

- **DSH**：钩子分三种粒度 —— `waterfall`（可改写，如 `agent/request` `:716`、
  `agent/pre-step` `:506`、`agent/request-error` `:660`）、`serial`（通知，如 `agent/turn-stopping` `:570`）、
  以及收件箱 steer/inject（`dsh-agent-loop/lib/types/agent.d.ts:34-38`）。
  重试是钩子的返回值：`agent/request-error` 返回 `{kind:'retry'}` 就重来这一步（`index.js:669`）。
  **配置只留给用户真正拥有的东西**：`maxParallelToolCalls` 单独开 settings 命名空间，
  `agents` 明确标注"boot-time 组合，存下来只会看起来生效"
  （`dsh-agent-loop/lib/types/index.d.ts:79-109`）。
- **我们**：181 个 agent 配置键里有相当一部分是"因为没接缝，所以只能做成开关"
  （`docs/research/pipeline-interface-audit.md:474` B-5 已定性）。
- **差距 A**。

### R8 工具结果是一等公民

- **DSH**：结果可以携带 `additionalContexts`（要注入下一步的用户消息）与
  `concludesTurn`（该结果宣告本轮结束），见 `dsh-tools/lib/types/index.d.ts:397-409`；
  失败是**结构化**的（`isError` + `error.info.code`），不是拼在字符串里。
- **我们**：`additionalContexts` 这个需求真实存在，但我们是用特例解决的 ——
  `ToolContext.extra["round_images"]` + `tools/round_images.py` + 每个循环里手写
  `_append_round_images(...)`（`chat.py:473`、`turn.py:351`、`stream.py:186`）。
  失败靠 `"error:"` 前缀字符串约定（`tools/spec.py:30`）。
- **差距 B**：能力有，但是"记住要在三个地方都调一下"。

### R9 包级不变式 + 官方 fixture：测试即契约

- **DSH**：每个包自带 `invariant.js` 注册到 `ctx.invariants`；
  `dsh-tools/lib/types/testing.d.ts` 提供**官方测试 fixture**（`defineContentToolFixture`）；
  事件词汇表由脚本生成并校验新鲜度（`dsh-session/lib/types/known-event-types.d.ts:1-21`，
  连"未知事件类型必须带 `ignorable` 标记"这种兼容规则都写进了类型注释与生成校验）。
- **我们**：`tests/architecture/test_turn_path_parity.py` 用**源码字符串扫描**断言
  "两条路径都调了某个函数名"。它抓得住"忘了调用"，抓不住"调了但语义不同"，
  且重命名/换写法就假通过或假失败。
- **差距 B**（方向对，强度不够）。

### R10 工具输出是「结构化数据 + 渲染器」，不是字符串

- **DSH**：`ToolDefinition` 强制要求 `output: { schema, render(args, value) }`
  （`dsh-tools/lib/types/index.d.ts:106-119`）：`execute` 只返回 **canonical JSON 值**，
  由 registry `snapshotToolValue`（无损快照）→ `validateJsonSchemaValue` → `render` → 深冻结。
  渲染器和 `presentCall/presentResult` 必须是**纯函数**，因为回放（replay）也会调它们。
- **我们**：`ToolHandler = (ctx, args) -> str`，一切皆字符串；错误靠 `"error:"` 前缀
  （`tools/spec.py:27,30`），成功结果靠拼接（`tool_loop.append_reply_directive` 往字符串尾部贴）。
- **差距 A**。代价是具体的：结果无法被结构化地截断/分页/spill（只能切字符）、
  无法按类型渲染、无法把"工具错误码"喂给重试/沙箱/回放逻辑。

### R11 超时与审批是「声明 + 独立策略强制」，且都返回**值**不是异常

- **DSH**：工具只声明 `timeoutMs`，registry **从不自己执行超时**
  （`dsh-tools` 明确 "the registry never enforces deadlines"）；由 `dsh-tool-call-timeout-policy`
  以 `tools/execute` 环绕瀑布实现，且**不 race、不弃置工具 promise、不 kill、不重试**——
  照常 `await next()`，只在自己计时器触发时把结果换成 `TOOL_TIMEOUT` 值
  （`Error: tool call timed out after Xms` + `error.info.code`）。
  审批同理：`tools/pre-execute` 返回 `{kind:'ask'}`，`ApprovalService` 判
  `allowed-once / rejected / cancelled / unavailable`，**无审批通道时 fail-closed 转 deny**，
  并落 `approval/asked` + `approval/decided` 审计事件；拒绝原因分四种文案喂回模型。
  三层门是**单调**的：guard 只能否决，不能把别人的 deny 翻回 allow。
- **我们**：`TOOL_TIMEOUT = 20` 是模块常量，靠 `asyncio.wait_for` 强杀协程
  （`tools/spec.py:23,213`）——语义上"放弃"了工具，而工具可能已经发出了一半的副作用。
  没有任何审批环节，权限是静态等级（这本身对 QQ 场景是对的），但**高危工具没有任何"临门一脚"**。
- **差距 B**。

---

## 2. 差距矩阵

**差距（A = 缺能力，B = 有但散）**

| # | 维度 | DSH | QQbotNext6 | 级别 |
|---|---|---|---|---|
| 1 | 循环归属 | 框架一处（loop-owned），适配器只流式 | 6 处，3 处在适配器内 | **A** |
| 2 | 历史真相 | append-only 事件日志 + `deriveMessages()` | 12 处直写 + 轮内可变 list | **A** |
| 3 | 停止语义 | `turn/end.reason` 枚举 | 无，只有日志文案 | **A** |
| 4 | 取消 | 每轮 AbortController，边界断言，中断锚点 | 只有开关标志位 | **A** |
| 5 | 请求不可变/自检 | `deepFreeze` + 在线不变式 | dump 事后看 | **A** |
| 6 | 扩展点 | waterfall / serial / inbox | 配置键 + 少量钩子 | **A** |
| 7 | 工具输出契约 | 强制 `output.schema` + `render()`，execute 返回 JSON 值 | handler 一律返回 `str`，错误靠 `"error:"` 前缀 | **A** |
| 8 | 工具并发 | 按**实参**分类 + 有界池 + 屏障 + 模型序提交 | 无界 `gather` | **B** |
| 9 | 超时语义 | 工具声明 `timeoutMs`，独立策略强制，超时是"值"、不弃置 promise | 模块常量 20s + `wait_for` 强杀 | **B** |
| 10 | 结果协议 | `additionalContexts` / `concludesTurn` / 结构化 error 码 | `extra["round_images"]` 特例 + 字符串约定 | **B** |
| 11 | 压缩正确性 | 阈值 0.8 / 保留 0.16 + 工具配对不变量 + 检查点括号 | 按**条数**切（今天切不到工具对，但 P4 后即成风险） | **B** |
| 12 | 契约测试 | invariant 插件 + 官方 fixture + 穷尽断言 | 源码字符串扫描 | **B** |
| 13 | 高危工具临门一脚 | `tools/pre-execute → ask` + 审批审计事件 + fail-closed | 只有静态权限等级 | **B**（可选） |

**我们领先的三处（改造时必须原样保留）**

| # | 维度 | DSH | QQbotNext6 |
|---|---|---|---|
| S1 | 轮次预算 | **无内置轮次预算**（README 明说，靠 `agent/turn-stopping` 兜） | `Failure` 分类 + `TurnBudget` + 回退链 |
| S2 | 领域语义 | 通用（无 QQ 概念） | 指代消解 / 未展开标记 / 群语境 / 发言通道 |
| S3 | 工具可用性判定 | 注册表 deny-by-default | `resolve_tool_state` 五层短路 + 7 原因码 |

> **不要妄自菲薄的三处（S1/S2/S3）**：它们是我们真正领先的地方，且都写在文档与测试里。
> DSH 的 README 自己承认"没有内置轮次预算"是**待办**，我们的 `TurnBudget`
> （`providers/policy.py:486-540`，把工具轮算"合法请求"、重试额度另算）比它成熟；
> DSH 的 token-meter 甚至**只做披露不做闸门**（`deriveTurnTokenUsage` 数据不全就返回
> `undefined`，与我们的"宁缺毋编"是同一条原则，但我们还多了一层硬预算）。
> 改造时这三块**必须原样保留**，它们不是债，是资产。

---

## 3. 目标架构

### 3.1 分层（改造后）

```
loop/
  turn_runner.py      ← 新增：Turn/Step 状态机 + TurnReason 枚举 + AbortScope
  tool_step.py        ← 新增：一次「请求 → 工具批次 → 回填」的原子步骤（唯一循环体）
  tool_loop.py        ← 保留：normalize_and_execute_tool_calls（执行核心，不动）
  session_log.py      ← 新增：append-only 事件日志 + derive_messages()（先包住现有 SessionManager）
  chat.py / turn.py   ← 变薄：只负责"取料 / 特殊发送语义"，循环交给 turn_runner
providers/
  base.py             ← 契约改为 stream_once() / chat_once()：**不得再持有循环**
  openai_compat.py    ← 删除内部 for 循环，chat() 退化为"一次请求"
```

### 3.2 核心对象

```python
class TurnReason(StrEnum):          # 对标 DSH turn/end.reason
    COMPLETED = "completed"; BLOCKED = "blocked"; MAX_TOKENS = "max-tokens"
    ERROR = "error"; ABORTED = "aborted"; SILENT = "silent"   # silent 是本项目产品语义

@dataclass
class StepOutcome:                  # 一步的结局（不含"怎么发出去"）
    kind: Literal["assistant", "tool_calls", "max_tokens", "error", "aborted"]
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    reason: str = ""

class AbortScope:                   # 对标 AbortController
    def cancel(self, cause: str) -> None: ...
    def throw_if_aborted(self) -> None: ...      # 每个边界都要调
```

### 3.3 一次 turn 的事件序列（对齐 R2/R3）

```
turn/start
  step/start → assistant/message(+chunks) → tool/call[] → tool/result[]   (×N 轮)
  step/start → assistant/message(interrupted=True, 已交付前缀)             (取消时)
turn/end(reason=…)
```

每个事件同时是**三种消费方的唯一来源**：写历史、发消息、记 usage。
`derive_messages()` 是唯一把事件折成 `messages` 的地方。

---

## 4. 分阶段计划

每个阶段都必须：`venv\Scripts\python.exe -m pytest -q` 全绿 + 该阶段验收测试通过 +
一步一提交（Conventional Commits）。

### P0 锁现状：把"源码扫描"换成"行为契约"（约 1 人日）

- **目标**：在动任何实现之前，先有一条能抓住"6 处循环行为漂移"的测试。
- **改动**：
  - 新增 `tests/agent/loop/test_tool_loop_contract.py`：用假 provider（返回预设 chunk 序列）
    驱动 **3 个入口**（`chat.stream_response` / `turn.run_turn` / `invoke llm_chat`）×
    **2 种模式**（流式 / 非流式）× **2 轮工具**，断言四件事：工具调用次数、回填顺序、
    `additionalContexts`（round_images）送达、`turn_completed` 广播一次。
  - 新增 `tests/architecture/test_no_tool_loop_in_providers.py`：先**标注**当前 3 个
    provider 内的循环为已知债（xfail/白名单），P1 完成后转为硬断言。
- **验收**：新测试在改动前全绿（证明它描述的是现实）；故意在某一入口删掉
  `_append_round_images` 时**至少一条**新测试变红。
- **风险**：低。纯新增文件。

### P1 工具循环收口：provider 只做单次请求（约 2 人日）

- **目标**：R1。循环只剩一处。
- **改动**：
  - `providers/base.py`：新增 `stream_once(request)` / `chat_once(request)`；`chat(call)` 保留为
    **兼容薄壳**，内部委托 `loop.tool_step`（第三方 `provider_invoker` 接管者的签名不变）。
  - 删除 `openai_compat.chat` / `anthropic.chat` / `gemini.chat` 里的 `for _round`，
    改调 `tool_step.run_tool_step(...)`。
  - `turn._stream_once` / `chat.stream_response` / `proactive.stream` 三处的 `for _round`
    改为调用同一个 `run_tool_step`，各自只保留"取料到哪 / 发出去怎么发"的差异。
- **验收**：P0 的契约测试全绿；`test_no_tool_loop_in_providers.py` 转正；
  `grep -c "range(max_tool_rounds)" app/` == 1。
- **风险**：中（动 6 个文件的主力路径）。回滚点：只删一个 provider 的循环就会暴露问题，
  因此**按 provider 逐个提交**（3 个小提交）。

### P2 Turn/Step 状态机 + 取消语义（约 3 人日）

- **目标**：R3 + R5。
- **改动**：
  - 新增 `loop/turn_runner.py`：`TurnReason` / `StepOutcome` / `AbortScope`，
    `while utils` 改成显式状态机；`chat.stream_response` 与 `turn.run_turn` 都收敛到它。
  - 每个 await 边界插 `abort.throw_if_aborted()`（流式 chunk 循环内、每次工具派发前后）。
  - 取消时写 `assistant/message(interrupted=True, 已交付前缀)`——把 `chat.py:428-432`
    那次事故的修法**升级为约定**：不再是"记得走收尾"，而是"中断锚点由 runner 负责写"。
  - `turn/end(reason=…)` 落库，遥测与记忆蒸馏改读 `reason` 而不是猜文案。
- **验收**：新增 `test_turn_reason_matrix.py`（正常/静默/超轮/流式失败/取消 五条路径
  都产出预期 `TurnReason`）；新增 `test_interrupt_prefix_is_in_history.py`
  （中途取消后，历史里必须有用户已看到的那半句）。
- **风险**：中高（取消语义容易出竞态）。保守做法：`AbortScope` 只做**协作式**检查
  （比 `task.cancel()` 安全），先不做线程级强杀。

### P3 工具执行契约：并发分类 + 有界池 + 超时策略（约 3 人日）

- **目标**：R4 + R11。
- **改动**：
  - `ToolSpec` 增 `concurrency`（`"parallel" | "exclusive"`，默认 **exclusive，fail-closed**）
    并允许按**实参**升级为独占：`concurrency_is_safe(args) -> bool`，与 DSH 的
    `isConcurrencySafe(args)` 同形。OneBot 的 `send_*` / `delete_*` / 群管理类一律 exclusive，
    `expand_*` / `get_*` 标 parallel。
  - `tool_loop` 的 `asyncio.gather` 换成**有界滚动池**（`asyncio.Semaphore`，
    配置 `tool_parallel_max` 默认 8，`1` = 串行），独占调用构成屏障（等池排空再单独跑），
    结果仍按**模型序**提交（当前 gather 已保序，需显式化并用测试锁住）。
  - 超时从"模块常量 + 强杀"改成"**声明 + 策略**"：`ToolSpec.timeout_s` 声明在工具上
    （默认沿用 20），执行器不再 `wait_for` 硬取消协程，而是**等待工具自己收敛**，
    超时只把结果替换成 `error: 工具 X 超时（Ns）`。副作用型工具（OneBot 群管理）
    改为 self-timeout（内部 `asyncio.timeout`）+ 结果值上报，避免"半发出去"。
- **验收**：现有 `tests/agent/tools/test_llm_tool_loop.py` 13 例全绿；新增 3 例：
  ①并行上限被遵守（同时最多 N 个在跑）②独占调用前后不发生重叠
  ③超时工具返回 `error:` 值且不残留未捕获的 task 异常。
- **风险**：中（超时语义从"强杀"改"等待收敛"，要确认没有工具真的会永久挂住——
  对 OneBot API 调用需先确认底层有 `aiohttp` 级超时兜底）。

### P4 会话日志单一真相 + 请求不可变 + 在线不变式（约 4 人日）

- **目标**：R2 + R6。这是最贵的一步，也是把前四步锁死的一步。
- **改动**：
  - 新增 `session/session_log.py`：`append(type, **payload)` + `derive_messages()`，
    先**包住**现有 `SessionManager.add_message`（不改存储格式），把 12 处写入口收成 1 个。
  - 历史文件加事件类型字段（向后兼容读旧格式：无 type 的一律当 `assistant/message`）。
  - `prompt_dump` 升级为**在线不变式**：`prompt_dump_verify` 开启时，每次请求前断言
    `request.messages == log.derive_messages()`，不一致就 `logger.error` + 落盘 diff
    （对标 `dsh-agent-loop/lib/invariant.js`，但默认只在 debug 构建/开启时生效，避免生产开销）。
  - 停止在"请求不可变"这半边：Python 侧只做 `MappingProxyType` 浅冻结 + 派发前快照，
    不做 DSH 那种全量 `deepFreeze`（收益/成本比不划算）。
- **验收**：`grep -rn "add_message(" app/ | wc -l` 从 10 降到 1（只剩 session_log 内部）；
  新增 `test_request_equals_log_derivation.py`；全部现有测试绿。
- **风险**：高（动历史格式与 12 个写点）。**必须先做迁移脚本 + 备份**，
  并在 P4 之前把 P0 的契约测试跑成硬门禁。

### P5 工具输出契约 + spill + 压缩保配对（约 3 人日）

- **目标**：R10 + R8 的收口 + 压缩正确性（差距 #7 / #11）。
- **改动**：
  - 工具结果从 `str` 升级为 `ToolResult{content: list[block], is_error: bool, error_code: str,
    meta: dict}`：`error:` 前缀约定退役，改用稳定错误码
    （`UNKNOWN_TOOL` / `INVALID_ARGS` / `TOOL_TIMEOUT` / `TOOL_UNAVAILABLE` / `ABORTED`）。
    这一步**只改协议、不改每个工具的实现细节**：先加适配层把 `str` 包成单 text block，
    再逐个工具迁移。
  - 大结果走 **spill 而非截断**：超过 `TOOL_RESULT_MAX`（`tools/spec.py:25`，2000 字）时
    全文落盘 `data/llm/<账号>/spill/<session>/`，模型只看到 head/tail 预览 + 路径 +
    "用 read/grep 取回"。对齐 DSH：**spill 失败绝不把成功调用变成 error**（best-effort），
    且跳过"取回类"工具自身（避免 read→spill→read 死循环）。
    > 这条对本项目格外重要：「消息环境补全」类工具正是靠长正文工作的，
    > 2000 字截断会砍掉要找的那一段。
  - `assembly/compress.py:24` 的压缩按**条数**切，可能把 `assistant(tool_calls)` 与紧随的
    `role:tool` 切开 → provider 400。改为按"轮 + 工具配对"切：切点必须满足
    "此前 tool_call 数 == tool_result 数"，并加断言（对标
    `dsh-compaction/lib/tool-pairing.js` 的 `toolPairingBalancedBefore`）。
    顺带把阈值从"条数"升级为"估算 token × 0.8 / 保留 0.16"。
- **验收**：新增 `test_compress_keeps_tool_pairing.py`（构造会被切断的历史，断言不产出非法请求）、
  `test_spill_roundtrip.py`（大结果落盘→模型可见预览→按路径可取回）、
  `test_tool_error_codes.py`（错误码稳定，且 `tool_call_hook` 能读到）。
- **风险**：低到中。spill 是纯增益；错误码迁移要保证 `is_error_result` 的旧调用点同步更新。

### P6 收尾：配置→扩展点 + 高危工具审批（约 2 人日）

- **目标**：R7 的试点 + R11 的审批半边。
- **改动**：
  - 把 `stream_output` / `speech_via_tool` / `context_expand_enable` 这类
    "因为没接缝只能做开关"的键，改成能力声明 + 策略对象（先在 3 个键上试点）。
  - 高危 OneBot 工具（`sensitivity in {high, critical}`，已在 `manifest.py:39-55` 定义）
    加"临门一脚"：开关 `tool_approval_enable` 打开时，执行前向 **Bot 拥有者**发一条确认
    （走已有 `ask_user` 式通道或群内确认），结果集合固定为
    `allowed_once / rejected / timeout`，**无通道时 fail-closed 转拒绝**，
    拒绝原因分文案喂回模型（"用户拒绝" vs "超时未响应"）。
    审计落日志 + 遥测，不进模型 transcript。
- **验收**：新增 `test_tool_approval_fail_closed.py`（无通道 → 拒绝）、
  `test_tool_approval_audit.py`（asked/decided 成对落库）。
- **风险**：低（默认关）。**QQ 场景的产品判断**：群管理类工具在群聊里弹确认会打断对话，
  因此默认关闭，只给"危险全权"账号用。

---

## 5. 明确不照抄（取舍）

| DSH 做法 | 我们为什么不做 |
|---|---|
| Cordis DI / Service / fiber 生命周期 | 我们是 Python + 插件注册表，引入一套 DI 框架的收益远小于迁移成本 |
| 全量 event sourcing（含 surface `replace` / 折叠重放 / 崩溃修复） | P4 只做 append-only + 派生；`replace` 语义要等压缩真需要时再谈 |
| TS 类型生成的工具 schema（`py-types.js`） | 我们已有 `ToolSpec.parameters` JSON Schema + WebUI 表单，重复建设 |
| 工具的**渲染器**（`output.render` / `presentCall` / `ToolCallView` 卡片） | 我们直接把 text block 交给 QQ 消息通道，没有"UI 卡片"这层消费者；只要 schema + 结构化错误码 |
| 独立的 token 计量服务（`dsh-token-meter`） | 我们已有 `usage/` + `TurnBudget` + 上游 usage 直取；再叠一层估算器只会产生"两套 token 数" |
| 两级剪裁（`ToolResultPruner` 8192 字符 + `spill` 50000 字节） | 只取 spill 一档；能取回就不该剪，剪与不剪之间不需要中间态 |
| 群聊全量审批（每次高危工具都 ask） | 会打断群聊节奏；我们做成**可选开关 + 默认关**，只给"危险全权"账号开 |
| PTC（programmatic tool calling，`run_code` 传输） | 是能力扩张（模型写代码调工具），与我们"群聊里少调几次工具"的目标相反 |
| 无内置轮次预算 | 我们**必须有** `max_tool_rounds`（QQ 场景要控成本/控延迟），只是要把它从硬编码升级为 policy |
| `deepFreeze` 全量冻结 | Python 侧做浅冻结 + 快照即可，全量深拷贝每轮上下文成本过高 |

---

## 6. 验收口径（全部可执行）

1. `venv\Scripts\python.exe -m pytest -q` 全绿；`ruff check .` 无新增违规。
2. 四条机器可验证的收敛指标：
   - `grep -c "range(max_tool_rounds)" app/` == **1**
   - `grep -rn "add_message(" app/` 只命中 `session/session_log.py` == **1**
   - 工具结果不再靠 `"error:"` 前缀判错（`is_error_result` 退役）
   - `resolve_tool_state` 仍是唯一工具可用性判定点（现有
     `tests/architecture/test_tool_state_single_source.py` 继续绿）
3. 反事故测试三件套（P0/P2/P4 各一条），且在**故意回退**对应修复时必须变红。
4. 性能不退化：一轮两工具调用的端到端 p50 延迟与改造前相差 < 10%（用 `prompt_dump` +
   `telemetry` 前后对比，样本 ≥ 30 轮）。
5. 每一阶段单独上线：P0–P6 各自完成后 `pytest -q` 全绿即可发布，不要求整条链做完。

---

## 7. 风险与回滚

| 风险 | 触发迹象 | 回滚 |
|---|---|---|
| P1 收口后某 provider 行为变了 | 契约测试红 / 某 provider 的工具结果 order 变化 | 每个 provider 独立小提交，单独 `git revert` |
| P2 取消竞态导致消息双发 | `send_hook` 里同一 session 出现重复 message_id | `AbortScope` 退回纯标志位（保留 `TurnReason`） |
| P4 历史迁移丢数据 | 迁移脚本 dry-run 与实跑条数不一致 | 迁移脚本强制 `--dry-run` 先跑 + 自动备份（沿用
`scripts/migrate_v3_*.py` 的既有做法） |
| 在线不变式误报刷屏 | `errors.log` 出现大量 derivation desync | `prompt_dump_verify` 默认关，只在排查时开 |

---

## 8. 附录：DSH 关键符号速查

| 概念 | 符号 | 位置 |
|---|---|---|
| 一步的工具调度 | `executeToolCalls` / `runGroup` | `dsh-agent-loop/lib/index.js:119,164` |
| 取消后补合成结果 | `appendSkippedToolCall` | `:276` |
| 轮次状态机 | `turn()` / `step()` | `:521,611` |
| 请求折叠 + 冻结 | `buildRequest` / `deepFreeze` | `:700,767` |
| 在线不变式 | `install`（订阅 `llm/stream`） | `dsh-agent-loop/lib/invariant.js:14` |
| 并发分类（按实参） | `executionMode` → `isConcurrencySafe(args)`（只认精确 `true`） | `dsh-tools/lib/types/index.d.ts:689` |
| 工具流水线 | pre-policy → guards(单调) → around → post → finalize | `:718-731` |
| 工具输出契约 | `output: { schema, render }`，execute 返回 JSON 值 | `:106-119` |
| 结果扩展 | `additionalContexts`（FIFO 注入下一步）/ `concludesTurn` | `:397-409` |
| 错误码即契约 | `UNKNOWN_TOOL` / `INVALID_ARGS` / `TOOL_TIMEOUT` / `ABORTED*` | `dsh-tools/lib/types/index.d.ts` |
| 超时策略 | `timeout-policy` 环绕瀑布，不 race 不弃置 | `dsh-tool-call-timeout-policy/lib/index.js` |
| 审批 | `ask`/`never` + `allowed-once/rejected/cancelled/unavailable` + `approval/asked,decided` | `dsh-user-approval/lib/types/index.d.ts` |
| 大结果 | `spill-policy`：head/tail 预览 + locator + best-effort | `dsh-spill-policy` / `dsh-spill-local` |
| 压缩配对不变量 | `toolPairingBalancedBefore` / `validateSurfaceRegion` | `dsh-compaction/lib/tool-pairing.js` |
| 压缩阈值 | `thresholdRatio 0.8` / `retainRatio 0.16` | `dsh-compaction-basic` |
| 轮次用量归属 | `deriveTurnTokenUsage`（数据不全就返回 `undefined`） | `dsh-token-meter/lib/turn-usage.js` |
| 日志→消息投影 | `deriveEventMessage` / `foldSurface` | `dsh-session/lib/types/surface.d.ts:64,96` |
| 崩溃修复 | `interruptedTurnClosers` | `dsh-session/lib/types/repair.d.ts:21` |
| 收件箱 steer | `Inbox.claim` / `nextStep` / `nextTurn` | `dsh-agent/lib/types/inbox.d.ts:25-40` |
| 消耗核算 | `foldConsumedWork` | `dsh-agent/lib/types/consumed-work.d.ts:38` |
| 并行上限默认值 | `DEFAULT_MAX_PARALLEL_TOOL_CALLS = 10` | `dsh-agent-loop/lib/types/constants.d.ts:5` |
