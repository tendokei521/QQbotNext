# AstrBot 组装对齐插件

在**单个账号**下接管消息组装，把 [`1/AstrBot-master`](../../../1/AstrBot-master)（v4.27.2）
的 system prompt 与请求形态 1:1 复刻过来，用于迁移对账。

## 安装与启用

插件在 `app/plugins/astrbot_alignment/`，框架启动即加载（无需安装步骤）。
在 WebUI 的模块页：

1. **按账号启用**（模块启停是按 `(模块, 账号)` 的，只对该账号生效）；
2. 配置项在模块卡片里（`config_schema.py` 渲染）；
3. 声明式接管 `assembly`：启用时装上，禁用/卸载自动还原框架默认装配器。

配合对账（可选）：

- Agent 配置页打开 `prompt_dump_enable` → 每轮最终 messages 落到
  `data/llm/<账号>/prompt_dump.jsonl`；
- 或 `GET /api/agent/prompt-dumps?bot_id=<qq>&session_id=group_xxx`；
- 与 AstrBot 侧同轮的 `messages` 逐条 diff。

## 组装口径（对齐依据）

### system prompt：一整条，段落顺序如下

| # | 段落 | AstrBot 位置 | 默认 |
|---|---|---|---|
| 1 | 安全模式 `LLM_SAFETY_MODE_SYSTEM_PROMPT`（**前插**） | `astr_main_agent.py:1117` | 开 |
| 2 | ChatUI GenUI | `:510` | 关（本框架无 ChatUI） |
| 3 | `\n# Persona Instructions\n\n{prompt}\n` | `:534` | Agent 的 `system_prompt` |
| 4 | Skills 段 `\n{...}\n` | `:569` | 有模块技能时 |
| 5 | Subagent router 段 `\n{...}\n` | `:656` | 空 |
| 6 | Workspace EXTRA_PROMPT 段 | `:422-428` | 空 |
| 7 | local/沙箱模式段 | `:442` | 空 |
| 8 | 工具调用段 `\n{...}\n` | `:1669` | full（本轮有工具时） |
| 9 | live 模式段 | `:1673` | 关 |
| 10 | 联网引用段 | `:1279` | 空 |

### 用户消息：content 块顺序

正文（经 `prompt_prefix` 包裹）→ 额外块 → 图片 → 音频（`provider/entities.py:188-261`）。
额外块顺序：媒体占位 → `<Quoted Message>` → `<system_reminder>` → 群聊上下文块。
**恰好一个文本块且无额外块/媒体时降级为纯字符串**（上游的向后兼容分支）。

### messages 结构

`[system] + begin_dialogs + history + [user]`（`tool_loop_agent_runner.py:308-323`，
persona 的 `begin_dialogs` 前插到历史最前：`:536`）。

## 与 AstrBot 的刻意差异（3 处）

| 差异 | 说明 |
|---|---|
| **Skills 段内容形态** | AstrBot 由 `SkillManager` 的 `SKILL.md` 清单生成（`## Skills` + 规则条目）；本框架的等价物是模块 `@skill` 注入的说明块。这里只对齐**位置与分隔符**，内容原样注入，不伪造上游模板 |
| **群聊上下文行的时间** | AstrBot 用**注入时刻**（`group_chat_context.py:198`），会把所有行写成当前时间；本插件用**该条消息自身**的时间戳（框架历史里存着真实时间） |
| **知识库段** | AstrBot 把检索结果作为 `[Related Knowledge Base Results]:` 追加到 user 侧额外块；本框架的知识库在框架侧注入，本插件不重复注入 |

未复刻（本框架无对应概念，且上游文案未取到原文，故不伪造）：音频/视频附件占位、
引用消息里的图片 caption、`computer_use_runtime` 的 sandbox/local 具体文案（后者提供了
`local_mode_prompt` 配置位可自行填入）。

## 相关配置键

见 `config_schema.py`：`persona_preset_id` / `prompt_prefix` / `llm_safety_mode` /
`tool_call_prompt_mode` / `identifier` / `group_name_display` / `datetime_system_prompt` /
`include_quoted_message` / `include_group_history` / `history_mode`（`raw` 才是 AstrBot 形态）
/ `media_placeholder` 等。

## 测试

```bash
venv\Scripts\python.exe -m pytest tests/test_astrbot_alignment_plugin.py -q
```

测试逐字断言 system prompt 的拼接结果、额外块顺序、降级规则，并卡住
「`DEFAULT_CONFIG` 与 `config_schema.SCHEMA` 的默认值一致」。
