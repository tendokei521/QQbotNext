> 本文只负责：说明 `app/plugin/` 放什么、不放什么。业务插件本身在仓库根的 `module/`，见 `docs/MODULE_DEV.md`。

# `app/plugin/` —— 插件框架

**一句话：插件的运行时（加载、分发、权限、能力接管），不含任何具体插件。**

## 边界：这里 vs `module/`

| | `app/plugin/`（本目录） | `module/`（仓库根） |
|---|---|---|
| 是什么 | 插件**框架** | 插件**本体**（bilibili_parser、douyin_parser …） |
| 谁改 | 改框架才动这里 | 加/改业务功能动那里 |
| 数量 | 14 文件 | 13 个插件目录 |

⚠️ 两者名字太像。P5 计划把本目录改名 `app/plugin/`、`module/` 改名 `plugins/`，
届时 `app/plugin/`（框架）与 `plugins/`（业务）不会再认错。

## 包含

| 文件 | 职责 |
|---|---|
| `base.py` | `BaseModule` / `ModuleConfig` / `ModuleAuthority` |
| `registry.py` | 扫描 / 加载 / 热重载模块 |
| `dispatcher.py` | 事件分发（订阅匹配 + 启停 + 单一服务 + 权限） |
| `api.py` | **插件开发 API**（`get_modules` / `get_config_path` / `get_data_path` / `get_features`） |
| `features.py` | 能力注册表（谁接管了「LLM 回复」这类能力） |
| `authority.py` | 权限系统（黑白名单 + 等级 + 单一服务门控） |
| `hooks.py` | `@module_hook` / `@llm_hook` / `@send_hook` / `@tool_call_hook` |
| `commands.py` | 统一命令解析与分发：`@command` / `@command_group`（隐藏参数 `skip_llm` 默认命中即截断 LLM） |
| `keyword.py` | 关键词匹配原语 |
| `nodes.py` | 消息节点（合并转发）辅助 |
| `groups.py` | 群开关检查 |
| `requirements.py` | 插件依赖声明与校验 |
| `resource_validation.py` | 插件资源（manifest 等）校验 |
| `schema_utils.py` | 配置 schema 与 WebUI 表单的互转 |

## `api.py` 的依赖方向（别改坏）

`api.py` 里四个便捷函数是**无参**的（插件不该被迫先拿容器），所以需要一个「当前容器」
入口。它取自 `app.core.container.get_active()`，**不是** `app.bootstrap`——后者会把
装配根拉进强连通块（`tests/test_architecture_boundaries.py` 的 `bootstrap_imports=0` 守着）。
