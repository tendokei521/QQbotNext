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
├── infrastructure/             基础设施（协议适配 / 持久化 / 文件系统）
│   ├── onebot/                 OneBot 实现：gateway(连接/去重) + client(API) + codec(编解码)
│   ├── config/config_service.py 配置中心（SQLite + 内存缓存 + 变更广播）
│   ├── fs/                     数据路径（paths / media_cache）——全库唯一的数据目录来源
│   └── persistence/database.py SQLite（aiosqlite）
├── agent/                      框架级 Agent（11 个子包，每个子包一个职责）
│   ├── loop/                   一轮对话的主流程（chat / pipeline / context / tool_loop）
│   ├── assembly/               把上下文拼成 messages（assembler / inputs / prompt / 压缩）
│   ├── session/                会话与历史（manager / history / model / enrich）
│   ├── persona/                人格与表达（rules / skills / tags / emoji + QQ 表情数据）
│   ├── context/                群语境与指代（group / referent / focus / nicknames）
│   ├── tools/                  工具协议与内置工具（spec / policy / system / onebot / mcp）
│   ├── delivery/               发送侧（speech / pool / pace / splitter / directives）
│   ├── media/                  图片与媒体（image / store / animated）
│   ├── proactivity/            主动消息与定时（manager / scheduler / stream / trigger）
│   ├── providers/ LLM 供应商适配（全库最规范的部分，新代码的范本）
│   └── memory/ knowledge/ group_log/   长期记忆 / 知识库 / 群聊日志
├── modules/                    插件框架 + 插件 API
│   ├── base.py                 BaseModule / ModuleConfig / ModuleAuthority
│   ├── registry.py             扫描 / 加载 / 热重载模块
│   ├── authority.py            权限系统（黑白名单 + 等级 + 单一服务门控）
│   ├── api.py                  插件 API（get_modules / get_config_path / get_data_path）
│   └── dispatcher.py           事件分发（订阅匹配 + 启停 + 单一服务 + 权限）
├── services/                   应用服务（Bot 生命周期 / 调度器 / 日志）
├── plugins/                    **业务插件**（14 个，每个子目录 = 一个插件）
└── webui/                      WebUI（FastAPI 分层：api/ + schemas/；前端在 app/webui/dashboard/）
module/                          插件**运行时数据**（不含代码）
├── configs/                     每模块配置（<name>/config.json + authority.json，迁移源）
├── data/                        每模块可选持久化数据（get_data_path 自动创建）
└── plugins/                     外部 zip 安装的插件（用户安装，不进仓库）
data/                            SQLite（data/app.db）+ 框架级 Agent 数据（data/llm，含历史/记忆/知识库）
logs/                           日志目录
```

### 分层依赖规则

`core` → `domain` → `infrastructure` → `modules`/`services` → `webui`。依赖只向下，
装配只在 `bootstrap.py` 一处完成，循环依赖在启动时即被暴露。

## 快速开始

### 前置条件

| 依赖 | 要求 | 说明 |
|---|---|---|
| Python | **3.10 – 3.13**，安装时勾选「Add Python to PATH」 | `start.py` 会自检；版本不符时退出码 4 并直接说明，不会让你去查网络 |
| 协议端 | NapCat / Lagrange / LLOneBot **任选其一** | 本程序通过 OneBot v11 WebSocket 收发消息，**不自带 QQ 客户端**；协议端负责登录 QQ |
| 网络 | 能访问 PyPI（或已配镜像） | 首次运行要装依赖；离线环境可用 `--check` 先自检 |

> 只想先看界面：`python start.py --check` 只做只读检查，不联网、不装包、不启动（也不会替你建 venv，环境没准备好时会直接说明）。

### 两条路：免安装包 / 一条命令

**免安装包（推荐给不想碰命令行的用户）**：从 Release 下载 `QQBotNext-*-kit.zip`，
解压后运行 `python start.py`（macOS/Linux 可用 `./start.sh`）。首次启动自动建环境、装依赖，
然后打开浏览器进「首次配置」向导。包里带 `deploy.json`（逐文件 sha256）和
`README-FIRST.txt`（三步上手）。

想自己构建发行包（含离线依赖）：

```bash
python scripts/build_deploy_kit.py --version 2.0.0              # 产物到 dist/kit/
python scripts/build_deploy_kit.py --with-wheelhouse            # 连离线依赖一起打（需联网）
python scripts/build_deploy_kit.py --embed-python <python-embed.zip>   # 内嵌解释器
```

> 包内**不含** `venv/`、`data/`、`logs/`、`.env`：发出去的包不会带着别人的数据或 Key。

### 一条命令（推荐）

```bash
python start.py
```

`start.py` 是唯一的一键入口，四步全包：**建 venv → 升 pip/setuptools → 查/装依赖 → 启动**。
依赖不齐时它不会硬着头皮启动（旧版那样只会换来一屏 `ModuleNotFoundError`），而是明确报错
并告诉你下一步。之后每次启动也只需要 `python start.py`（Windows 与 Linux/macOS 同一条命令；
`./start.sh` 只是转交给 `start.py` 的薄壳）。

常用开关：

| 命令 | 说明 |
|------|------|
| `python start.py` | 一键：缺什么补什么，然后启动 |
| `python start.py --check` | 只做只读检查（不联网、不装包、不建 venv、不启动），退出码 1 = 依赖不齐 |
| `python start.py --no-launch` | 环境备齐但不起进程 |
| `python start.py --recreate` | 删掉重建 `venv`（venv 坏掉时的自救入口） |
| `python start.py --dev` | 连带 `[dev]` 组（pytest / ruff） |
| `python start.py --pause` | 结束前等回车，方便双击运行时看输出 |

用手动方式也行，根目录 `requirements.sh` 是兜底入口
（等价于 `pip install -r requirements.txt`，其中 `requirements.txt` 只是指向 `pyproject.toml` 的指针）。

> Linux/macOS 首次使用若脚本报换行符错误，先跑 `dos2unix start.sh requirements.sh`。

## WebUI（管理后台）

内置一套前端：

| 入口 | 说明 |
|------|------|
| `/` | **Dashboard（Vue 3 + Vuetify 3）**，由 `app/webui/dashboard/dist` 提供；未构建时给出「请先 pnpm build」提示页 |

> 旧版手写 UI（`/legacy`）已在 P6 移除——同一张配置表单曾实现两遍，schema 每加一个
> 字段要改两处且没有测试守卫。

新版 Dashboard 源码在 [`app/webui/dashboard/`](app/webui/dashboard/)，风格对齐 AstrBot dashboard（浅/暗双主题、卡片化布局、实时日志控制台、模块配置 schema 表单等）。构建方法：

```bash
cd dashboard
pnpm install          # 首次
pnpm build            # 产物输出到 app/webui/dashboard/dist，浏览器刷新即可生效
```

> 未构建（或 `app/webui/dashboard/dist` 不存在）时，首页会显示「请先 pnpm build」的提示页（HTTP 503），不会回退到旧版 UI。
> `dist` 已纳入版本库，克隆后无需 Node 环境即可直接使用新版 UI。

### 手动启动

```bash
# 首次启动
venv\Scripts\activate.bat          # 激活虚拟环境
pip install -e ".[dev]"           # 安装依赖（依赖清单以 pyproject.toml 为准）
python main.py
```

> `requirements.txt` 只是指向 `pyproject.toml` 的安装指针（内容为 `.[dev]`），
> 依赖版本一律以 `pyproject.toml` 为准，不要在 `requirements.txt` 里重复维护依赖。

首次启动会：
1. **没有 `.env` 就生成一份完整的**（所有配置键 + 注释 + 随机令牌，可事后直接改）；
   已存在则只补空令牌，不覆盖你写的内容；
2. 初始化 `data/app.db`；
3. **自动从旧 JSON 文件迁移配置**（`webserver/webconfig.json`、`webui/webui_config.json`、
   `data/module/configs/*/config.json`、`data/module/configs/*/authority.json`）——首次运行后以 SQLite 为准；
4. 按配置自动连接 `auto_connect=true` 的账号，加载对应模块；
5. 启动 WebUI（默认 `http://127.0.0.1:9200`）与定时调度器。

