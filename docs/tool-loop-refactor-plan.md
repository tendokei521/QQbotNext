# Agent 工具循环改造施工方案（P0–P6）

> 状态：**施工中**（P0 待开工）。
> 上位文档：[`tool-loop-professionalization.md`](tool-loop-professionalization.md)（差距分析与取舍，
> 这里不重复"为什么"，只写"怎么改"）。
> 基准：DSH `@deepseek-ai/*`（本地 `~/.dsh/profiles/node_modules/`）。
> 读者：动手改代码的人。文末 §7 是进度看板，每完成一步就勾掉。

---

## 0. 施工总则

1. **每阶段可独立上线、独立回滚**。P0–P6 之间是"建议顺序"，不是"必须一次做完"。
2. **一步一提交**（AGENTS.md）。每阶段的提交拆分见各阶段末的"提交"小节，提交信息用
   Conventional Commits，**不混入无关改动**。
3. **验证分档**（AGENTS.md）：
   - 只动文档 → 不跑测试；
   - 动单个模块/函数 → 跑相关测试文件 `-q`；
   - 阶段收尾（跨层/多模块）→ 全量 `venv\Scripts\python.exe -m pytest -q` 全绿 + `ruff check .` 无新增违规。
   - **报告必须写清"跑了什么、没跑什么"**。
4. **每次改动前后都能回答"这条不变量由哪个测试守着"**。没有测试的改动不算完成。
5. **不许留半截**：`except Exception: pass`、`# TODO 稍后` 一律不接受（仓库已有
   `tests/architecture/test_no_silent_except.py` 在守）。

---

## 1. 全局约定（先定，后面全部引用）

### 1.1 `TurnReason`（一轮对话的结局，替代"猜日志文案"）

| 值 | 含义 | 触发点 |
|---|---|---|
| `completed` | 正常发言结束 | 模型不再要工具且产出了文本 |
| `silent` | 主动选择不说 | 发言通道无入稿 / 纯文本为空（**本项目的产品语义**，DSH 没有） |
| `blocked` | 被钩子拦下，没请求模型 | `post_assemble` 置空、`job.skip` |
| `max_rounds` | 工具轮耗尽 | `for` 循环走到上限 |
| `max_tokens` | 输出被 max_tokens 截断 | `finish_reason == "length"` 且无正文 |
| `error` | 请求/执行失败 | provider 错误、工具全崩、异常 |
| `aborted` | 被取消 | `AbortScope.cancel()` |

> 落库位置：`TurnResult.reason`、会话事件 `turn/end.reason`、遥测 `TurnRecord.reason`。
> 消费者：记忆蒸馏（只蒸馏 `completed`）、主动消息（统计 `silent`）、可观测性面板。

### 1.2 工具错误码（替代 `"error:"` 字符串前缀）

| 码 | 何时 |
|---|---|
| `UNKNOWN_TOOL` | 名字不在本轮清单里 |
| `INVALID_ARGS` | 参数不是合法 JSON 对象，或不符合 schema |
| `TOOL_TIMEOUT` | 超过该工具声明的 `timeout_s` |
| `TOOL_UNAVAILABLE` | 执行前复核发现已不可用（模块关掉/被禁用） |
| `PERMISSION_DENIED` | 权限/作用域不足 |
| `ABORTED_BEFORE_DISPATCH` | 取消时尚未开始 |
| `ABORTED` | 取消时已开始（结果被替换） |
| `TOOL_EXCEPTION` | 处理器抛异常 |
| `OUTPUT_LIMIT` | 结果超限且 spill 不可用 |

**兼容期**：`ToolResult.is_error` 之外，`is_error_result(text)` 保留一版双读
（`text.lstrip().startswith("error:")` 或 `result.is_error`），下个大版本删。

### 1.3 会话事件类型（P4 用）

| 事件 | 是否进 `messages`（surface-eligible） | 说明 |
|---|---|---|
| `user/message` | ✅ | 用户发言 |
| `assistant/message` | ✅ | Bot 发言（含 `interrupted` 标记） |
| `turn/start` `turn/end` | ❌ log-only | 轮次边界 + reason |
| `step/start` `step/end` | ❌ log-only | 一次请求的生命周期 |
| `tool/call` `tool/result` | ❌ log-only（**v1 不进模型**） | 保留当前"工具结果只在当轮"的语义 |
| `request/header` | ❌ log-only | 本轮真实的 model / system / tools 指纹 |
| `usage` | ❌ log-only | 上游回报的用量 |

> 关键决策：**"日志记全部，投影只取一部分"**（DSH 的 `isSurfaceEligibleType` 同款）。
> 这样 P4 拿到单一真相，但**不改变模型看到的历史**——工具结果是否要跨轮持久化是**另一个**产品决策，
> 留到 P5 之后再谈。

### 1.4 新增钩子阶段（必须同步三处）

新增 `turn_started` / `step_completed` / `turn_stopping` 时，同步改：
1. `app/agent/hooks.py` 的阶段清单 docstring；
2. `tests/architecture/test_extensibility_contract.py:51` 的 `DECLARED_STAGES`；
3. `docs/MODULE_DEV.md` 的钩子表。

### 1.5 新增配置键（必须登记）

`app/core/env_template.py`（环境变量）、`app/agent/config_schema.py`（表单 + 分组）、
`app/agent/config_partition.py`（归属模块/本体）、`app/agent/config.py`（默认值）。
漏一处会被 `tests/core/test_env_template.py` 或 `test_config_partition.py` 拦下。

**本次新增键（全表）**

| 键 | 默认 | 阶段 | 归属 |
|---|---|---|---|
| `tool_parallel_max` | 8 | P3 | 本体（高级功能） |
| `tool_timeout_default_s` | 20 | P3 | 本体（高级功能） |
| `tool_spill_enable` | True | P5 | 本体（高级功能） |
| `tool_spill_max_bytes` | 50000 | P5 | 本体（高级功能） |
| `prompt_dump_verify` | False | P4 | 本体（会话/压缩） |
| `tool_approval_enable` | False | P6 | 本体（权限管理） |

---

## 2. 不可破坏的对外契约（兼容矩阵）

动手前先认这些"用户看得见"的东西，改坏了就是破坏性变更：

