"""工具层：模型能调用什么、怎么调用。

## 本子包只负责

工具协议与内置工具集：`@tool` 装饰器、`ToolContext`、OpenAI function-calling
的 spec 生成、各类内置工具的实现与权限/风险判定。

## 不负责

- 何时调用、调用几次（`loop/tool_loop.py`）；
- 工具权限的**用户配置**（`app/modules/` 与 `agent/config.py`）。

## 文件地图

| 文件 | 职责 |
|---|---|
| `spec.py` | `tool` 装饰器、`ToolContext`、`ToolSpec` 生成 |
| `system.py` | 系统级工具（状态、配置查询） |
| `context_tools.py` | 语境类工具（查历史、查成员、查焦点） |
| `session_tools.py` | 会话类工具（记忆的写/删、话题切换） |
| `mcp.py` | MCP 协议工具接入 |
| `web_search.py` | 联网搜索（Tavily） |
| `onebot/` | OneBot API 工具：`manifest.json` 是数据，`executor.py` 是执行，`security.py` 是策略 |
"""
