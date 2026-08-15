# QQBot Next 2.0

基于 OneBot 协议的多账号 QQ 机器人框架，采用 **分层 + 插件** 架构。

## 架构总览

```
main.py / app/bootstrap.py      装配与生命周期入口
app/
├── core/                       核心内核（无业务依赖）
│   ├── settings.py             pydantic-settings 配置（.env，变量名=字段名大写）
│   ├── container.py            轻量 DI 容器
│   ├── logger.py               日志（6h 轮转 / 分级别文件 / 前缀 Logger）
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
data/                           SQLite（data/app.db）+ 框架级 LLM 数据（data/llm）
logs/                           日志目录
```

### 分层依赖规则

`core` → `domain` → `infrastructure` → `modules`/`services` → `webui`。依赖只向下，
装配只在 `bootstrap.py` 一处完成，循环依赖在启动时即被暴露。

## 快速开始

### 简单开始（推荐）

```bash
# 1. 自动创建虚拟环境并安装依赖
scripts\requirements.bat

# 2. 启动
start.bat
```

之后每次启动只需要运行 `start.bat`。

> Linux/macOS 可运行 `scripts/requirements.sh` 后执行 `start.sh`。

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

### 装饰器风格（推荐）

新架构支持用装饰器直观注册“模块流水线钩子”和“LLM 流水线钩子”：

```python
# module.py
from app.modules import BaseModule, module_hook, llm_hook

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
```

要点：

- 模块流水线在前，LLM 流水线在后；
- `event.llm.stop()` 跳过 LLM 回复；
- `continue` 是 Python 关键字，手动放行请用 `event.llm.resume()`；
- 内置 `llm_debounce` 模块演示了“多条消息防抖合并为一次 LLM 请求”。

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
- 移除了文件监听（watchdog），配置变更通过事件广播实时推送到 WebUI。

## 测试

```bash
venv\Scripts\python.exe -m pytest -q
```
