> 本文只负责：说明 `app/agent/loop/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/loop/` —— 一轮对话的主流程：判定 → 组装 → 请求 → 工具循环 → 发言。

## 包含

| 文件 | 职责 |
|---|---|
| `chat.py` | `handle()` 入口；`prepare_prompt` / `generate_response` / `stream_response`（2,192 行，P4 待拆） |
| `pipeline.py` | 请求管线（非流式为主流程，流式另一条） |
| `context.py` | `LlmContext`：一轮对话里到处要用的运行时上下文对象 |
| `tool_loop.py` | 工具调用循环：解析 → 执行 → 回填 → 再请求 |
| `locks.py` | 按会话的互斥锁（同一会话不并发跑两轮） |
| `pool.py` | 并发槽位控制 |

## 注意

**这是全库最大的一个包，也是唯一还有环的地方。**
`chat.py` ⇄ `proactivity/scheduler.py` ⇄ `proactivity/stream.py` 三者互为递归
依赖（主循环要拿 `schedule_task` 工具，调度器到点要跑一轮生成）。

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` 与本包内文件。
- 不得 import `app.webui` / `app.bootstrap`。跨子包依赖请走对方 `__init__.py` 的导出。
- 新增文件前先问：它属于「取料 / 决策 / 发送 / 存储」哪一类？答不上来就别加。