| 契约 | 位置 | 谁在用 | 本次如何处理 |
|---|---|---|---|
| `from app.agent import tool, skill, ToolContext, LlmContext, ToolCallContext` | `app/agent/__init__.py` | 全部业务插件 | **签名不动**；`@tool` 增加可选参数（默认值保持旧行为） |
| `@llm_hook(stage)` 的 10 个阶段 | `hooks.py` | `llm_*` 模块 | 只增不改；新增阶段需登记（§1.4） |
| `supersedes=("assembly",)` / `("provider_invoker",)` | `app/plugin/features` | `astrbot_alignment` 等 | `build(req)` / `chat()` / `stream()` 签名不动；P1 后 `chat()` 的**入参**不变，只是它内部不再循环 |
| `BaseModule.llm_chat(...)` | `app/plugin/` | 业务模块 | 签名不动（`max_tool_rounds` 仍生效，改由 loop 层执行） |
| `ToolHandler = (ctx, args) -> str` | `tools/spec.py` | 全部工具 | P5 改为返回 `ToolResult`，**过渡期**执行器自动把 `str` 包成 `ToolResult` |
| `/agent/telemetry`、`/agent/prompt-dumps` | `app/webui/api` | Dashboard | 只加字段不删字段 |
| 磁盘 `<session>.json`、`data/app.db` | `session/manager.py` | 用户数据 | 只加字段；旧文件必须能读（§P4 有迁移脚本） |
| `provider_invoker` 的 `supersedes` 语义 | `providers/invoker.py` | 第三方 | 不动 |

---

## 3. 阶段详表

### P0 行为契约测试：先把"现在是什么样"焊死

**目标不变量**：对 3 个入口 × 2 种模式 × 2 轮工具，工具执行次数、回填顺序、
补图送达、`turn_completed` 广播次数**都相同**。改之前先让测试绿，改之后让测试继续绿。

**依赖**：无。

**改动**

| 文件 | 改什么 |
|---|---|
| `tests/agent/loop/conftest.py`（新） | 假 invoker / 假 runtime 两个 fixture（**直接复用** `tests/agent/loop/test_run_turn.py:96-136` 里已经被验证过的 `_Config`/`_Invoker`/`_Telemetry`/`_runtime` + `patched_prepare` 写法，不要另创一套） |
| `tests/agent/loop/test_tool_loop_contract.py`（新） | 契约矩阵（下文） |
| `tests/architecture/test_turn_path_parity.py` | **P1 删除**源码扫描（它锁的是"某个函数名出现过"，函数搬家后必假红）。P0 先用行为测试取代它（本文件第 2 行），P1 搬函数时再删，避免提前失去守卫 |
| `tests/architecture/test_no_tool_loop_in_providers.py`（新） | 白名单登记 3 个 provider 内的 `for _round`（现在 xfail），P1 完成后清空白名单转硬断言 |
| `app/agent/tools/state.py:249` | **顺手修真 bug**：这里调 `spec.allowed_for(event, scope=...)`，而 `ToolSpec` 上只有 `allows()`（`spec.py:113`）；异常被 `:251-252` 吞掉，导致判定链第 ⑤ 层 `permission_denied` **从未生效**。改为 `spec.allows(ctx)` 的等价实现，并补一条测试 |

> 第 4 行是这次盘点**顺带发现**的真 bug（不是重构项）：权限预判一直在静默失效。
> 它足够小、足够独立，放在 P0 一起修最划算——但**必须单独一个提交**。


**假 provider 设计**（关键，后面所有阶段都用它）

```python
# tests/agent/loop/conftest.py
class ScriptedProvider:
    """按脚本产出响应：每个元素要么是文本 chunk 序列，要么是 tool_call 序列。"""
    def __init__(self, script: list[list[StreamEvent]]): ...
    async def stream(self, *, runtime, chain, messages, params): ...   # 记录收到的 messages
    async def chat(self, *, runtime, chain, messages, params) -> LLMResponse: ...
    @property
    def seen(self) -> list[list[dict]]:   # 每次调用实际收到的 messages 快照
```

注入方式：`monkeypatch.setattr("app.agent.loop.turn.resolve_invoker", lambda rt: fake)`，
以及 `chat.py` / `providers/__init__.py` 里的同名符号。

**契约断言（每个组合都跑）**

1. 假 provider 收到 **3 次** 请求（1 次原始 + 2 次工具回填）；
2. 第 2 次请求的尾部严格是 `assistant(tool_calls=c0) → role:tool(c0结果)`，
   第 3 次是 `… → assistant(tool_calls=c1) → role:tool(c1结果)`；
3. `tool_calls[i].id` 与 `role:tool[i].tool_call_id` 一一对应且非空；
4. 工具执行顺序 == 模型给出顺序（`calls == ["a","b"]`）；
5. `turn_completed` 钩子**恰好一次**；
6. 工具往 `tool_ctx.extra["round_images"]` 塞一张图时，视觉模型的第 2 次请求里
   出现一条含 image 块的 user 消息；文本模型不出现。

**验收**

```bash
venv\Scripts\python.exe -m pytest tests/agent/loop/test_tool_loop_contract.py -q
venv\Scripts\python.exe -m pytest tests/architecture/test_no_tool_loop_in_providers.py -q
```

外加一次**手工变异检查**（写进 PR 说明）：临时注释掉 `chat.py` 里的
`_append_round_images(...)`，重跑，**必须至少一条变红**；确认后恢复。

**提交**：2 个 ——
1. `fix(tools): restore the permission gate in the tool-state chain`（独立，先做）
2. `test(agent): lock the tool-loop contract across three entry points`

**回滚**：纯新增文件，删除即可。

**工作量**：1 人日。

---

### P1 工具循环收口：provider 只做单次请求

**目标不变量**：全仓库只有 **1 处** 工具轮循环；3 个 provider 适配器里再也找不到
`for _round`；`grep -c "range(max_tool_rounds)" app/` == 1。

**依赖**：P0（没有契约测试不许动这块）。

**改动**

1. **新增 `app/agent/loop/tool_step.py`**（唯一循环体）：