### 日志

- **文件**：`logs/debug.log`（原始完整）、`warn.log`、`errors.log`、`user.log`（用户简洁日志）
- **轮转**：四个文件每 6 小时同步归档到 `logs/YYYY-MM-DD-HH/`，保留 48 份
- **导出**：设置页可打开日志导出弹窗，在固定滚动框中按时间段折叠选择日志，支持打包 ZIP 下载
- **WebUI**：日志面板默认显示简洁日志；开启“显示原始日志”后显示完整技术日志
- **控制台**：控制台输出与 WebUI 的“显示原始日志”开关同步；无论开关状态，`debug.log` 始终完整记录

### 关键配置（.env）

**首次启动会自动生成一份完整的 `.env`**（所有键 + 注释 + 随机令牌），想改哪项直接改，
不用先去对照 `.env.example` 抄键名。`.env` 已存在时程序**只补空令牌**，其余内容一字不动。

| 变量 | 默认 | 说明 |
|------|------|------|
| `WEBUI_HOST` | `127.0.0.1` | WebUI 监听地址（外网访问请配合反向代理） |
| `WEBUI_PORT` | `9200` | WebUI 端口 |
| `WEBUI_TOKEN` | 空（首次启动自动生成） | 登录 WebUI 的令牌；留空则自动生成并写回 `.env`，登录页粘贴该值 |
| `WEBUI_AUTO_OPEN` | `false` | 启动后用浏览器打开带 `?accesstoken=` 的一键登录链接（本地 start 脚本默认开启，`--no-open` 关闭） |
| `DB_PATH` | `data/app.db` | SQLite 路径 |
| `LOG_DIR` | `logs` | 日志目录（日志面板与 `/log` 指令都读它） |
| `WS_CONNECT_TIMEOUT` | `30` | WebSocket 连接超时（秒） |
| `WS_PING_INTERVAL` / `WS_PING_TIMEOUT` | `30` / `10` | WebSocket 心跳间隔 / 超时（秒） |
| `MODULE_DIR` | `data/module` | 插件运行时数据根目录（configs / data / plugins(zip) / uninstalled_modules.json）；老布局仓库根 `module/` 启动时自动搬过来 |
| `PLUGINS_DIR` | `app/plugins` | 业务插件**代码**目录（可指向别处换一套插件集） |

