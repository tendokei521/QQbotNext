> 本文只负责：说明 `app/domain/` 放什么、不放什么。上层目录总览见 `docs/ARCHITECTURE.md`。

# `app/domain/` —— 领域模型

**一句话：协议无关的类型与契约。谁都能 import，它不 import 谁。**

## 包含

| 文件 | 职责 |
|---|---|
| `bot.py` | `IBot` 抽象——模块只依赖它收发消息，不认 OneBot |
| `events.py` | 类型化事件（消息 / 通知 / 申请） |
| `message.py` | 消息段模型（文本 / 图片 / @ …） |

## 边界（重要）

- 允许 import：`app.core.*`、标准库、三方库。
- **不得** import：`app.agent` `app.infrastructure` `app.modules` `app.services` `app.webui`。
  `tests/test_architecture_boundaries.py` 会把这条闸死。
- **不得**做 IO：这里只有类型和纯函数。读文件、连数据库、发网络请求都不属于这里。

## 现状与目标

现在只有 4 文件 / 595 行，**全是 DTO，没有业务规则**——这是本仓库最大的结构性欠账
（见 `STYLE.md §12.6`）。真正该属于领域的判断（「何时该主动说话」「下次触发怎么算」
「人格规则怎么裁」）目前散在 `app/agent/proactivity/` 与 `app/agent/persona/` 里，
和 LLM 调用、JSON 落盘混居。

P4 的作业：把**零内部依赖的纯规则模块**搬进来（候选：`proactivity/time_parser.py`、
`proactivity/trigger.py`、`delivery/splitter.py`、`providers/policy.py` 一类）。