```python
@dataclass
class StepRequest:
    runtime: Any
    chain: list[dict]
    messages: list[dict]            # 就地追加（与今天一致，P4 才改成快照）
    params: dict                    # model/temperature/max_tokens/tools/tool_executor/...
    max_rounds: int
    tool_ctx: Any = None
    modalities: Any = None
    # 三个注入点：这也是六个循环点的全部差异所在
    on_text: Callable[[str], Awaitable[None]] | None = None        # 逐句（流式）
    on_round_end: Callable[[dict, list[dict]], Awaitable[None]] | None = None  # (round_text, tool_results)
    abort: Any = None               # P2 起生效；P1 先留 None

@dataclass
class StepResult:
    kind: Literal["assistant", "max_tokens", "error", "aborted", "empty"]
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    finish_reason: str = ""
    error: str = ""
    usage: dict = field(default_factory=dict)
    rounds: int = 0

async def run_stream_step(req: StepRequest) -> StepResult: ...
async def run_blocking_step(req: StepRequest) -> StepResult: ...
```

   `run_stream_step` 内部保留今天 `chat.stream_response` 的全部细节：碎片累积、
   `normalize_and_execute_tool_calls`、`append_round_images`、空回复重试、
   思考吃满预算的收敛轮、轮数上限。`run_blocking_step` 同理，但调 `invoker.chat()`。

2. **改造 6 个循环点**（逐个提交，一次一个）：

| 文件 | 改成 |
|---|---|
| `loop/turn.py:_stream_once` | `on_text=lambda s: on_sentence(s)`，其余删 |
| `loop/chat.py:stream_response` | `on_text` 里做切句 + `yield` + 写历史；`on_round_end` 里做发言通道 flush + speak reminder |
| `plugins/llm_proactive/stream.py` | `on_text` 里 `pool.put(...)` |
| `loop/turn.py:_chat_once` | `run_blocking_step` |
| `providers/openai_compat.py` | 删内部循环，`chat()` 只发一次请求；保留 `tools` 入参（转发给模型） |
| `providers/anthropic.py` / `gemini.py` | 同上 |

3. **产出通道抽象（这是收口的核心难点）**。现在三个入口的"产出"是三种互斥形态：
   `chat.stream_response` 是 **async generator**（`yield` 句子/气泡，且 `pipeline.py:387,422-438`
   靠 `should_stop` 在**句间**打断、`finally` 里 `_save_sent_history`）；
   `turn._stream_once` 用 `await on_sentence(text)`；`proactive/stream.py` 用 `await pool.put(Message)`。
   → `StepRequest` 的 `on_text` 必须同时能承载三者：generator 侧用 `asyncio.Queue` 把
   `on_text` 转回 yield（或改成"runner 产出 → 外层 `async for` 消费"），
   **并且要保住"暂停在句间"的语义**，否则 `should_stop` 打断会延后一整轮。

4. **round 边界抽象**。现在四处不一样：chat 每轮 `_flush_speech` + `SPEAK_REMINDER`（`chat.py:478-491`）；
   turn / proactive 只 `_append_round_images`；provider 层只回填 messages。
   → `StepRequest.on_round_end(round_index, rounds_left, tool_results)`，flush 出来的
   `Message` 通过同一个产出通道送出去。

5. **统一 params 键集（顺手修两个静默缺陷）**：
   - `invoker.py` 的 `STREAM_PARAMS` **不含 `max_tool_rounds`**，而 `turn._call_params` 传了它
     → 一直被静默丢弃（"配了没用"）；
   - `proactive/stream.py:140-149` **完全不传 `budget`** → 主动消息/定时任务路径没有
     `TurnBudget` 闸门（最贵的一条路径反而没人管）；
   - `assistant_output._force_speak_round` 硬编码 `max_tool_rounds=2`。
   → 统一成 runner 的一处入参，并让 `STREAM_PARAMS`/`CHAT_PARAMS` 对齐。

6. **reasoning 溢出重试留在调用方，不下沉**。三层语义本就不同
   （流式 `chat.py:437-450` / 非流式 `turn.py:336-346` / provider 明确"不在这里重试"
   `providers/__init__.py:548-553`）。下沉会把单次重试叠成双重。

7. **兼容壳**：`providers/__init__.py:chat_with_fallback(..., tools=, tool_executor=, max_tool_rounds=)`
   与 `client.llm_chat(...)` 保持签名，内部改调 `run_blocking_step`。
   这样"绕开 loop 直接调 provider 跑工具循环"的第三方代码仍然工作。
   同时删掉 `tests/architecture/test_turn_path_parity.py` 的源码扫描（P0 已用行为测试取代）。

**验收**

```bash
grep -rn "range(max_tool_rounds)" app/            # 期望：只有 loop/tool_step.py 一行
venv\Scripts\python.exe -m pytest tests/agent -q
venv\Scripts\python.exe -m pytest tests/architecture/test_no_tool_loop_in_providers.py -q   # 白名单清空
```

外加 P0 契约测试全绿。

**提交**（5 个，便于单独 revert）：
1. `feat(agent): add the shared tool-step runner`（新增文件 + 单测）
2. `refactor(agent): drive the streaming turn through the shared step runner`
3. `refactor(agent): drive the proactive stream through the shared step runner`
4. `refactor(agent): driving blocking turns through the shared step runner`
5. `refactor(providers)!: make adapters single-request; the loop now lives in agent/loop`
   > 带 `!` 是因为"适配器不再自己跑工具循环"对 `provider_invoker` 接管者是可见变化；
   > 文档同步 `docs/MODULE_DEV.md` §4.7。

**回滚**：按提交粒度 `git revert`，最多回到"provider 内仍有循环"的旧态。

**工作量**：2 人日。

---

### P2 Turn/Step 状态机 + 取消语义

**目标不变量**：每一轮都产出**结构化结局**（`TurnReason`）；被取消的轮次在历史里留下
"用户已看到的前缀"；`turn/end` 事件与遥测里能读到 reason；取消不会丢历史、不会双发。

**依赖**：P1（循环只有一个才好接管）。

**改动**

1. **新增 `app/agent/loop/turn_runner.py`**：

```python
class AbortScope:
    """协作式取消：只置标志，边界处 assert 抛出。比 task.cancel() 安全。"""
    def __init__(self) -> None: self._cause: str = ""
    def cancel(self, cause: str) -> None: ...
    @property
    def aborted(self) -> bool: ...
    def throw_if_aborted(self) -> None: raise TurnAborted(self._cause) if aborted

class TurnRunner:
    def __init__(self, runtime, limits: TurnLimits): ...
    async def run(self, req: TurnRequest, *, on_sentence=None) -> TurnResult: ...
```

   `TurnResult` 增加 `reason: TurnReason`、`interrupted: bool`、`rounds: int`（旧字段全保留）。

