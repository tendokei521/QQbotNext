> 本文只负责：说明 `app/agent/proactivity/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/proactivity/` —— 主动消息与定时任务：Bot 自己决定什么时候开口。

## 包含

| 文件 | 职责 |
|---|---|
| `manager.py` | 主动消息总控：何时允许、冷却、频率 |
| `scheduler.py` | `TaskScheduler` 定时引擎（LLM 语义定时，落盘 `tasks_data.json`） |
| `stream.py` | 主动消息的流式生成 |
| `trigger.py` | 触发条件判定（确定性兜底） |
| `time_parser.py` | 中文时间解析（「明天早上8点」→ 时间戳） |

## 注意

⚠️ 别和 `app/services/scheduler.py::SchedulerService`（230 行的声明式定点任务）
搞混，两者零复用（`STYLE.md §12.3`）。
`scheduler.py` 与 `loop/chat.py` 目前互为依赖，P4 要把「定时工具」
（`build_schedule_tool` / `handle_schedule_tool` / 意图检测）拆到 `tools/`。

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` 与本包内文件。
- 不得 import `app.webui` / `app.bootstrap`。跨子包依赖请走对方 `__init__.py` 的导出。
- 新增文件前先问：它属于「取料 / 决策 / 发送 / 存储」哪一类？答不上来就别加。
