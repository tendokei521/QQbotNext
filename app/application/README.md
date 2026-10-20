> 本文只负责：说明 `app/application/` 放什么、不放什么。上层目录总览见 `docs/ARCHITECTURE.md`。

# `app/application/` —— 应用服务

**一句话：跨模块的「用例」，把 core / infrastructure / agent 拼成一个可被 WebUI 调用的动作。**

## 包含

| 文件 | 职责 |
|---|---|
| `bot_service.py` | Bot 生命周期 + 登录后模块装配（WebUI 面向的能力入口） |
| `cron_service.py` | 精确定时任务管理器（模块声明的 `SCHEDULES=` 定点任务；类名仍为 `SchedulerService`） |
| `log_service.py` | 日志读取（供 WebUI 展示最近日志） |
| `session_history_service.py` | 会话列表 / 对话线程 / 编辑 / 导出 / 图片管理 |
| `provider_service.py` | 配置动态数据源（Provider）注册表 |
| `provider_preset_service.py` | Provider 预设（把「连哪里」从 Agent 配置里独立出来） |
| `provider_model_service.py` | Provider 模型实例 / 全局设置 |
| `config_profile_service.py` | 配置档案与路由（UMO → 档案） |
| `role_preset_service.py` | 角色预设（可复用的系统提示词） |
| `module_install_service.py` | 插件软卸载状态（只写配置，不删目录） |
| `resource_registry.py` | 系统资源注册表（「可被引用的资源」统一成 kind → 候选列表） |
| `capabilities.py` | 框架能力清单（给插件与前端一份机器可读的「这个框架有什么」） |

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` `app.agent`、标准库、三方库。
- **不得** import `app.webui`——WebUI 调服务，服务不认识 WebUI。
- 这里**不做协议适配**（那是 `infrastructure/`），也**不做 LLM 决策**（那是 `agent/`）。

## 两个「scheduler」

`app/application/scheduler.py::SchedulerService`（230 行，声明式定点任务）与
`app/agent/proactivity/scheduler.py::TaskScheduler`（967 行，LLM 语义定时，落盘
`tasks_data.json`）**零复用**。改之前先确认改的是哪一个（`STYLE.md §12.3`）。

> 本目录原名 `app/services/`。改成 `application/` 是因为「services」在本仓库撞了三次：
> 这里（用例层）、`ModuleContext.services`（插件可用服务集合）、`ServiceAccess`。
> 现在 `application/` = 用例层，与分层图一致。
