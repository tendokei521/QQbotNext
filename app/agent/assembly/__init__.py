"""装配层：把原料拼成 `messages`。

## 本子包只负责

「模型这一轮该看到什么」——历史、记忆、群语境、知识、图片、技能说明按什么顺序、
占多少 token、超了怎么压。输入是各种原料，输出是 `messages` 列表 + 工具清单。

## 不负责

- 调模型（`loop/`）；
- 发消息（`delivery/`）；
- 决定要不要回复（`modules/` 的触发判定与 `loop/` 的锁）。

## 文件地图

| 文件 | 职责 |
|---|---|
| `inputs.py` | 收集原料（历史、记忆、知识、群语境） |
| `assembler.py` | 主装配流程，产出 `messages` |
| `prompt.py` | system prompt 的段落拼装与模板 |
| `token_budget.py` | token 预算分配与超限判断 |
| `compress.py` | 上下文压缩（摘要式降级） |
| `enhance.py` | 装配后的增强（人设强化、行为规则注入） |
| `dump.py` | prompt 落盘（调试用，WebUI 可下载） |
"""
