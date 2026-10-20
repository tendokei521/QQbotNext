> 本文只负责：说明 `app/agent/assembly/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/assembly/` —— 把上下文拼成发给模型的 `messages`：取料 → 排序 → 算预算 → 压缩。

## 包含

| 文件 | 职责 |
|---|---|
| `assembler.py` | 总装配：按 profile 决定「放哪些块、按什么顺序」 |
| `inputs.py` | 取料：群语境、历史、记忆、知识库、人格规则各取一份 |
| `prompt.py` | 系统提示词的分段渲染 |
| `token_budget.py` | token 预算分配与裁剪 |
| `compress.py` | 超预算时的历史压缩 |
| `dump.py` | 把最终 messages 落盘（`/agent/prompt_dump` 用） |
| `enhance.py` | 外部注入的增强块 |

## 注意

改「模型到底看到了什么」→ 先动这里，**不要**动 `loop/`。
`assembler.py` 的可达闭包已从 41,717 行降到 9,406 行（P3）。

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` 与本包内文件。
- 不得 import `app.webui` / `app.bootstrap`。跨子包依赖请走对方 `__init__.py` 的导出。
- 新增文件前先问：它属于「取料 / 决策 / 发送 / 存储」哪一类？答不上来就别加。
