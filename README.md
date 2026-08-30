# QQBot Next 2.0

基于 OneBot 协议的多账号 QQ 机器人框架，采用 **分层 + 插件** 架构。

## 架构总览

```
main.py / app/bootstrap.py      装配与生命周期入口
app/
├── core/                       核心内核（无业务依赖）
│   ├── settings.py             pydantic-settings 配置（.env，变量名=字段名大写）
│   ├── container.py            轻量 DI 容器
│   ├── logger.py               日志（6h 轮转 / debug+warn+errors+user / 简洁+原始双视图）
│   ├── task_manager.py         统一后台任务管理（可追踪、级联取消）
│   └── event_bus.py            类型化事件总线（框架级发布订阅）
├── domain/                     领域层（协议无关）
│   ├── bot.py                  IBot 抽象（模块只依赖它收发消息）
│   ├── events.py               类型化事件（消息/通知/申请）
│   └── message.py              消息段模型
├── infrastructure/             基础设施（协议适配 / 持久化）
│   ├── onebot/                 OneBot 实现：gateway(连接/去重) + client(API) + codec(编解码)
│   ├── config/config_service.py 配置中心（SQLite + 内存缓存 + 变更广播）
│   └── persistence/database.py SQLite（aiosqlite）
├── llm/                        框架级 LLM Agent（provider/会话/定时/主动/工具）
├── modules/                    插件框架 + 插件 API
│   ├── base.py                 BaseModule / ModuleConfig / ModuleAuthority
│   ├── registry.py             扫描 / 加载 / 热重载模块
│   ├── authority.py            权限系统（黑白名单 + 等级）
│   ├── api.py                  插件 API（get_modules / get_config_path / get_data_path）
│   └── dispatcher.py           事件分发（订阅匹配 + 启停 + 单一服务 + 权限）
├── services/                   应用服务（Bot 生命周期 / 调度器 / 日志）
└── webui/                      WebUI（FastAPI 分层：api/ + schemas/ + templates/）
module/                         插件目录（三级结构）
├── modules/                    业务插件主体（每个子目录 = 一个插件）
├── configs/                    每模块配置（<name>/config.json + authority.json，迁移源）
└── data/                       每模块可选持久化数据（get_data_path 自动创建）
data/                           SQLite（data/app.db）+ 框架级 LLM 数据（data/llm，含历史/记忆/知识库）
logs/                           日志目录
```

### 分层依赖规则

`core` → `domain` → `infrastructure` → `modules`/`services` → `webui`。依赖只向下，
装配只在 `bootstrap.py` 一处完成，循环依赖在启动时即被暴露。

## 快速开始

### 简单开始（推荐）

linux环境下需要执行 `dos2unix requirements.sh`进行换行符清洗

```bash
# 1. 自动创建虚拟环境并安装依赖
scripts\requirements.bat

# 2. 启动
start.bat
```

之后每次启动只需要运行 `start.bat`。

> Linux/macOS 可运行 `scripts/requirements.sh` 后执行 `start.sh`。

## WebUI（管理后台）

内置两套前端：

| 入口 | 说明 |
|------|------|
| `/` | **新版 Dashboard（Vue 3 + Vuetify 3）**，`dashboard/dist` 存在时自动启用 |
| `/legacy` | 旧版 UI（原生 HTML/JS），始终可访问，作回退 |

新版 Dashboard 源码在 [`dashboard/`](dashboard/)，风格对齐 AstrBot dashboard（浅/暗双主题、卡片化布局、实时日志控制台、模块配置 schema 表单等）。构建方法：

```bash
cd dashboard
pnpm install          # 首次
pnpm build            # 产物输出到 dashboard/dist，浏览器刷新即可生效
```

> 未构建（或 `dashboard/dist` 不存在）时服务自动回退到旧版 UI，互不影响。
> `dist` 已纳入版本库，克隆后无需 Node 环境即可直接使用新版 UI。

### 手动启动

```bash
# 首次启动
venv\Scripts\activate.bat          # 激活虚拟环境
pip install -r <pyproject>        # 或 pip install -e .
python main.py
```

首次启动会：
1. 读取 `.env`（缺省用默认值，参考 `.env.example`）；
2. 初始化 `data/app.db`；
3. **自动从旧 JSON 文件迁移配置**（`webserver/webconfig.json`、`webui/webui_config.json`、
   `module/configs/*/config.json`、`module/configs/*/authority.json`）——首次运行后以 SQLite 为准；
4. 按配置自动连接 `auto_connect=true` 的账号，加载对应模块；
5. 启动 WebUI（默认 `http://127.0.0.1:9200`）与定时调度器。

### 日志

