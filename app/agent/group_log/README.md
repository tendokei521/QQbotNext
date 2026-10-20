> 本文只负责：说明 `app/agent/group_log/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/group_log/` —— 群聊日志：把群里发生的事记下来，供「刚才聊了什么」类问题查询。

## 包含

| 文件 | 职责 |
|---|---|
| `context.py` | 把群日志整理成给模型看的上下文块 |
| `events.py` | 事件模型 |
| `render.py` | 渲染成文本 |
| `store.py` | 存储 |

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` 与本包内文件。
- 不得 import `app.webui` / `app.bootstrap`。跨子包依赖请走对方 `__init__.py` 的导出。
- 新增文件前先问：它属于「取料 / 决策 / 发送 / 存储」哪一类？答不上来就别加。
