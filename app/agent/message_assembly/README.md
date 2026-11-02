> 本文只负责：说明 `app/agent/message_assembly/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/message_assembly/` —— 消息渲染：把一条 QQ 消息变成模型可读的文本。

## 包含

- `spec.py`：渲染契约（哪些段能内联、哪些只能给占位符、占位符 → 展开工具的映射）；
- `segments.py` / `render.py`：结构化标记的渲染（`[at:id=..]`、`[image:id=..]`、`[reply_to:..]` 等）；
- `default_renderer.py`：默认渲染器——只做时间/群/发送者/正文/at/占位符/引用 id；
- `history.py`：历史条目的渲染与引用 id 提取；
- `enrich.py`：引用正文、被引用者、展开状态等**富化**信息（渲染器/历史之外的补充材料）；
- `convention.py`：给模型的「消息格式约定」说明段（标记含义、哪些要展开）。

## 不放

- 不决定"发不发、怎么发"——那是 `delivery/`；
- 不拼系统提示词——那是 `assembly/`；
- 不读写会话存储——历史取用方在 `session/`，本包只接收已取出的消息。