> 变量名 = `app/core/settings.py` 的字段名大写，**环境变量优先于 `.env`**。键清单的唯一来源是
> `app/core/env_template.py`（加字段不登记会被 `tests/core/test_env_template.py` 拦下）。
> 注意 `QQBOT_LLM_DATA_DIR` 这类在 `os.environ` 里直接读的开关**不属于 `.env`**，只能作为
> shell 环境变量使用。

### 账号连接方式（正向 / 反向 WebSocket）

每个账号在「账号管理」里单独选连接方式，两种都是 OneBot v11 的 WebSocket：

| 方式 | 谁发起连接 | 地址填什么 |
|---|---|---|
| **正向 WebSocket** | 框架连出去 | OneBot 实现（NapCat / Lagrange / LLOneBot）暴露的 ws 地址，如 `ws://127.0.0.1:3001/` |
| **反向 WebSocket** | 框架本机监听，OneBot 实现连进来 | 本机监听地址，如 `ws://0.0.0.0:3001/onebot/v11/ws`；把这个地址原样填进 NapCat 的「反向 WebSocket」 |

- 两种方式共用同一套能力：收发消息、API 调用、登录信息、断线重连、按账号隔离；
- `access_token` 两条路都支持：正向放在 URL 的 `?access_token=`，反向由框架校验对端
  `Authorization: Bearer` 头或 URL 参数，不匹配直接拒绝（关闭码 4401）；
- 反向监听只在本机进程内占用指定端口，端口需自行保证未被占用；对端重启时会话会自动
  重新接入，不需要手动点连接。

#### NapCat 反向 WebSocket（三步）

1. 启动 NapCat 并登录 QQ（一键包 / 管理器均可）；
2. 在 NapCat 里新建 **反向 WebSocket**，地址填本程序的监听地址（默认端口 3001）：
   `ws://127.0.0.1:3001/onebot/v11/ws`，Token 与账号里填的一致（或都留空）；
3. 回到本程序「账号管理」新增账号（连接方式选**反向 WebSocket**），点「连接」后
   NapCat 会自动接入——启动日志与 `/api/readiness` 都会显示「反向 WebSocket 正在监听」。

> 更省事的方式：访问 `/setup` 首启向导，第 1 步会创建账号并给出可直接复制的监听地址，
> 连不上时逐步提示（对端未接入 / Token 不匹配 / 端口占用）。

