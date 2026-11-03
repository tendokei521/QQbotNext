# 架构

> 读者：要改框架代码 / 判断"这段逻辑该放哪"的人。
> 只写**当前事实**；历史演进看 commit message，不要凭记忆补。

---

## 1. 一句话

一个 Python 进程里同时跑：**若干 QQ 账号的 OneBot 连接**、**一个 WebUI 管理后台**、**一个定时调度器**，
以及**每个账号一套 LLM 运行时**（模型、人格、记忆、插件、工具）。

---

## 2. 分层与依赖方向

```
app/core/            最底层：配置、日志、事件总线、任务管理、容器、脱敏
app/domain/          纯领域规则（无 IO）：消息/会话/事件/触发/权限/节奏/切分
app/infrastructure/  唯一 IO 出口：SQLite 持久化、OneBot 编解码与连接、配置存储、文件路径
app/plugin/          插件框架：发现/加载/生命周期/钩子/权限/指令/工具源/仲裁
app/agent/           LLM 对话核心：一次请求的完整路径（组装→调用→工具循环→投递）
app/plugins/         内置插件（业务能力，全部以插件形态存在）
app/application/     用例服务：BotService、AgentManager、配置/模型/人格/会话/统计等
app/webui/           HTTP API + 静态前端（Vue 3 + Vuetify 3，产物 dist 进仓库）
```

**四条硬规则**（`tests/architecture/test_architecture_boundaries.py` 守着）：

1. `domain/` 只依赖 `core/` 和自身（纯规则、可单测、无 IO）；
2. `infrastructure/` 不依赖上层（`agent` / `plugin` / `application` / `webui`）；
3. 生产代码不许 `from app.bootstrap import ...`（装配根只做装配）；
4. `app/agent/__init__.py` 保持薄壳，不重导出 Agent 主循环。

---

## 3. 启动与装配

```
main.py
  └─ app.bootstrap.run()
       ├─ Settings（app/core/settings.py：.env + 环境变量，变量名=字段名大写）
       ├─ ConfigService（SQLite data/app.db）      ← 配置/账号/模块/模型/人格的唯一真源
       ├─ AgentManager（每账号一个 AgentRuntime）
       ├─ ModuleRegistry.discover() → 按账号装配模块实例
       ├─ OneBotGateway（正向/反向 WebSocket 连接协议端）
       ├─ TaskScheduler / CronService（定时任务）
       └─ WebUI（FastAPI：/api/* + dist 静态资源）
```

`app/bootstrap.py` 是**唯一**允许把这些拼起来的地方；其余模块通过运行时属性或依赖注入取用。
`app/core/container.py` 的服务定位器是给 WebUI 用的，业务代码不要当全局变量用。

---

## 4. 一次请求怎么走完

```
OneBot 事件（WebSocket）
  └─ infrastructure/onebot     解析成 domain.events.*
      └─ plugin.dispatcher     按账号分发
          ├─ 模块自己 respond()（多模块争抢时由「插件响应策略」仲裁出唯一赢家）
          └─ 框架：AgentRuntime → loop.turn_runner
                ├─ loop.turn_input     归一化输入（去抖、合并、引用、图片）
                ├─ loop.prompt_prep    取历史/记忆/上下文/工具，构造 PromptRequest
                ├─ assembly.assembler  五层装配成 messages（见 §5）
                ├─ providers           provider 链（重试 / 切换备用模型）
                ├─ loop.tool_step      执行工具调用、回流结果、继续下一轮
                └─ delivery            按输出策略投递（纯文本 / 流式 / 发言通道）
```

**两条路径必须一致**：流式（`loop/chat.py`）与非流式（`loop/turn.py`）共用同一套 prompt 准备与工具循环，
差异只在投递；`tests/architecture/test_turn_path_parity.py` 守这条。

---

## 5. 提示词：五层装配

`app/agent/assembly/layers.py` 定义唯一的层模型，顺序固定：

| 层 | 内容 | 谁产出 |
|---|---|---|
| **L1 系统提示词** | 规则与协议：安全规则、工作方式、表达底线、定时/主动性/消息格式说明、结尾的底线再声明 | 提示词方案（`prompts/presets.py`）+ 内置段落 |
| **L2 人格预设** | 「我是谁」，唯一来源是人格预设 | `persona_preset_id` |
| **L3 钩子注入** | 插件与本轮动态信息：记忆、上下文、群记录、表情标签… | 模块 `register_section` / `context_blocks` |
| **L4 对话历史** | 窗口 / 压缩 / 开场对话 | `assembly/history_window.py`、`compress.py` |
| **L5 本轮消息** | 正文 + 元信息 + 引用 + 图片 | `loop/turn_input.py` |

约定：

