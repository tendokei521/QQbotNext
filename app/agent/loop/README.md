> 本文只负责：说明 `app/agent/loop/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/loop/` —— 一轮对话的主流程：判定 → 组装 → 请求 → 工具循环 → 发言。

## 包含

| 文件 | 职责 |
|---|---|
| `chat.py` | `handle()` 入口；`stream_response` 消费 `iter_stream_step` 的事件（`text` / `round_end`），发言通道逐轮 flush（含收尾轮），结局写 `ctx.state["turn_reason"]` |
| `pipeline.py` | 请求管线（非流式为主流程，流式另一条） |
| `context.py` | `LlmContext`：一轮对话里到处要用的运行时上下文对象 |
| `tool_step.py` | **唯一的工具轮循环**：`iter_stream_step`（事件流）/ `run_stream_step`（回调封装）/ `run_blocking_step`（非流式）；多轮、回填、补图都在这里 |
| `tool_loop.py` | 工具批次执行：`execute_tool_batch`（有界并发 + 独占屏障，回填按模型顺序）、标准化与回传消息 |
| `turn_runner.py` | 结局与取消：`TurnReason` / `TurnState` / `AbortScope` / `TurnLimits`（`turn.py::run_turn` 据此产出 `reason` / `rounds` / `interrupted`） |
| `locks.py` | 按会话的互斥锁（同一会话不并发跑两轮） |
| `pool.py` | 并发槽位控制 |

## 注意

**这是全库最大的一个包，也是唯一还有环的地方。**
`chat.py` ⇄ `proactivity/scheduler.py` ⇄ `proactivity/stream.py` 三者互为递归
依赖（主循环要拿 `schedule_task` 工具，调度器到点要跑一轮生成）。

取消是协作式的（`LlmContext.abort` = `AbortScope`）：只在「每次请求前 / 派发工具前」
检查；没派发出去的工具补 `ABORTED_BEFORE_DISPATCH` 配对结果，已产出的前缀照常留历史。

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` 与本包内文件。
- 不得 import `app.webui` / `app.bootstrap`。跨子包依赖请走对方 `__init__.py` 的导出。
- 新增文件前先问：它属于「取料 / 决策 / 发送 / 存储」哪一类？答不上来就别加。