2. **取消接线**：`LlmPipeline.interrupt_session(session_id)`（`pipeline.py:113`）从
   "标记 stale" 升级为"找到活跃 turn 的 `AbortScope` 并 cancel"，保留 generation 语义。
   在每个 await 边界插 `abort.throw_if_aborted()`：流式 chunk 循环内、每次工具派发前后、
   每次请求前。

3. **中断锚点**：`TurnAborted` 冒泡到 runner 时，
   - 已产生的文本写一条 `assistant/message(interrupted=True, content=已发前缀)`；
   - 未派发的工具调用补 `ABORTED_BEFORE_DISPATCH` 结果（保 replay 合法）；
   - `turn/end.reason = "aborted"`。

4. **新增钩子阶段** `turn_started` / `step_completed` / `turn_stopping`（§1.4 三处同步）。

5. **配置**：`max_tool_rounds`/`empty_reply_retries`/`reasoning_overflow_retries` 收进
   `TurnLimits` 数据类（键名不变，行为不变）。

**验收**

```bash
venv\Scripts\python.exe -m pytest tests/agent/loop -q
```

新增测试：
- `test_turn_reason_matrix.py`：正常 / 静默 / 超轮 / 流式失败 / 取消 五条路径 → 五个 reason；
- `test_interrupt_prefix_is_in_history.py`：中途取消后历史里存在已发前缀且 `interrupted=True`；
- `test_abort_before_dispatch_records_tool_result.py`：取消后每个工具调用都有配对结果。

**提交**（4 个）：`feat(agent): add TurnReason and the turn state machine` /
`feat(agent): cooperative cancellation with an interrupt anchor` /
`feat(agent): broadcast turn_started, step_completed and turn_stopping` /
`refactor(agent): gather turn limits into TurnLimits`.

**风险与对策**：取消竞态容易双发。对策：`AbortScope` 只在**边界**检查，
不在发送中途抢；发送侧继续由 `pipeline` 的 generation 判重兜底。

**工作量**：3 人日。

---

### P3 工具执行契约：并发分类 + 有界池 + 超时策略

**目标不变量**：①同一时刻在执行的工具数 ≤ `tool_parallel_max`；②独占工具前后不与其他工具重叠；
③工具超时**不抛弃已在执行的协程**，只把结果替换成 `TOOL_TIMEOUT`；④回填顺序永远等于模型顺序。

**依赖**：P1（执行核心在 `tool_loop.py`，不依赖 P2）。

**改动**

1. `ToolSpec` 增三个字段（都有默认值，旧构造点不动）：
   `concurrency: str = "exclusive"`、`concurrency_safe: Callable[[dict], bool] | None = None`、
   `timeout_s: float | None = None`。
   **分类必须按每次调用**：同一个 OneBot `_handler` 既跑 `send_poke` 也跑 `set_group_kick`
   （`llm_tools_extra/onebot/executor.py:45,98-152`），按工具名分类是错的。

2. **OneBot manifest 的 `risk` 要透传**：`resolve_tool_policy` 已经带出
   `risk`（`onebot/security.py:30,81`），但 `build_onebot_tools` 构造 `ToolSpec` 时**丢弃了它**
   （`executor.py:303-313`）。补上透传，映射 `read(83) → parallel` / `send(43) + admin(49) → exclusive`。
   也可以在 `manifest.json` 里逐条写 `concurrency`/`timeout_s`（校验测试只查必填字段与枚举，
   **唯一禁止写进 JSON 的是 `sensitivity`**）。

2. **按并发模式分组执行 + 有界滚动池**（替掉 `tool_loop.py:157-158` 的无界 `asyncio.gather`）：

```python
async def execute_tool_batch(specs_by_name, calls, executor, *, limits, tool_results, abort=None) -> list[dict]:
    """按 concurrency 分组：连续 parallel-safe 进有界池，exclusive 单独成屏障；结果按模型序提交。"""
```
   - 分类 **fail-closed**：`concurrency != "parallel"` 或 `concurrency_safe(args)` 不返回
     **精确 True** → 当独占；
   - 池大小 `limits.parallel_max`（配置 `tool_parallel_max`，`1` = 串行）；
   - 回填仍按 `prepared` 顺序（现有结构天然保序，加注释 + 测试锁住）；
   - 取消时未派发的调用补 `ABORTED_BEFORE_DISPATCH` 结果；
   - 逐条标注：`expand_*` / `get_*` / `memory_recall` / `knowledge_search` → parallel；
     `schedule_task` / `memory_save` / 一切 `send_*` / `delete_*` → exclusive。

3. **超时改为"声明 + 策略"**（`tools/spec.py:make_executor`，现在只有 3 处引用模块常量
   `spec.py:213,216,217`，替换面极小）：

```python
task = asyncio.create_task(spec.handler(ctx, args))
done, _ = await asyncio.wait({task}, timeout=spec.timeout_s or limits.timeout_default_s)
if not done:                       # 不 cancel：让工具自己收敛（DSH 同款语义）
    _orphan(task, name)            # 挂 done_callback：结束后只记日志
    return ToolResult.error(TOOL_TIMEOUT, f"工具 {name} 超时（{t}s）")
```

   注意两条实测事实：①OneBot 侧确实有 `aiohttp` 级超时兜底（`mcp.py:217`、`web_search.py:57`
   有 `ClientTimeout`），所以"不 cancel"不会积压；②`expand_*` 内部会 `asyncio.gather`
   批量取多个 id，**单工具 20s 是墙钟上限而非 Σ**，声明值要按最坏批量给。
   若发现某个工具确实会永久挂住，改为"先 cancel 再记 `ABORTED`"，并在文档写明差异。

**验收**

```bash
venv\Scripts\python.exe -m pytest tests/agent/tools -q
```

新增测试：`test_tool_parallel_bound.py`（同时最多 N 个在跑）、
`test_tool_exclusive_barrier.py`（独占前后无重叠）、
`test_tool_timeout_keeps_task_alive.py`（超时后任务仍在跑且最终被收敛，无 `Task was destroyed` 警告）。

**提交**（3 个）：`feat(tools): declare concurrency and timeout per tool` /
`feat(tools): bounded parallel pool with exclusive barriers` /
`feat(tools): replace hard cancellation with a declared timeout policy`.

**风险**：超时语义从"强杀"改"等待收敛"。护栏：若某工具确实会永久挂住，
P3 先给它单独声明 `timeout_s=None` + 内部自杀超时，不搞全局限定。

**工作量**：3 人日。

---

