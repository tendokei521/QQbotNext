# 历史档案（不维护）

这里保存的是**过程记录**：实施计划、评审报告、外部实现研究、早期设计稿。
它们解释了"当初为什么这么改"，但**不代表当前代码**——模块名、配置键、页面结构都可能已经变了。

| 目录 | 内容 | 典型年份 |
|---|---|---|
| [plan/](plan/) | 分阶段实施计划（每条带 commit hash 与验收状态） | 2026-10 |
| [reviews/](reviews/) | 评审与问题分析（含排障证据、根因） | 2026-10 |
| [research/](research/) | 外部 bot 实现研究、差分对比 | 2026-10 |
| [legacy/](legacy/) | 早期设计稿：模块重设计、多账号、打包、提示词重写、记忆/指代/上下文单点设计 | 2026-09 ~ 10 |
| [assembly-takeover-progress.md](assembly-takeover-progress.md) | 更早一次"组装接管"改造的进度记录 | 2026-10 |

## 读档案前的三个提醒

1. **模块清单已变**：现在内置只有 5 个模块（触发与调度 / 上下文增强 / 长期记忆 / 工具扩展 / LLM 调试）。
   档案里出现的 `llm_output`、`llm_assembly`、`llm_message_meta`、`llm_proactive`、`llm_schedule`、`llm_knowledge` 都已合并或删除。
2. **配置键已变**：`assembly_profile`、`behavior_rules_enable`、`system_prompt_style` 已删除；
   提示词改为五层装配（见现行 [../ARCHITECTURE.md](../ARCHITECTURE.md)、[../AGENT_CONFIG.md](../AGENT_CONFIG.md)）。
3. **链接可能失效**：搬家时保留了目录相对结构，但指向 `docs/` 根目录旧设计稿的链接可能断。
   需要引用历史时，请整段摘录（并注明"档案，已过时"），不要直接链进去当现行依据。

## 现行文档在哪

见 [../README.md](../README.md)（文档地图）。