- **文件**：`logs/debug.log`（原始完整）、`warn.log`、`errors.log`、`user.log`（用户简洁日志）
- **轮转**：四个文件每 6 小时同步归档到 `logs/YYYY-MM-DD-HH/`，保留 48 份
- **导出**：设置页可打开日志导出弹窗，在固定滚动框中按时间段折叠选择日志，支持打包 ZIP 下载
- **WebUI**：日志面板默认显示简洁日志；开启“显示原始日志”后显示完整技术日志
- **控制台**：控制台输出与 WebUI 的“显示原始日志”开关同步；无论开关状态，`debug.log` 始终完整记录

### 关键配置（.env）

| 变量 | 默认 | 说明 |
|------|------|------|
| `WEBUI_HOST` | `127.0.0.1` | WebUI 监听地址（外网访问请配合反向代理） |
| `WEBUI_PORT` | `9200` | WebUI 端口 |
| `WEBUI_TOKEN` | 空 | 非空则 API 需 `Authorization: Bearer <token>` |
| `DB_PATH` | `data/app.db` | SQLite 路径 |
| `WS_CONNECT_TIMEOUT` | `30` | WebSocket 连接超时 |

## 开发一个插件

> 完整模块开发文档见 [`docs/MODULE_DEV.md`](docs/MODULE_DEV.md)，包含每个钩子的参数与用法。

在 `module/modules/<name>/` 下新建文件（模块内部一律用**相对导入**，与目录位置解耦）：

```python
# module.py —— 声明
from app.modules import BaseModule
from .config_schema import SCHEMA

class Module(BaseModule):
    name = "插件名"
    sign = "Sign"
    description = "功能描述"
    permission = "member"            # everyone/member/group_admin/group_owner/owner
    subscribe = ("message_group", "message_private")   # 订阅的事件类型
    default_config = {"greeting": "你好"}
    config_schema = SCHEMA

    async def handle(self, event):
        from .service import handle
        await handle(self, event)
```

```python
# service.py —— 业务
from app.core.logger import module_logger

async def handle(module, event):
    log = module_logger.add_info(f"#{event.bot_index}").add_info(module.name)
    if event.message_type == "group":
        await event.bot.send_group_msg(event.group.group_id, "回复内容")
    else:
        await event.reply("快捷回复到当前会话")
```

```python
# config_schema.py —— 可选，WebUI 表单
SCHEMA = {"greeting": {"type": "text", "label": "问候语", "default": "你好"}}
```

插件可通过 `module.config.get(key)` / `module.authority.enabled` / `module.ctx.services.cache`
访问配置、启停状态与缓存；通过 `event.bot` 调用全部 OneBot API。

插件可以声明接管框架内置能力：`provides` 表示本插件提供的能力，`supersedes` 表示启用时
自动接管并禁用对应框架能力，卸载/禁用时自动恢复。内置能力见
`docs/MODULE_DEV.md` 的「全局能力注册表」。

### 装饰器风格（推荐）

新架构支持用装饰器直观注册“模块流水线钩子”和“LLM 流水线钩子”：

```python
# module.py
from app.modules import BaseModule, module_hook, llm_hook, send_hook

class Module(BaseModule):
    # 模块流水线：按事件类型注册处理函数（subscribe 可自动推导）
    @module_hook("message_group", order=10)
    @module_hook("message_private", order=10)
    async def on_message(self, event):
        if event.text == "ping":
            await event.reply("pong")
            event.llm.stop()          # 已处理，跳过 LLM

    # LLM 流水线：请求前钩子（可暂停/防抖）
    @llm_hook("pre_request", event_type="*", order=10)
    async def before_llm(self, ctx):
        await ctx.event.llm.wait_continue()   # 等待 event.llm.resume()

    # LLM 流水线：请求后拆分多条消息
    @llm_hook("post_response", order=20)
    async def after_llm(self, ctx):
        from app.domain.message import Message
        parts = [ctx.response_text[i:i+50] for i in range(0, len(ctx.response_text), 50)]
        ctx.response_messages = [Message.from_text(p) for p in parts]

    # 消息发送成功后可拿到响应里的 message_id
    @send_hook(message_type="*", order=10)
    async def after_send(self, ctx):
        message_id = ctx.message_id
```

框架还提供 `@before_send_hook`（发送前拦截/改写）、`@api_hook`（任意 OneBot API 调用后）、
`@bot_lifecycle_hook`（登录/断线）、`@event_completed_hook`（事件处理完成）、
`@tool_call_hook`（LLM 工具调用后），详见 `docs/MODULE_DEV.md`。

要点：

- 模块流水线在前，LLM 流水线在后，发送成功钩子独立于两条流水线；
- `event.llm.stop()` 跳过 LLM 回复；
- `continue` 是 Python 关键字，手动放行请用 `event.llm.resume()`；
- 内置 `llm_enhance` 模块演示了“防抖合并 + 群聊用户信息感知”。

### LLM 工具与技能

模块可以把能力暴露给 LLM：