### P4 会话日志单一真相 + 请求不可变 + 在线不变式

**目标不变量**：①`add_message(` 在全仓库只剩 1 个调用点（`session_log` 内部）；
②每次请求的 `messages` 可由会话日志**确定性重建**；③开启 `prompt_dump_verify` 时，
不一致会被检出并落盘 diff，而不是静默漂移。

**依赖**：P1（请求在 loop 层组装）+ P2（`turn/end` 才有意义）+ **P5 的配对守卫**（§6 更正 1）。

**现已核实的事实（动手前必须知道）**

- 落盘不是 JSON 文件，是 **SQLite**：`data/llm/<safe_bot_id>/history/history.db`，
  表 `conversations(bot_id, task_id, conv_id, session_id, type, title, saved_at, messages TEXT)`，
  `messages` 是**整包 JSON 数组字符串**，写入是 `INSERT OR REPLACE` **全量覆盖**。
  今天**没有** seq / event-type / entry id：唯一性全靠 list 下标 + "最近一条 assistant"的位置语义。
- `add_message(` 有 **12** 个调用点（不是 10）：多出
  `loop/command_dispatch.py:230`（`"#llm exit"`）与 `:249`（`"#llm stop"`）。
- **6 条路径在没有轮次的情况下写历史**：`import_history`、`scheduler.py:365,404`、
  `proactive/manager.py:348,404`、`command_dispatch.py:230,249`。
  它们都紧跟手动 `history.save_session()`，**不经过 pipeline 收尾**——
  日志化若把"落盘"绑到 turn 生命周期，这 6 条会静默丢数据。
- `mark_last_assistant_message_id`（`manager.py` 内，`pipeline.py:500` 调）是
  **按位置**找"最近一条 assistant"回填 message_id；`upsert_streaming_assistant` 是
  **按对象身份**原地改（`_streaming_entries[session_id]`）。
- WebUI 是**按下标**改历史：`app/application/session_history_service.py` 的
  `add_message/delete_message/edit_message/replace_message_media` 都
  `load_history()` → 改 `messages` 列表 → `update_history(messages=...)` 整包覆盖 → 回灌内存。

**改动**

1. **新增 `app/agent/session/session_log.py`**：

```python
SURFACE_ELIGIBLE = frozenset({"user/message", "assistant/message"})

class SessionLog:
    def append(self, type: str, **payload) -> dict: ...       # 带 seq，落盘
    def events(self, *, since: int = 0) -> list[dict]: ...
    def derive_messages(self) -> list[dict]:
        """只投影 surface-eligible；返回 [{role, content}, ...]（与今天 get_history 同形）。"""
```

2. **存储层加表不改列**（关键，避免一次改动所有 reader）：
   新增表 `history_events(bot_id, task_id, seq INTEGER, type TEXT, payload TEXT, ts INTEGER)`，
   主键 `(bot_id, task_id, seq)`，**只追加不覆盖**；`conversations.messages` 保留为
   **派生缓存**（WebUI / 统计 / 记忆蒸馏 / 导出都还在读它）。
   写路径：`SessionLog.append` → 追加事件行 + 刷新 `messages` 缓存（同一个事务）。

3. **12 个写入口收成 1 个语义**：`SessionManager.add_message(...)` 签名不动，
   内部改走 `self.log.append("user/message"|"assistant/message", ...)`；
   另外 2 个写入 API（`upsert_streaming_assistant` / `mark_last_assistant_message_id`）
   改为事件语义：
   - `upsert_streaming_assistant(text)` → 追加 `assistant/delta` 事件，
     派生时对同一 `entry_id` 取**最后版本**（不是覆盖写）；
   - `mark_last_assistant_message_id(mid)` → 追加一条 `assistant/message_id` 事件，
     **不修改**已写入的消息体（派生物只读，回填写进日志）。
   > 这两条是改造的**第一硬约束**：现有测试
   > `tests/plugins/proactivity/test_llm_stream_history_handles.py` 断言
   > `len(get_history)==2`，改造后必须仍然成立。

4. **WebUI 的按下标操作改为按 id**：`session_history_service.py` 的
   `edit/delete/replace_media` 改成追加 `message_edited` / `message_deleted` 事件，
   `derive_messages()` 里折叠（删除 = 从派生视图剔除，不物理删除事件）。
   `_message_at(messages, index)` 的下标语义保持不变——对外 API 不改。

5. **迁移脚本** `scripts/migrate_session_events.py`（`--dry-run` 先跑 + 自动备份，
   沿用 `scripts/migrate_v3_*.py` 的做法）：把每行 `conversations.messages`
   按数组下标生成 `seq = index` 的 `user/message` / `assistant/message` 事件；
   **幂等**（已有 `history_events` 行则跳过）。
   注意两个已知坑：`_migrate_legacy()` 只在表空时跑一次；`_row_to_dict` 对坏
   `messages` 会**静默降级为空列表**（表现是"历史凭空消失"），迁移前必须先备份 + 校验条数。

6. **在线不变式**：每次请求前，`prompt_dump_verify=True` 时断言
   `messages == log.derive_messages()`，不等则 `errors.log` 一条 +
   落 `data/llm/<bot>/prompt_dump/desync-<ts>.json`。
   （对标 `dsh-agent-loop/lib/invariant.js`，默认关，避免生产开销。）

**验收**

```bash
grep -rn "add_message(" app/ | wc -l          # 期望 1（session_log 内部）
venv\Scripts\python.exe -m pytest -q          # 全量：改到历史层，按 AGENTS.md 走大工程档位
```

新增测试：`test_request_equals_log_derivation.py`、`test_session_log_migration.py`
（旧格式 → 事件 → 派生结果与原 `history` 逐条相等）、`test_session_log_streaming_upsert.py`
（流式 upsert 后 `len(get_history)` 与改造前一致）。

**必须同步的既有测试/stub（否则必炸）**：
`tests/agent/session/test_llm_history_contract.py`（含直接 `session.data.history.append({...})`）、
`tests/agent/session/test_llm_history_write.py`、
`tests/agent/test_group_log_handles.py`、
`tests/plugins/proactivity/test_llm_stream_history_handles.py`、
`tests/plugins/proactivity/test_llm_stream_error_finalize.py`、
`tests/agent/delivery/test_llm_speech_flow.py`；
以及 5 处替身实现：
`tests/plugins/test_astrbot_alignment_plugin.py:64,251`、
`tests/agent/test_llm_initiative_compression.py:48`、
`tests/agent/assembly/test_llm_assembly_kinds.py:131`、
`tests/agent/loop/test_run_turn.py:208-211`、
`tests/agent/test_llm_help.py:71`。

