> 本文只负责：说明 `app/agent/tools/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/tools/` —— 工具协议与内置工具：模型能调用的能力从此声明。

## 包含

| 文件 | 职责 |
|---|---|
| `spec.py` | `ToolSpec` 协议（并发 / 超时 / 敏感级声明）+ `@tool` 装饰器 + `ToolContext`；`make_executor` 是唯一执行出口 |
| `policy.py` | **注入策略**：这个 Bot 此刻允许把哪些工具交给模型（纯读配置） |
| `result.py` | 结构化工具结果 `ToolResult` + 9 个错误码；`"error:"` 前缀双读过渡 |
| `spill.py` | 超大结果落盘：超 `tool_spill_max_bytes` 才落，内容寻址 + 原子写，失败保留原文 |
| `recall.py` | 取回工具 `recall_tool_result`：只读本账号 spill 目录，分页 |
| `approval.py` | 高危工具人工审批：走 `runtime.approval_channel`，fail-closed |
| `system.py` | 系统工具目录（收集 + 标注 enabled/ready，供 WebUI 展示） |
| `context_tools.py` | 上下文展开类工具（expand_*） |
| `session_tools.py` | 会话类工具（get_current_session / get_chat_history） |
| `web_search.py` | Tavily 联网搜索工具 |
| `mcp.py` | MCP 协议工具接入 |
| `onebot/` | OneBot 工具清单与执行（`manifest.json` 175 条 + 权限分级） |

## 注意

**`policy.py` 与 `system.py` 是有意拆开的两件事**：策略决定「注入什么」，
目录决定「展示什么」。合并会把主循环拖进 `system.py` 的依赖里造环（P3 已修）。
执行统一走 `make_executor`；一批工具内部的并发由 `loop/tool_loop.execute_tool_batch`
调度（有界池 + 独占屏障，回填按模型顺序），超时只换结果、不 cancel 协程。
加一条 OneBot 工具 → 改 `onebot/manifest.json`，不要写 Python 字面量；
`tests/test_onebot_manifest_data.py` 会校验结构、敏感级别与 JSON 大小上限。

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` 与本包内文件。
- 不得 import `app.webui` / `app.bootstrap`。跨子包依赖请走对方 `__init__.py` 的导出。
- 新增文件前先问：它属于「取料 / 决策 / 发送 / 存储」哪一类？答不上来就别加。
