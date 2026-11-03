> 本文只负责：说明 `app/agent/persona/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/persona/` —— 人格与表达：这个 Bot「是谁、怎么说话」。

## 包含

| 文件 | 职责 |
|---|---|
| `rules.py` | 行为段渲染工具：本轮能力探测（caps）+ 按段落表逐段拼接 |
| `skills.py` | 技能声明与渲染 |
| `tags.py` | 标签体系 |
| `emoji.py` | 表情词典：口语别名 → QQ 系统表情 id |
| `data/` | 生成的数据（`qq_faces.py`：QQ 客户端系统表情全量表） |

## 注意

两套系统提示词（chat / roleplay）的**段落文案不在本包**，统一放在
`app/agent/prompts/presets.py`；`rules.py` 只保留与文案无关的机制。

`data/qq_faces.py` 是**生成物**，由 `scripts/gen/export_napcat_faces.py` 从本机
NapCat 包导出，`--check` 做升级后比对。**不要手改**。

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` 与本包内文件。
- 不得 import `app.webui` / `app.bootstrap`。跨子包依赖请走对方 `__init__.py` 的导出。
- 新增文件前先问：它属于「取料 / 决策 / 发送 / 存储」哪一类？答不上来就别加。
