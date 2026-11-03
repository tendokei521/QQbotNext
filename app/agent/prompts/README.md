> 本文只负责：说明 `app/agent/prompts/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/prompts/` —— 框架提示词的唯一位置。

## 规则

1. **框架所有提示词注入都从这里 import**：内置 system 段、发言协议、工具结果回应要求、
   群记录头尾、历史省略提示、重试 nudge、压缩/摘要等内部任务 prompt。
2. 业务代码（`assembler` / `loop` / `delivery` / `group_log` / `tools`）不再自己写字面量；
   它们只负责"什么时候注入、注入到哪"，文案从本包取。
3. **模块自己的提示词仍归模块**（`llm_context` / `llm_trigger` 用 `register_section`），
   不搬进本包——模块卸载时提示词要跟着消失。
4. 人格正文、群记录、历史、记忆数据这些"运行时内容"不是文案：它们由各自模块生成，
   只是填进这里声明的模板/段落。

## 目录

| 文件 | 内容 |
|---|---|
| `presets.py` | 两套提示词方案（chat / roleplay）的段落文案与渲染 |
| `sections.py` | 框架默认 system 段（persona/behavior/emoji/rules/identity/…） |
| `convention.py` | 消息格式约定段 |
| `participation.py` | 参与度 / 触发规则的提示词段 |
| `emoji.py` | 可用表情提示 |
| `onebot.py` | OneBot 敏感工具的消息凭证约束 |
| `identity.py` | 身份块文案模板（账号/拥有者/管理员/当前群聊） |
| `persona.py` | 默认人格兜底文案 |
| `speech.py` | 发言协议（贴在最后一条 user 消息尾部） |
| `tool_reply.py` | 工具结果回应要求（贴在工具结果尾部） |
| `group_log.py` | 群聊环境记录的头尾声明 |
| `history.py` | 历史省略提示 / 压缩摘要头 |
| `nudges.py` | 重试类 user 提醒 |
| `internal.py` | 后台内部 LLM 任务 prompt（历史压缩） |

## 写法约定

- **静态文案优先常量 / 段落数组**；只有需要运行时值的才写成函数（如身份块、参与度段）。
- 新增提示词：先加进本包对应文件，再在注入点 import；不要在调用点写 `"""..."""`。