### 更新（可回滚）

```bash
python scripts/update.py status                  # 当前版本 / 待应用 / 可回滚
python scripts/update.py stage --kit <kit.zip>   # 校验并暂存（不动线上）
python scripts/update.py apply                   # 备份 data 与 .env 后切换
python scripts/update.py rollback                # 退回上一版（含数据库备份）
```

### 故障排查

先看**总览页「运行状态」卡**或访问 `GET /api/readiness`：它会把"差什么、去哪修"逐条列出。

**连上了但机器人不回话**时，用**诊断台**（总览「账号状态」每一行的听诊器图标，或「账号管理」卡片右上角）：
里面按账号显示最近收到的 OneBot 原始事件（消息/通知/请求），还能直接「发一条测试消息」。
如果诊断台里一直没有任何事件，问题在协议端（NapCat/LLOneBot）一侧；有事件但机器人不回，才是回复配置/模型的问题。

| 现象 | 先查 | 入口 |
|---|---|---|
| 群里 @ 机器人没反应 | 群聊回复是否开启；该群是否被单独关闭/静音 | 总览就绪卡「开启回复」→「AI 对话面板」；群内发 `#llm status` |
| 私聊不回 | 私聊回复开关 | 「AI 对话面板」→ 回复开关 → 私聊回复 |
| 有回复但内容空/报错 | 模型池是否为空、Key 是否有效 | 首启向导第 2 步，或 `/api/readiness` 的「还没选模型」 |
| 账号一直连不上 | 反向：NapCat 是否填了同一地址/Token；正向：地址与端口是否正确 | 「账号管理」卡片里的「协议端怎么填」；诊断台看事件是否进来 |
| 诊断台没有任何事件 | 协议端是否真的连到本程序、方向是否选反 | 诊断台顶部状态；「账号管理」里应显示「等待协议端接入 / 已连接」 |
| 启动直接退出 | 看退出码：1=venv、2=依赖、3=缺 main.py、4=Python 版本不符 | 控制台输出已写明原因与修复命令 |
| 改了配置但不生效 | 是否改在了"账号级"而群级有覆盖 | 「回复与权限」里看每项的来源（默认/账号/群） |

更多运维细节（日志轮转、数据备份、端口占用）见 [`MAINTENANCE.md`](MAINTENANCE.md)。

## 内置功能插件

流式输出、长期记忆、主动消息、定时任务、OneBot/MCP 工具、指代消解、组装档位等
**全部由模块提供**，随仓库发布、随账号自动装配，各自持有自己的配置页。

分三层：**基础功能**（收发消息、会话、toolcall、单次 LLM 请求、定时服务）留在框架；
**高级接口**（`assembly` / `provider_invoker` / `output` / `agent`）是框架开的"可接管"槽位；
**业务**（记忆、主动消息、定时任务、工具、元信息、指代）由模块用基础接口自己实现，
**不声明接管** —— 框架不知道它们存在。

| 模块 | 键数 | 负责 |
|---|---|---|
| `llm_output` | 25 | 输出策略：`text` / `stream` / `speech`（发言通道） |
| `llm_assembly` | 10 | 组装档位、行为规则段、请求落盘对账 |
| `llm_message_meta` | 14 | 消息元信息渲染（时间/发送者/引用/@）与历史清洗 |
| `llm_context` | 10 | 指代消解、上下文按需展开、历史查询权限 |
| `llm_memory` | 17 | 长期记忆召回策略 |
| `llm_knowledge` | 3 | 知识库检索 |
| `llm_tools_extra` | 9 | OneBot 工具、MCP、工具结果回应要求 |
| `llm_proactive` | 17 | 主动消息（私聊随机间隔 / 群聊沉默后开口） |
| `llm_schedule` | 3 | 定时任务 |

**本体配置共 57 键，分在两处工作台 + 3 个设置页，每个字段只有一个编辑入口**：

- **AI 对话面板**（`/ai`）：回复总开关、群聊/私聊开关、触发方式、模型池、人格；
- **回复与权限**（`/reply`）：逐群/私聊范围、响应权限、拥有者/管理者名册、高危工具审批；
- **AI 设置**（`/ai/settings/*`）：对话行为、会话管理、高级功能（图片与联网等）。

其余能力都在各自模块的配置页里。

