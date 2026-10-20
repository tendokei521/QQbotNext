> 本文只负责：说明 `app/agent/storage/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/storage/` —— 存储接口与工厂：历史/记忆的「存哪」由这里决定。

## 包含

| 文件 | 职责 |
|---|---|
| `base.py` | `HistoryStore` / `MemoryStore` 抽象与工厂函数 |

## 注意

**具体实现在 `app/infrastructure/persistence/`**，本包只有接口。
（这个包曾被方案误判为死代码，实际被 `media/store.py` 与 `session/manager.py` 使用。）

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` 与本包内文件。
- 不得 import `app.webui` / `app.bootstrap`。跨子包依赖请走对方 `__init__.py` 的导出。
- 新增文件前先问：它属于「取料 / 决策 / 发送 / 存储」哪一类？答不上来就别加。
