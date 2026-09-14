# Changelog

## [Unreleased]

### 工具主动性与聊天记录

- 新增系统工具 `get_chat_history`（取代 `get_session_history`）：默认零参数、自动定位当前会话，
  本地记录不足时自动补拉 QQ 聊天记录（`scope=auto/local/qq`），取不到时给明确路标
- `get_chat_history` 支持跨会话：私聊里用 `group_name`/`group_id` 查“发起人自己也是成员”的群
  （结果只回给发起人，`history_cross_query_enable` 可关）；群聊里查别的群、查别人的私聊一律拒绝
- 新增输出侧动作通道：模型可用 `[reply]` 引用当前消息、`[@QQ]` 真实 @ 某人
  （`outbound_directive_enable`，默认开启；仅本轮首句生效，指令不会漏给用户）
- 新增唯一一块「主动性」system 提示：按本轮可用工具裁剪，只讲时机不讲参数；
  命中历史意图时在同一块内补强「必须先查记录」
- 新增戳一戳节流 `poke_cooldown_seconds`（默认 20s，同会话同一人），失败不占冷却
- 修复 `fetch_*_online_history` 未解包 OneBot 响应信封（`data.messages`）导致
  群聊环境背景 / 私聊前历史 / 主动发言群背景长期静默失效的问题
- 修复工具以 `error:` 文本报错时被记为 `success=True`（1404 等接口错误此前不可见）
- 修复工具名与 OneBot action 不一致：删除伪工具 `get_msg_history`，
  为 `.ocr_image` / `.handle_quick_operation` 补 action 别名

### LLM 可扩展性优化（P0/P1）

- 新增 LLM 可观测性：`/agent/telemetry` 记录延迟 / token / provider / model / 工具 / 钩子耗时
- 会话历史从 JSON 目录扫描迁移到 SQLite 索引存储，旧 JSON 自动导入
- 新增会话级异步锁与 LlmPool 默认串行化，避免同会话并发请求/写历史
- Provider 增加能力协商与运行期注册；新增 Anthropic / Gemini 原生适配器
- 工具增加权限与作用域：`@tool(permission=..., scopes=[...])`
- 知识库检索可选启用 sqlite-vec ANN 后端，未安装时自动回退 SQLite 余弦扫描

### Agent 配置前端重构

- Agent 配置从通用 schema 表单升级为领域化页面
- 新增：概览 / 基础配置 / 模型 / 对话行为 / 流式回复 / 权限 / 知识库 / MCP / 定时任务与主动消息
- 新增共享 `agentConfig` Store，统一跨页草稿与自动保存
- 流式回复新增三档预设：快速（500–1000ms）、正常（1000–2000ms）、偏慢（3000–4000ms）

### 日志导出

- 设置页新增“导出日志”弹窗
- 日志列表放入固定滚动框，按时间段默认折叠展示
- 支持当前轮次与历史归档日志选择，并打包 ZIP 下载

### NapCat 工具

- 新增 `IBot.call_api` 通用 API 入口
- 新增数据驱动的 NapCat/OneBot 工具包：`app/llm/napcat/`
- Agent 新增 NapCat 工具配置页，可按风险等级/权限/作用域开关工具

## [2.0.0] - 2026-08-17

QQBot Next 2.0 首个正式 Release：基于 OneBot 协议的多账号 QQ 机器人框架，采用分层 + 插件架构。

### 架构与框架

- 核心重组为 `core → domain → infrastructure → modules/services → webui` 单向依赖分层
- 统一 DI 容器装配，生命周期集中在 `app/bootstrap.py`
- 类型化事件总线与统一后台任务管理（可追踪、级联取消）
- pydantic-settings 配置中心，SQLite 持久化并自动迁移旧 JSON 配置

### 全新 Dashboard

- Vue 3 + Vuetify 3 新版管理后台，`dashboard/dist` 已纳入版本库
- 模块分类 / 搜索 / 折叠 / 网格视图
- Agent 独立入口，账号管理与配置表单实时保存
- 日志面板支持简洁 / 原始双模式，与控制台输出同步
- 旧版 UI 保留为 `/legacy` 回退入口

### LLM Agent

- 模块可为 LLM 注册工具（`@tool`）与技能（`@skill`），携带 `ToolContext`
- 模块流水线钩子与 LLM 流水线钩子装饰器
- 流式输出、句子级发送、带 tools 的多轮工具循环
- 主动消息 / 定时任务流式发送支持
- 用户信息感知、回复打断、防抖合并等能力并入 `llm_enhance` 模块

### 插件与模块

- 新增群申请管理、戳一戳回复、今天吃什么等插件
- 防撤回模块按事件语义拆分入口，一事件一处理函数
- 权限系统重构为语义化角色 + 模块级过滤

### 稳定性与修复

- WebSocket 连接级失败断开处理
- 配置组件渲染问题修复，动态 / 重复列表支持 `string_list`
- 日志轮转与双视图稳定

### 测试

- 全量测试基线：`168 passed`