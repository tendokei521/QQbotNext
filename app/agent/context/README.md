> 本文只负责：说明 `app/agent/context/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/context/` —— 群语境与指代：谁在跟谁说话、「他/那个」指的是谁。

## 包含

| 文件 | 职责 |
|---|---|
| `group.py` | 群语境：最近谁说了什么、群的整体氛围 |
| `referent.py` | 指代消解：把「他 / 那个 / 上一条」落到具体消息或人 |
| `focus.py` | 当前对话焦点 |
| `nicknames.py` | 昵称提取与映射 |
| `display_names.py` | 展示名解析（群名片 / 昵称 / QQ 号的优先级） |

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` 与本包内文件。
- 不得 import `app.webui` / `app.bootstrap`。跨子包依赖请走对方 `__init__.py` 的导出。
- 新增文件前先问：它属于「取料 / 决策 / 发送 / 存储」哪一类？答不上来就别加。
