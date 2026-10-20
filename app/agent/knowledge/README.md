> 本文只负责：说明 `app/agent/knowledge/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/knowledge/` —— 知识库：向量化、检索、增删。

## 包含

| 文件 | 职责 |
|---|---|
| `manager.py` | 知识库总控 |
| `store.py` | 持久化 |
| `vector.py` | 向量化与相似度检索 |
| `tool.py` | 原生工具（knowledge_add / search / delete） |

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` 与本包内文件。
- 不得 import `app.webui` / `app.bootstrap`。跨子包依赖请走对方 `__init__.py` 的导出。
- 新增文件前先问：它属于「取料 / 决策 / 发送 / 存储」哪一类？答不上来就别加。