**提交**（5 个）：`feat(session): add an append-only session event log` /
`refactor(session): route every history write through the log` /
`refactor(webui): edit sessions by event, not by overwriting the array` /
`feat(scripts): migrate session history into events` /
`feat(agent): verify each request against the log derivation`.

**风险**：最高。护栏：只加表不改列；迁移先 `--dry-run` 再在真实账号数据上跑；
P0 契约测试与上面 6 个历史测试必须全绿。

**工作量**：4 人日（若 WebUI 按 id 改写超预期，追加 1 人日）。

---

### P5 工具输出契约 + spill + 压缩保配对

**目标不变量**：①工具结果是有类型的（`is_error` + `code`），不再靠字符串前缀；
②超大结果**落盘可取回**而不是被砍掉；③会话历史（今天还不含工具消息，将来若含）
永远不会被切成"有 tool_call 没 tool_result"的非法请求。

**依赖**：P3（`ToolSpec` 字段已就位）。

**改动**

1. **新增 `app/agent/tools/result.py`**：

```python
@dataclass(frozen=True)
class ToolResult:
    blocks: tuple[dict, ...]        # [{type:"text", text:...}, ...]
    is_error: bool = False
    code: str = ""                  # 见 §1.2
    meta: dict = field(default_factory=dict)

    @classmethod
    def text(cls, s: str) -> "ToolResult": ...
    @classmethod
    def error(cls, code: str, message: str) -> "ToolResult": ...
    def plain(self) -> str: ...     # 拼成字符串（喂给 role:tool 消息）
```

2. **改造点只有一处**：`make_executor` 的唯一出口（`spec.py:252-261` 的 `final`）做
   `resolve()` → 仍返回 str 给上层。理由（已核实）：三个 provider 的插入点全部只读
   `msg["content"]`，而它由 `tool_loop.py:165-169` **一处**生成 → provider 零改动。
   需同步三处：`LLMResponse.tool_results[].result`、`hooks.py:90 ToolCallContext.result`、
   `telemetry.ToolCallRecord`（都保持 `str`，存 `.plain()`）。
   ⚠️ `tool_loop.py:155` 现在有 `return result if isinstance(result, str) else str(result)`——
   对 dataclass 会得到 **repr**，必须换成显式 `.plain()`。

3. **`error:` 走双读过渡**（不要一次性改 60 个生产者）：
   `is_error = res.is_error or is_error_result(res.text)` 一行即可。
   生产者约 **60 处**、断言前缀的测试 **15 个文件**（清单见盘点报告），
   一次性改会变成大爆炸提交。顺带把 `success:` / `ok:` 两个**无消费者**的前缀归一
   （新的 `ToolResult` 用 `is_error` 表达，不再有"成功前缀"）。

4. **spill**（新增 `app/agent/tools/spill.py`，仓库里**没有任何 spill 实现**，从零写）：
   - 复用现成骨架：`app/infrastructure/fs/paths.py:64 bot_data_dir()`
     （已含 `QQBOT_LLM_DATA_DIR` 测试隔离 + 路径穿越白名单）；
     写入照抄 `app/agent/media/store.py:160-162 sha_of`（内容寻址 16 位 sha，天然去重）
     与 `:228-233` 的 `tmp + os.replace` 原子写；开关/降级照抄 `assembly/dump.py:26-45`；
   - 路径：`data/llm/<bot_id>/spill/<session_id>/<sha16>.txt`（0600）；
   - 触发：`len(result.plain()) > tool_spill_max_bytes` 且 `tool_spill_enable`；
   - 替换：`maxInlineBytes` 预算内的 head/tail 预览 + 路径 + "用 `recall_tool_result` 取回"；
   - **best-effort**：落盘失败只 warn、保留原文，绝不把成功变 `is_error`；
   - 跳过取回类工具自身与 `source="speech"`（`source=="speech"` 已跳过回应要求，
     同样不该被替换）。
5. **新增取回工具 `recall_tool_result(path, offset=0, limit=4000)`**：
   仓库里没有 read/grep 类工具（`expand_*` 只取 QQ 消息），必须新建。
   两个坑：`source="system"` 的工具要同时登记
   `app/agent/tools/system.py:36-52`（WebUI 清单）与
   `state.py:257-326 resolve_all_tool_states`（它只枚举 session_tools + 注册表 + OneBot + MCP），
   漏一处就是"存在但界面上没有"。

6. **压缩保配对**（`assembly/compress.py`）：`split_history` 增加切点校验
   `tool_calls_before(cut) == tool_results_before(cut)`，不满足就往前挪一位；
   加一条断言 + 单测。**今天不可达**（工具消息不进历史，见 §6 更正 1），
   但它是 P4 之后"是否让工具消息进历史"的**前置条件**，所以放在 P5 先做。

**验收**

```bash
venv\Scripts\python.exe -m pytest tests/agent/tools tests/agent/assembly -q
```

新增测试：`test_tool_error_codes.py`（每个码可达 + `tool_call_hook` 能读到）、
`test_spill_roundtrip.py`（大结果落盘 → 预览 → `recall_tool_result` 取回全文）、
`test_compress_keeps_tool_pairing.py`（构造含 tool 对的历史，切点永远合法）。

**提交**（4 个）：`feat(tools): return structured tool results with stable error codes` /
`feat(tools): spill oversized results instead of truncating` /
`feat(tools): add recall_tool_result for spilled output` /
`fix(assembly): never cut a tool-call/tool-result pair while compressing`.

**工作量**：3 人日。

---

### P6 配置→扩展点 + 高危工具审批

**目标不变量**：①至少 3 个"因为没接缝只能做开关"的配置键变成能力声明/策略对象；
②高危 OneBot 工具在 `tool_approval_enable` 打开时，**无审批通道 → 拒绝**（fail-closed），
且 asked/decided 成对留痕。

**依赖**：P3（分类已有 risk 映射）+ P5（错误码有 `PERMISSION_DENIED`）。

**改动**

