> 本文只负责：说明 `app/agent/assembly/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/assembly/` —— 把上下文拼成发给模型的 `messages`：取料 → 排序 → 算预算 → 压缩。

## 包含

| 文件 | 职责 |
|---|---|
| `assembler.py` | 总装配：按 profile 决定「放哪些块、按什么顺序」 |
| `builtin_sections.py` | 内置段的定义（每段的层、条件、文案） |
| `sections.py` | 提示词 section 注册表：加一段提示词不必接管整个装配器 |
| `layers.py` | 五层模型（L1 系统提示词 → L2 人格 → L3 注入 → L4 历史 → L5 本轮） |
| `presets.py` | **三套系统提示词的全部段落文案**（chat / roleplay / assistant） |
| `prompt.py` | 系统提示词的分段渲染 |
| `prompt_cache.py` | 提示词前缀稳定性工具（KV cache 的可测基础） |
| `inputs.py` | 取料：群语境、历史、记忆、知识库、人格规则各取一份 |
| `token_budget.py` / `context_budget.py` | token 预算分配与"离窗口爆掉还有多远"的估计 |
| `compress.py` / `compaction.py` / `pairing.py` | 超预算时的历史压缩、可插拔压缩后端、压缩切点不切开工具配对 |
| `history_window.py` | 历史窗口：这一次请求内联多少条历史 |
| `context_data.py` | 模块注入的参考资料统一加数据边界 |
| `interrupt.py` | 回复打断的配置桥（开关落到运行时中断标记） |

## 注意

改「模型到底看到了什么」→ 先动这里，**不要**动 `loop/`。
`assembler.py` 的可达闭包已从 41,717 行降到 9,406 行（P3）。

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` 与本包内文件。
- 不得 import `app.webui` / `app.bootstrap`。跨子包依赖请走对方 `__init__.py` 的导出。
- 新增文件前先问：它属于「取料 / 决策 / 发送 / 存储」哪一类？答不上来就别加。