```python
from app.llm import tool, skill

class Module(BaseModule):
    @tool(description="查询天气", parameters={"type": "object", "properties": {"city": {"type": "string"}}})
    async def query_weather(self, ctx, args):
        return "晴"

    SKILLS = {
        "周报助手": {
            "description": "用户说'写周报'时使用",
            "instructions": "1. 收集数据 2. 按三段输出",
            "tools": ["query_weather"],
        }
    }
```

- `@tool`：注册为 LLM function calling 工具（带 `ToolContext` / 超时 / 截断）
- `SKILLS` / `@skill`：注入 system prompt 的技能说明
- 模块 `config` 里可用 `tools_enabled` / `skills_enabled` 单独开关工具与技能

工具支持**权限与作用域**：

```python
@tool(
    description="删除群消息",
    parameters={...},
    permission="group_admin",      # everyone/member/group_admin/group_owner/owner
    scopes=["group"],              # group / private / ["*"]
)
async def delete_message(self, ctx, args):
    ...
```

### 模型 Provider

框架内置三类原生适配器：

| Provider | 说明 |
|---|---|
| `openai` | OpenAI 兼容协议（DeepSeek / Ollama / OpenRouter 等） |
| `anthropic` | Anthropic Claude Messages API |
| `gemini` | Google Gemini Generative Language API |

第三方插件还可以用 `register_provider(name, cls, aliases=...)` 运行期注册新适配器。

### LLM 可观测性

每次 LLM 请求会记录延迟 / token / provider / model / 工具调用 / LLM 钩子耗时，可以通过 WebUI 读取：

```http
GET /agent/telemetry?bot_id=<qq>&limit=30
POST /agent/telemetry/reset?bot_id=<qq>
```

### Agent 配置页面

Agent 配置已从通用表单升级为**专属领域页面**：

| 页面 | 内容 |
|---|---|
| 概览 | 配置状态摘要与入口 |
| 基础配置 | 提示词、群/私聊开关、历史、触发 |
| 模型 | Provider 模型池与模型参数 |
| 对话行为 | 用户信息感知、回复打断、冷却 |
| 流式回复 | 流式开关与发送节奏预设 |
| 权限 | 黑白名单与角色权限 |
| 长期记忆 | 记忆开关、召回与可信度 |
| 知识库 | 知识库检索与 Embedding 模型 |
| MCP 工具 | MCP stdio server 配置 |
| Napcat Tools | 把 NapCat/OneBot API 暴露给 LLM |
| 定时任务 / 主动消息 | 任务与主动发言管理 |

流式回复内置三档发送节奏预设：

- **快速**：平均约 500–1000ms
- **正常**：平均约 1000–2000ms
- **偏慢**：平均约 3000–4000ms

### 流式输出（带 tools）

在 LLM 配置开启 `stream_output` 后，LLM 回复会按句子流式发送，并且仍然支持定时任务等工具调用。

- 每个完整句子都会触发 `pre_send` / `post_send` 钩子；
- 整个流结束后触发 `post_stream` 钩子；
- 工具调用通过流式 `tool_calls` 碎片累积解析，支持多轮工具循环；
- 单句最大长度由 `stream_sentence_max_length` 控制。

### 插件 API（`from app.modules import ...`）

| 函数 | 用途 |
|---|---|
| `get_modules()` | 已加载模块对象列表 |
| `get_config_path(name)` | 模块配置目录 `module/configs/<name>/` |
| `get_data_path(name)` | 模块数据目录 `module/data/<name>/`（自动创建，供持久化自定义数据） |

配置/权限迁移源文件：`module/configs/<name>/config.json` + `authority.json`（首次启动迁移进 SQLite，之后以 SQLite 为准）。

## 事件类型（subscribe 可用值）

| 类型 | 说明 |
|------|------|
| `message_group` / `message_private` | 群/私聊消息 |
| `notice_poke` | 戳一戳 |
| `notice_group_emoji` | 群表情回应 |
| `notice_group_recall` / `notice_private_recall` | 撤回 |
| `notice_group_increase` / `notice_group_decrease` | 入群/退群 |
| `request_group` / `request_private` | 加群/加好友申请 |
| `time_core` | 定时调度（整分钟触发，可判断 `event.hour/minute`） |

## 与旧版（v1）的差异

- 旧 `basic/`、`webserver/` 中的核心逻辑已重组进 `app/` 分层包，业务代码零直接依赖传输层；
- 模块配置/权限/账号配置从 JSON 文件迁移到 SQLite（首次启动自动迁移，旧 JSON 保留为迁移源）；
- 消息收发统一走 `event.bot`（IBot 抽象），不再透传 websocket；
- 后台任务经 `TaskManager` 统一管理，模块热重载自动级联取消；
- 移除了文件监听（watchdog），配置变更通过事件广播实时推送到 WebUI；
- 日志改为双系统：`debug.log` 保留原始完整日志，新增 `user.log` 简洁日志，`debug/warn/errors/user` 四文件同步 6h 轮转；WebUI“显示原始日志”开关与控制台输出同步。

## 测试

```bash
venv\Scripts\python.exe -m pytest -q
```
