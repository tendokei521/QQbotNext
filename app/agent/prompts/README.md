> 本文只负责：说明 `app/agent/prompts/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/prompts/` —— 框架提示词的唯一位置，**代码顺序 = 实际组装顺序**。

## 规则

1. **框架所有提示词注入都从这里 import**：内置 system 段、发言协议、工具结果回应要求、
   群记录头尾、历史省略提示、重试 nudge、压缩/摘要等内部任务 prompt。
2. 业务代码（`assembler` / `loop` / `delivery` / `group_log` / `tools`）不再自己写字面量；
   它们只负责"什么时候注入、注入到哪"，文案从本包取。
3. **模块自己的提示词仍归模块**（`llm_context` / `llm_trigger` 用 `register_section`），
   不搬进本包——模块卸载时提示词要跟着消失。
4. 人格正文、群记录、历史、记忆数据这些"运行时内容"不是文案：它们由各自模块生成，
   只是填进这里声明的模板/段。

## 目录（按组装顺序）

| 文件 | 内容 |
|---|---|
| `system.py` | **全部静态文案，严格按组装顺序**：L1 10 persona → 30 emoji → 54 reply_rules → 55 onebot_auth → 60 message_meta → 100 group_log → 106 identity → L4 历史提示/摘要头 → L5 发言协议 → 工具结果回应要求 → 重试 nudge → 后台压缩 prompt |
| `presets.py` | L1 order 20 behavior 的两套方案段落（chat / roleplay） |
| `sections.py` | 默认段 builder，函数顺序与 `default_sections()` 的 order 一致（persona → … → user），每个函数上方标了实际槽位 |
| `convention.py` | L1 order 60 消息格式约定段的 builder（文案在 `system.py`） |
| `participation.py` | L1 order 54 参与度/触发规则段的 builder（文案在 `system.py`） |

## 写法约定

- **静态文案优先常量 / 段落数组**，全部放 `system.py`，按组装顺序排列；
- 只有需要运行时值的才写成函数（如身份块、参与度段），函数放在 `sections.py`
  对应 order 的位置；
- 新增提示词：先加进 `system.py` 的对应顺序位置，再在注入点 import；不要在调用点写 `"""..."""`。