- **L1 在层内合并成一条 system**（`assembler.merge_layer_blocks`），所以 L1 只占一条消息；
- **层序优先于段的 `order`**：插件段默认落 L3，要进 L1 必须显式声明 `layer="system_prompt"`；
- `describe()` 返回 `[{block, layer, role, chars, messages, contributors}]`；开调试时 `[Prompt]`
  头行打「层摘要 + system token 估算 + 前缀指纹」，排障先看这一行；
- 前缀稳定性由 `tests/architecture/test_prompt_prefix_stability.py` 守住（同配置 → 同前缀）。

---

## 6. 配置体系

- **真源**：`data/app.db`（`ConfigService`）。`.env` 只管进程级开关（端口、日志目录、DB 路径……）。
- **分区**：`app/agent/config_partition.py` 把默认键切成 **本体键**（Agent 页面渲染）与
  **模块键**（各模块自己的配置页持有），一个字段只有一个编辑入口。
- **Schema**：`app/agent/config_schema.py` 管渲染（分组、显隐、类型、资源引用）；
  模块的 schema 在自己目录的 `config_schema.py`。
- **读策略键**：机制代码用 `app/agent/policy.py` 的 `policy_config(runtime, kind)` /
  `policy_get(...)` / `policy_view(runtime)`——模块接管后读模块值，未接管回退 Agent 配置。
- **迁移**：`scripts/migrate_*.py` + 启动期 best-effort 迁移（`app/application/*_migration.py`），
  要求幂等、可 dry-run、失败不阻断启动。

---

## 7. 插件体系（写法见 [MODULE_DEV.md](MODULE_DEV.md)）

| 文件 | 职责 |
|---|---|
| `plugin/loader.py` | 按路径加载模块（物理位置与 import 名解耦）、建实例、依赖自检 |
| `plugin/registry.py` | 每账号一份实例表；`discover / load_single / unload_single / reload_all` |
| `plugin/base.py` | `BaseModule`：声明字段、`on_load/on_unload`、hook 注册、`spawn`、`llm_turn` 等 |
| `plugin/hooks.py` | 钩子总线（`@llm_hook` / `@command` / 事件订阅） |
| `plugin/dispatcher.py` + `responder.py` | 事件分发与插件响应仲裁 |
| `plugin/authority.py` | 模块授权、单服务声明 |
| `plugin/resource_validation.py` | 配置里"资源引用"字段的悬挂值策略 |

模块只管自己的业务；框架不认识"记忆""心情"这类概念。

---

## 8. 目录速查（改 X 去哪）

| 想改什么 | 先看 |
|---|---|
| 一次回复的流程 / 卡在哪一步 | `app/agent/loop/{turn_runner,pipeline,generate,chat}.py` + 日志 |
| 提示词内容 / 段落顺序 | `app/agent/{assembly/layers.py,prompts/system.py,prompts/sections.py,prompts/presets.py}` |
| 工具怎么暴露、什么时候不可用 | `app/agent/tools/{spec,policy}.py` + [TOOL_STATE.md](TOOL_STATE.md) |
| 模型调用、重试、故障转移 | `app/agent/providers/*`（`runtime_manager.py` 管实例） |
| 人格 / 记忆 / 上下文 / 触发调度 | 对应内置插件 `app/plugins/llm_*` |
| 配置项 / 默认值 / 页面渲染 | `app/agent/config{,_schema,_partition}.py` + [AGENT_CONFIG.md](AGENT_CONFIG.md) |
| WebUI 接口 | `app/webui/api/*.py`（每个文件一组资源） |
| 前端页面 | `app/webui/dashboard/src/` + [dashboard-frontend-architecture.md](dashboard-frontend-architecture.md) |
| 账号连接 / OneBot 报文 | `app/infrastructure/onebot/*` |
| 数据落库 | `app/infrastructure/persistence/*`；会话历史另见 `app/agent/session/` |

---

## 9. 可观测性

- **日志**：`app/core/logger.py` 分 `debug/warn/errors/user` 四份文件，按账号打标签；
  WebUI 日志页可切"简洁/原始"。
- **用量**：`app/agent/usage/*` + `telemetry.py` 记每次模型调用的 token/耗时/归属，
  统计页读它（口径见 [DATA_STATS_DESIGN.md](DATA_STATS_DESIGN.md)）。
- **提示词对账**：`assembly/dump.py`（`prompt_dump_enable`）把每轮最终 messages 落盘，逐条 diff。
- **就绪检查**：`application/readiness_service.py` → `GET /api/readiness`，回答"差什么、去哪修"。

---

## 10. 验收闸门（改架构相关代码至少跑这些）

```bash
venv\Scripts\python.exe -m pytest tests/architecture -q
venv\Scripts\python.exe -m ruff check app tests
```

`tests/architecture/` 每个文件守一条结构性契约（依赖方向、目录布局、插件布局、配置分区、
提示词分层、前缀稳定、运行时属性、UI 文案、Vue SFC 结构……）。测试总策略见 [TEST_POLICY.md](TEST_POLICY.md)。
