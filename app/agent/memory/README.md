> 本文只负责：说明 `app/agent/memory/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/memory/` —— 长期记忆：记什么、怎么召回、怎么蒸馏、怎么纠正。

## 包含

| 文件 | 职责 |
|---|---|
| `manager.py` | `MemoryManager`：召回注入、自动保存、隐性蒸馏 |
| `store.py` | SQLite 存储（按 bot 隔离，owner 路由，audit） |
| `recall.py` | 召回排序与块渲染 |
| `extract.py` | 从对话里抽取值得记的事实 |
| `detect.py` | 确定性兜底：什么话该被记为记忆 |
| `tool.py` | 原生工具（memory_save / recall / delete / correct / deny） |
| `commands.py` | `#llm memory ...` 管理指令 |

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` 与本包内文件。
- 不得 import `app.webui` / `app.bootstrap`。跨子包依赖请走对方 `__init__.py` 的导出。
- 新增文件前先问：它属于「取料 / 决策 / 发送 / 存储」哪一类？答不上来就别加。
