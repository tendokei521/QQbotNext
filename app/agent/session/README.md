> 本文只负责：说明 `app/agent/session/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/session/` —— 会话与历史：切换 / 落盘 / 渲染 / 富化。

## 包含

| 文件 | 职责 |
|---|---|
| `manager.py` | `SessionManager`：会话生命周期、当前会话、落盘时机 |
| `history.py` | 历史记录的读写与格式转换 |
| `model.py` | 历史的数据模型（一条消息长什么样） |
| `enrich.py` | 富化：给历史补上图片/昵称等引用信息 |

## 注意

历史的**存储接口**在 `app/agent/storage/`，**数据目录**在 `app/infrastructure/fs/paths.py`。

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` 与本包内文件。
- 不得 import `app.webui` / `app.bootstrap`。跨子包依赖请走对方 `__init__.py` 的导出。
- 新增文件前先问：它属于「取料 / 决策 / 发送 / 存储」哪一类？答不上来就别加。