> **从 v2 升级**：务必跑两个迁移脚本（都支持 `--dry-run`，自动备份 DB）：
>
> ```bash
> python scripts/migrate_v3_owner_to_roster.py   # 账号级 owner QQ → 权限管理的拥有者名册
> python scripts/migrate_v3_split_config.py      # agent 配置 108 键 → 9 个模块行
> ```
>
> 前端清单 `sections.json`（AI 设置 3 页）与 `ai-panel.json`（面板 / 回复与权限字段）都由
> `python scripts/gen_sections.py` 从 `app/agent/config_partition.py` 生成，不要手改。

## 开发一个插件

> 完整模块开发文档见 [`docs/MODULE_DEV.md`](docs/MODULE_DEV.md)，包含每个钩子的参数与用法。

在 `app/plugins/<name>/` 下新建文件（模块内部一律用**相对导入**，与目录位置解耦）：

```python
# module.py —— 声明
from app.plugin import BaseModule
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
自动接管并禁用对应框架能力，卸载/禁用时自动恢复。内置能力与完整用法见
[`docs/FEATURE_REGISTRY.md`](docs/FEATURE_REGISTRY.md)。

### 装饰器风格（推荐）

新架构支持用装饰器直观注册“模块流水线钩子”和“LLM 流水线钩子”：

```python
# module.py
from app.plugin import BaseModule, module_hook, llm_hook, send_hook

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
from app.agent import tool, skill

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

### 指令交互（`#llm`）

在会话里直接发 `#llm <动作>` 即可管理机器人的会话、定时任务、主动消息与长期记忆，
**不需要 @ 机器人**（群聊需开启「群聊回复」，私聊需开启「私聊回复」）。发 `#llm help`
（或单独发 `#llm`）会以**合并转发**形式返回完整指令表，每个分组一个节点：

```
#llm help                                  # 指令表（合并转发）
#llm task / list / switch <id> / new / load <id> / export / exit / stop
#llm schedule / schedule cancel <id>       # 定时任务
#llm proactive [session_id]                # 主动消息
#llm memory list | search | correct | deny | confirm | forget | clear | reset | audit
```

- 指令回复统一以「# 」开头，便于与聊天内容区分；`#llm stop`、`#llm memory audit` 仅 Bot 拥有者可用；
- `#llm help` 是**静态指令表**，不依赖模型：API 密钥没配好时也能查看；
- 指令总表定义在 `app/agent/commands.py`（`COMMAND_GROUPS`），新增指令时表与
  `chat.handle_commands` 两处必须同步——`tests/test_llm_help.py` 会逐条校验，防止帮助与实际行为漂移；
- 合并转发不被实现支持时自动降级为分段纯文本。

### 上下文补全（聊天环境自己取）

聊天记录里常常只剩"骨架"（`@123`、`[引用]`），框架分三层把"血肉"补齐，让模型不必猜：

1. **预展开**：渲染层把 `@123` 反查成 `@三哥(123)`（`app/agent/context/nicknames.py` 共享缓存，
   与触发消息的 @ 解析共用，同一个人只查一次）；
2. **缺口标记 + 摘要**：实在取不到才标 `【未展开:用户123】`，并在背景块 / `get_chat_history`
   头部给出 `【本段含 2 处未展开内容：…；可调用 expand_message 展开后再回答】`；
3. **按需展开**：三个系统工具按意图分区——`expand_recent`（按位置："上一条/刚才那条"，
   不需要 id）、`expand_message`（按 id：被引用消息 / 合并转发）、`expand_user`（按 QQ：是谁）。
   取回过的内容登记为「已展开」，后续不再重复取。

只有**能被解决**的缺口才会被标记（图片/语音无法转文字就不标记）。

### 指代消解（知道用户说的是哪一条）

「那你能做到消息里的那个样子吗」这类**纯回指**句没有任何 id，靠模型自省会挑错对象。
框架维护一张**会话焦点表**（谁刚被讨论过、摘要是什么），每轮请求注入「当前对话焦点」，
并在判定出明确指向时**确定性预取**（不依赖模型决定要不要调工具，也就不再静默十几秒）：