1. **能力声明试点**（3 个键）：
   - `stream_output` → `OutputCapability(kind="stream"|"blocking"|"speech")`，
     由 `llm_output` 模块声明；
   - `speech_via_tool` → 同上（它是 `OutputCapability` 的一种取值，不是独立开关）；
   - `context_expand_enable` → `ToolCapability("context_expand")`，
     `llm_context` 模块声明它提供，框架只问"有没有人提供"。
   > 目标不是消灭配置，而是让"能力存在与否"由插件声明、配置只表达偏好。

2. **审批**（新增 `app/agent/tools/approval.py`）：
   ```python
   class ApprovalOutcome(StrEnum): ALLOWED_ONCE="allowed_once"; REJECTED="rejected"; TIMEOUT="timeout"; UNAVAILABLE="unavailable"
   async def request_approval(runtime, *, tool, args, ctx) -> ApprovalOutcome
   ```
   - 触发条件：`tool_approval_enable` **且** OneBot 工具 `sensitivity in {high, critical}`；
   - 通道：私聊 Bot 拥有者发确认（复用现有消息发送，不新增通道）；
   - 无拥有者/发送失败 → `UNAVAILABLE` → **拒绝**（fail-closed）；
   - 拒绝文案按 outcome 区分（"用户拒绝" / "超时未响应" / "无审批通道"）；
   - 审计：`telemetry.record_tool(..., meta={"approval": outcome})` + 一条 `user.log`。

**验收**

```bash
venv\Scripts\python.exe -m pytest tests/agent/tools tests/architecture -q
```

新增测试：`test_tool_approval_fail_closed.py`、`test_tool_approval_outcomes.py`、
`test_capability_declaration_replaces_switch.py`（能力声明生效后旧键不再影响行为）。

**提交**（3 个）：`feat(tools): ask the owner before running high-risk tools` /
`refactor(agent): declare output and tool capabilities instead of config switches` /
`docs: update the module dev guide for capabilities and approvals`.

**工作量**：2 人日。

---

## 4. 排序、依赖与关键路径

```
P0 ─▶ P1 ─┬─▶ P2 ─────────────┐
          │                    ├─▶ P4 ─▶ P6
          └─▶ P3 ─▶ P5 ────────┘
```

- **关键路径**：P0 → P1 → P3 → P5 → P4（P4 需要 P5 的配对守卫，见 §6 更正 1）。
- **可并行**：P2 与 P3 互不依赖（都只依赖 P1）；P6 只依赖 P3/P5。
- **建议先做的两个"1 天见收益"**：P0（安全网）、P3 的**有界池**部分
  （不用改契约，只把 `gather` 换掉，就能消掉"20 个工具 20×20s 撞 30s 超时"这个真实故障）。

---

## 5. 每阶段的"完成"判据（Definition of Done）

一个阶段算完成，必须同时满足：

1. 该阶段验收命令全绿（含全量 `pytest -q`，若跨层）；
2. 新增测试能**在故意回退时变红**（PR 里贴一次变异检查结果）；
3. 文档同步：`docs/tool-loop-professionalization.md` 的对应差距行从 A/B 改为 ✅，
   `README.md` 若有行为变化同步；
4. 提交历史干净：每个提交只做一件事，信息符合 Conventional Commits；
5. 无新增 `except Exception: pass`、无新增裸 `print`、`ruff check .` 无新增违规。

---

## 6. 对初版调查的三处更正（均已核实）

**更正 1：压缩劈开 tool 配对在当前设计下不可达。**
初版把"压缩按条数切可能劈开 `tool_call`/`tool_result`"列为 B 级差距。
实测：会话历史只存 user/assistant 文本（`session/manager.py:434-487` 只写
`role` ∈ {user, assistant}，`get_history` 只回 `{role, content}`），
`role:tool` 消息**只活在当轮 `messages` 里**，而 `maybe_compress_context` 作用在
**历史**上（`assembly/inputs.py:48`、`loop/prompt_prep.py:211`），因此今天切不到工具对。
→ 它从"现存 bug"降级为"**P4 一旦让工具消息进历史就会立刻成立**的前置约束"。
P5 的配对守卫因此**不是清理，而是给 P4 上的保险**，必须先于 P4 落地。

**更正 2：P4 的正确设计是"日志记全部、投影只取一部分"。**
日志要成为唯一真相，但**不**意味着工具结果要进后续请求。事件分
surface-eligible（`user/assistant`）与 log-only（`tool/*`、`step/*`），
`derive_messages()` 只投影前者（DSH 的 `isSurfaceEligibleType` 同款）。
这样 P4 拿到单一真相，同时**保持今天的模型语义与 KV 前缀稳定性**不变。
"工具结果是否跨轮持久化"是一个独立的产品决策，留给 P4 之后单独评估。

**更正 3：历史写入点是 12 个不是 10 个，且落盘是 SQLite 而不是 JSON。**
- 多出的 2 个：`loop/command_dispatch.py:230`（`"#llm exit"`）、`:249`（`"#llm stop"`）。
- 落盘：`data/llm/<safe_bot_id>/history/history.db` 的 `conversations` 表，
  `messages` 列是**整包 JSON 数组字符串** + `INSERT OR REPLACE` 全量覆盖，
  没有 seq / event-type / entry id。所以 P4 **必须新增 `history_events` 表**，
  而不能"在既有 JSON 里加个 events 字段"。

**更正 4（顺带发现的既有 bug）：工具权限预判第 ⑤ 层从未生效。**
`tools/state.py:249` 调 `spec.allowed_for(event, scope=...)`，而 `ToolSpec` 上只有
`allows()`（`spec.py:113`）；异常被 `:251-252` 吞掉 → `permission_denied` 分支是死代码。
放在 P0 一并修（独立提交）。

---

## 7. 进度看板

| 阶段 | 状态 | 提交 | 备注 |
|---|---|---|---|
| P0 契约测试 | ✅ **已完成** | `2a1c1ab` `46f0fb4` | 顺带修了两个静默 bug（权限两层）；契约测试变异验证通过；全量 2435 passed |
| P1 循环收口 | ✅ **已完成** | `20b0e40` … `ba3f9cd` | 循环只剩 `loop/tool_step.py` 两处（流式 + 非流式）；适配器单次请求；全量 2447 passed |
| P2 状态机+取消 | ✅ **已完成** | `25a7cda` `2969cf9` `1564770` `924c19a` `673d031` | `TurnReason`/`TurnState`/`AbortScope`/中断锚点/三个观测钩子/`TurnLimits` 全部落地；全量 2464 passed |
| P3 并发+超时 | ✅ **已完成** | `c456185` `8cc3efa` `d868fbc` `eb44851` | 声明/有界池/独占屏障/超时策略 + 两个配置键已登记（本体键 45→47）；全量 2479 passed |
| P4 单一真相 | 🔄 **进行中 2/5** | `98c01a7` `94672ad` | 三个写 API 已双写事件日志（读侧未动）；全量 2489 passed |
| P5 输出契约+spill | ☐ 未开工 | — | 依赖 P3 |
| P6 能力+审批 | ☐ 未开工 | — | 依赖 P3/P5 |