| 场景 | 处理 |
|---|---|
| 回复某条 / @ 某人 | 直接定目标，框架预取 |
| "上一条 / 刚才那条 / 刚才的消息" | `expand_recent` 按位置取回 |
| 纯回指（"那个样子/那条"） | 焦点优先 + 位置邻近；候选不明按 `referent_ambiguous_policy` **都取回** |
| 已经取过的内容 | 渲染为 `【已展开:… → 摘要】`，工具命中登记**零 API 调用** |

相关开关与设计说明见 [`docs/context-expand-design.md`](docs/context-expand-design.md)、
[`docs/referent-resolution-design.md`](docs/referent-resolution-design.md)：
`fetch_at_nickname`、`context_expand_enable`、`referent_*`、`max_tool_rounds`、
`tool_result_directive*`、`proactive_env_prompt_enable` 等。

> 主动消息与定时任务同样具备这套能力（此前这两条路径完全不传 tools）。

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

### 图片与动图理解（视觉模型）

本轮消息里的图片会随请求直接传给声明了 `image` 模态的模型；文本模型照旧只看到
`[图片]` 占位，语义不变。

**动图与表情包会被拆帧**：GIF / 动态 WebP / QQ 商城表情（`mface`）先解码成若干张
静态帧再一起送出，并附一句「这几张是同一张动图的连续帧」——多数网关对 `image/gif`
只读第一帧，甚至直接 400，框架自己拆帧才保证「动图看得到」。

| 配置项（Agent → 工具与联网） | 默认 | 说明 |
|---|---|---|
| `image_understanding_enable` | 开 | 图片是否随请求传给模型（仅视觉模型生效） |
| `image_max_count` / `image_max_mb` | 4 / 10 | 单轮静态图张数、单张体积上限 |
| `gif_understanding_enable` | 开 | 动图是否拆帧 |
| `gif_max_frames` | 6 | 单条消息所有动图**共享**的帧数预算（等间隔采样，必含首尾帧） |
| `gif_frame_max_edge` | 512 | 每帧长边上限（像素），调小可省 token |
| `gif_probe_all_images` | 关 | OneBot 实现不给动图线索时，让每张图都探测一次 `n_frames` |

实现分两层：`app/agent/media/image.py` 负责「段 → 图片/帧条目」（含动图线索判断、取字节、
体积上限），`app/agent/media/animated.py` 是纯计算的拆帧逻辑（Pillow 解码 → 等间隔取帧 →
白底合成 → JPEG）。历史消息里的动图同样能按需取回（`expand_image` 走同一条通路）。

### 模型 Provider

框架内置三类原生适配器：

| Provider | 说明 |
|---|---|
| `openai` | OpenAI 兼容协议（DeepSeek / Ollama / OpenRouter 等） |
| `anthropic` | Anthropic Claude Messages API |
| `gemini` | Google Gemini Generative Language API |

第三方插件还可以用 `register_provider(name, cls, aliases=...)` 运行期注册新适配器。
> 它只替换**HTTP 适配器**（框架既定调用约定之内）；要连调用约定一起换
> （回退链/重试/工具循环放在哪一层），用 `supersedes = ("provider_invoker",)`，
> 见下文「[接管消息组装](#接管消息组装迁移--复刻第三方组装格式)」。

### LLM 可观测性

每次 LLM 请求会记录延迟 / token / provider / model / 工具调用 / LLM 钩子耗时，可以通过 WebUI 读取：

```http
GET /agent/telemetry?bot_id=<qq>&limit=30
POST /agent/telemetry/reset?bot_id=<qq>
```

日志里**一次请求只落一行 token 用量**（含工具多轮与重试的累计），在整轮请求完全结束后打印：

```
[Api] 本次请求消耗: 输入 1234（缓存命中 1000 / 未命中 234） / 输出 567 / 合计 1801 tokens | 回复 27 字符 | model=deepseek-chat | 流式
```

- 命中/未命中**直接取上游字段**（DeepSeek `prompt_cache_hit_tokens`/`prompt_cache_miss_tokens`、
  OpenAI `prompt_tokens_details.cached_tokens`、Anthropic `cache_read_input_tokens`），不自己估算；
- 流式请求会自动带 `stream_options.include_usage` 让上游回报 usage；个别上游不认这个参数时会
  自动去掉重发一次（该次拿不到 token 数，但不影响回复）；
- 上游一个字都没报 usage 时**不打印**这一行（宁缺毋编）；
- 可用 `extra_body.stream_options` 覆盖默认行为。

### 接管消息组装（迁移 / 复刻第三方组装格式）

从别的框架（例如 AstrBot）迁移过来时，往往要 1:1 复刻它「发给模型的东西」。
框架为此开了三个口子，**都在单账号粒度上生效**：

| 口子 | 用途 | 插件侧写法 |
|---|---|---|
| `@llm_hook("post_assemble")` | 事后微调本轮请求 | `ctx.messages` 可增删/重排/整体替换；置空 = 本轮不请求 LLM |
| `supersedes = ("assembly",)` | **整体替换组装** | 实现 `build(req) -> list[dict]`（`req` 含历史/背景/记忆/技能/图片等全部归一化输入） |
| `supersedes = ("provider_invoker",)` | **整体替换 provider 调用约定** | 实现 `chat()` / `stream()`（第三方把工具循环放在 runner 层时用） |

四条组装路径（普通回复非流式/流式、主动消息、定时任务）统一收口，插件不必按路径分别打补丁。
`post_assemble` 与两个「装槽」能力都由 `FeatureRegistry` 管租约：禁用/卸载自动还原，
多个接管者先到先得 + 交棒。详见 [`docs/MODULE_DEV.md` §4.7](docs/MODULE_DEV.md)。

配套的迁移对账开关（AI 设置 →「会话管理」，**默认都不改变现有行为**）：

| 配置键 | 默认 | 用途 |
|---|---|---|
| `system_prompt_style` | `blocks` | `single` = 把所有 system 块折成一整条（对齐第三方「一整条 system prompt」形态） |
| `prompt_dump_enable` | 关 | 每轮**最终**请求 messages 落盘 `data/llm/<账号>/prompt_dump.jsonl`，供逐条 diff |
| `context_token_budget` | `0` | >0 启用 token 预算裁剪：从最旧历史丢起，system 与本轮提问永不丢 |

落盘内容可直接查：

```http
GET    /agent/prompt-dumps?bot_id=<qq>&session_id=group_xxx&limit=10
DELETE /agent/prompt-dumps?bot_id=<qq>
```

历史侧配套：`SessionManager.history_snapshot()` 给**未投影的原始条目**（自带渲染器组装用），
`import_history()` 把外部历史批量搬进来让框架既有组装器也能读到。

### AI 对话页面

配置不再是一张 400 项的大表单，而是「面板 / 回复与权限 / 设置」三层：

| 页面 | 内容 |
|---|---|
| AI 对话面板 | 状态摘要、回复总开关、群/私聊开关、触发方式、模型池、人格、诊断入口 |
| 回复与权限 | 逐群/私聊范围与静音、响应权限档位、拥有者/管理者名册、高危工具审批、高级规则 |
| AI 设置 → 对话行为 | 消息合并等待、回复打断 |
| AI 设置 → 会话管理 | 会话窗口、群聊历史、上下文压缩 |
| AI 设置 → 高级功能 | 图片/动图理解、联网搜索、工具执行、模型缓存 |

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

### 插件 API（`from app.plugin import ...`）

| 函数 | 用途 |
|---|---|
| `get_modules()` | 已加载模块对象列表 |
| `get_features(bot_id=None)` | 全局能力注册表状态（含接管者） |
| `get_config_path(name)` | 模块配置目录 `data/module/configs/<name>/` |
| `get_data_path(name)` | 模块数据目录 `data/module/data/<name>/`（自动创建，供持久化自定义数据） |

配置/权限迁移源文件：`data/module/configs/<name>/config.json` + `authority.json`（首次启动迁移进 SQLite，之后以 SQLite 为准）。

## 事件类型（subscribe 可用值）

| 类型 | 说明 |
|------|------|
| `message_group` / `message_private` | 群/私聊消息 |
| `notice_poke` | 戳一戳 |
| `notice_group_emoji` | 群表情回应 |
| `notice_group_recall` / `notice_private_recall` | 撤回 |
| `notice_group_increase` / `notice_group_decrease` | 入群/退群 |
| `request_group` / `request_private` | 加群/加好友申请 |

> **定时不要用事件**：`time_core` 轮询广播已废弃（见 `app/application/cron_service.py`）。
> 需要"到点做事"请用模块的 `SCHEDULES` 声明或运行时注册，
> 见 [`docs/MODULE_DEV.md` §4.9.6](docs/MODULE_DEV.md)。

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