### P2 实际进度

| 步骤 | 状态 | 提交 | 说明 |
|---|---|---|---|
| `TurnReason` 七值 + `classify_step` + `TurnState` | ✅ | `25a7cda` | 只认 `kind`/`finish_reason`/`text`，不看日志文案；兜底话术不再冒充 `completed` |
| 两条入口都产出结构化结局 | ✅ | `25a7cda` | `TurnResult.reason` + `ctx.state["turn_reason"]` + 遥测 `reason` 字段 |
| `AbortScope` 协作式取消 + 流水线接线 | ✅ | `2969cf9` | 只在两个边界检查（请求前/派发工具前）；`interrupt_session` 同时抬代际与取消 |
| 中断锚点（已发前缀进历史 + 未派发工具补 `ABORTED_BEFORE_DISPATCH`） | ✅ | `1564770` | 未派发的调用补配对结果再抛，transcript 保持可回放 |
| 新钩子阶段 `turn_started` / `step_completed` / `turn_stopping` | ✅ | `924c19a` | 三处同步；纯观测（返回值不影响结局） |
| `TurnLimits` 收拢三个上限配置 | ✅ | `673d031` | 键名/取值口径/行为都不变 |

### P1 实际进度（每个可提交步骤一条）

| 步骤 | 状态 | 提交 | 说明 |
|---|---|---|---|
| 共享运行器 `loop/tool_step.py`（流式）+ 单测 | ✅ | `20b0e40` | 循环控制、碎片累积、工具执行、回填、补图收成一处 |
| `turn._stream_once` 改走 runner | ✅ | `297859e` | 句子仍用 `\n` 连接；失败仍打一行 error |
| runner 的 lint 清零 + `RoundSummary.images_appended` | ✅ | `e17dea2` | 补图留痕从各副本挪到共享层（可观测性不丢） |
| `proactive.stream` 改走 runner | ✅ | `86acb70` | 句子逐句入池不变；工具轮上限不变 |
| `run_blocking_step` + `LLMResponse.tool_calls` | ✅ | `1409ce4` | 适配器能报"我要调这些工具"，循环才能上移 |
| `turn._chat_once` 改走 runner | ✅ | `eee40e5` | 顺带删掉"补图后再补问一轮"的特例；`classify_response` 认 `tool_calls` 为产出 |
| runner 改**事件流**（`iter_stream_step`） | ✅ | `cda21b5` | 回调里不能 yield：把产出变成事件，主路径才能边产边发且保住句间打断 |
| `chat.stream_response` 改走 runner | ✅ | `6259e7c` | 见下方偏差说明（收尾轮也 flush 发言通道） |
| 三个适配器改单次请求 + 兼容壳 | ✅ | `541eae5` | `chat_with_fallback` 签名不变，内部转调 `run_blocking_step` |
| 白名单收敛（`KNOWN_LOOPS` → 只剩 runner） | ✅ | `541eae5` | `BASELINE_COUNT = 2`（流式 + 非流式各一处） |
| `test_turn_path_parity.py` 的源码扫描 | ✅ 改为**改指向** | `541eae5` | 见下方偏差说明 |
| params 键集清理（主动消息补预算闸门） | ✅ | `ba3f9cd` | 主动消息/定时任务此前完全没有 `TurnBudget` |

#### P1 交付与计划的差异（都已落库）

1. **多了一个 `iter_stream_step`**：计划里 `on_text` 回调 + "队列转 yield" 的方案能跑，但队列要么把句间打断延后，要么需要在
   `GeneratorExit` 里等一个取消任务。改成**事件流**（`("text", 句子)` / `("round_end", RoundSummary)`）
   后，`chat.stream_response` 直接在 `async for` 里 `yield`，语义零折损，也不用引入并发。
2. **`test_turn_path_parity.py` 没删，改成指向 runner**：计划写"删掉源码扫描"，但那文件里还有
   4 条与搬家无关的有效守卫（`post_assemble` 必经、补全登记、历史清洗口径、`turn_completed` 广播）。
   只把"两条路径都要补图"那条改成锁 runner——删整文件等于顺手撤掉别的安全带。
3. **`STREAM_PARAMS` 不加 `max_tool_rounds`**：计划想让它与 `CHAT_PARAMS` 对齐，但收口后流式 invoker
   **不该**自己跑循环（否则又双循环一遍），这个值对它没有意义。非流式的 `max_tool_rounds` 仍走
   `CHAT_PARAMS` → 兼容壳 → `StepRequest.max_rounds`，语义唯一。
4. **两个真 bug 顺手修掉**（不修就是留隐患）：
   `classify_response` 不认 `tool_calls` 会把"我要调工具"判成空回复并原样重发（适配器单次请求后必现）；
   发言通道的**收尾轮**此前从不 flush，最后一轮才调 `send_message` 时那条消息会丢。

### P0 实际交付（与计划的差异）

- 新增 `tests/agent/loop/test_tool_loop_contract.py`（3 个入口 × 2 轮工具，15 条断言）；
  替身**继承了真实 `OpenAICompatProvider`，只替换 HTTP 底座**——所以量的是真循环，
  P1 把循环搬到 loop 层后这三条测试不需要改。
- 新增 `tests/architecture/test_no_tool_loop_in_providers.py`：认**循环形状**
  （`for _round in range(`）而不是变量名，冻结当前 6 份副本，只许减不许增。
- 变异验证：把工具结果内容清空 → 3 条契约测试全红；还原 → 全绿。
- **计划外的收获**：盘点时发现并修掉两个静默 bug（`allows()` 无事件误判、
  判定链第 ⑤ 层死代码），见 §6 更正 4。
- 计划里的"删除 `test_turn_path_parity.py`"**顺延到 P1**：函数搬家前它还是有效守卫，
  提前删等于在动刀前先撤掉安全带。

> 每完成一格就改这张表并提交，避免"做到哪了"靠回忆。
