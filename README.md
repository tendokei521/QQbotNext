<div align="center">

# QQBot Next 1.0

**轻量化、易配置、功能全面的类 AstrBot QQ 机器人框架**

基于 OneBot v11 · 多账号 · 多模型 · 单进程 · 自带管理后台 · 插件化

[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![OneBot](https://img.shields.io/badge/OneBot-v11-4B8BBE)](https://github.com/botuniverse/onebot-11)
[![Version](https://img.shields.io/badge/version-1.0.0-blue)](CHANGELOG.md)
[![License](https://img.shields.io/github/license/tendokei521/QQbotNext)](LICENSE)

[它能做什么](#它能做什么) · [快速开始](#快速开始) · [接入 QQ](#接入-qq) · [管理后台](#管理后台) · [技术架构](#技术架构) · [文档](#文档)

</div>

---

> [!WARNING]
> **项目状态：快速迭代中。** 框架仍处于快速迭代期间，配置项、插件接口与默认行为都可能变化；
> 项目的**稳定性**及**插件社区**均不及老牌 bot 框架。
>
> 如果你希望的是：**稳定性高、插件多、生产环境使用**，那么 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 是更好的选择。
> 本项目更适合：想轻量部署、愿意读源码、需要按自己习惯改行为和配置的场景。完整对比见[选型建议](#选型建议与-astrbot-的对比)。

## 目录

- [项目简介](#项目简介)
- [它能做什么](#它能做什么)
- [快速开始](#快速开始)
- [接入 QQ](#接入-qq)
- [管理后台](#管理后台)
- [功能插件](#功能插件)
- [配置与数据](#配置与数据)
- [更新与回滚](#更新与回滚)
- [排障](#排障)
- [技术架构](#技术架构)
- [开发者指南](#开发者指南)
- [文档](#文档)
- [选型建议：与 AstrBot 的对比](#选型建议与-astrbot-的对比)
- [贡献与致谢](#贡献与致谢)
- [许可与免责声明](#许可与免责声明)

---

## 项目简介

QQBot Next 是一个运行在**单个 Python 进程**里的 QQ 机器人框架：同时管理若干账号的 OneBot 连接、
每个账号一套 LLM 运行时（模型、人格、记忆、插件、工具）、一个定时调度器和一个浏览器管理后台。

它的定位与 AstrBot 类似——用大模型驱动 QQ 对话，把"配置"和"扩展"做成可视化的东西；
区别在于本项目刻意做得更小：不依赖 Docker、消息队列或独立前端服务，配置与运行时数据存 SQLite，
`python start.py` 一条命令完成建环境、装依赖、启动、打开后台。

**设计目标**

- **轻量**：单进程、单数据库、前端产物随仓库发布，克隆下来就能跑；
- **易配置**：首次向导（连接 → 模型 → 人格 → 完成）+ 全套 WebUI 表单，改完即时生效；
- **功能全面**：多账号、多模型、人格、记忆、上下文、主动性、工具、插件、统计、更新回滚；
- **可改造**：分层架构 + 领域规则与 IO 分离 + 契约测试，插件与框架解耦，改动有清晰边界。

## 它能做什么

### 对话与人格

- **多账号**：一个进程管理多个 QQ 号，每个号独立模型池、人格、插件、权限、回复开关，可单独启停。
- **多模型与回退**：支持 OpenAI 兼容（DeepSeek / OpenRouter / Moonshot / 智谱 / Ollama / LM Studio /
  SiliconFlow 等）、OpenAI Responses、Anthropic、Gemini；同一账号可配置多个模型，主模型失败自动切备用。
- **人格预设**：人格是可保存、可切换的预设（"我是谁"），与模型、聊天行为解耦。
- **两套提示词方案**：聊天（日常群聊）与角色扮演（角色一致性、演绎表达、OOC 边界）。
- **触发方式**：仅 @ 我 / 仅关键词 / @ 或关键词 / 所有消息 / 都不触发，可按账号与群分别设置。
- **参与度模式**：除"条件触发"外，也可按参与度（极低 / 低 / 中 / 高）让模型自己判断何时接话；
  同群已有一轮在飞时不再并发请求，避免两个回复互相打断。
- **回复体验**：输出方式（普通请求 / 流式）与发言方式（整段一条 / 收到就发 / 按换行拆分按节奏）分开配置，
  消息去抖与合并、回复打断。
- **看图**：图片理解与动图（逐帧）理解，需要模型支持视觉。
- **联网**：可选的 Tavily 联网搜索工具（填自己的 API Key）。

### 记忆与上下文

- **会话上下文**：会话窗口、历史轮数、上下文压缩、群聊历史、引用回复与"刚才那条"指代，
  按需展开合并转发等极端情况。
- **长期记忆**：常驻用户画像 + 混合召回 + 后台蒸馏，记住偏好与事实；可整体关闭。
- **上下文增强**："这条""那个"指的是什么、需要时再拉群记录，由插件按需注入，不无脑堆提示词。

### 主动性与工具

- **主动消息**：私聊随机间隔搭话、群聊沉默一段时间后开口，支持免打扰时段；可关闭。
- **定时任务**：一次性 / 每天 / 每周 / 每月 / 固定间隔的提醒与自动任务。
- **OneBot 工具集**：随包内置 OneBot v11 工具清单（当前 175 项接口），按"读 / 发送 / 管理"分级，
  含发消息、贴表情、戳一戳、群文件、群相册、合并转发、历史消息等。
- **系统工具**：会话信息、Tavily 搜索、大结果取回（`recall_tool_result`）等。
- **插件工具**：插件注册的工具与提示词段随插件启停自动装配。
- **权限与审批**：群 / 私聊响应范围、拥有者与管理者名册、高危工具审批（可配置为需要确认）。

### 管理与运维

- **管理后台**：浏览器里完成账号、模型、人格、插件、权限、统计、日志的配置与查看；
  只有监听地址、端口、令牌等进程级开关在 `.env`。
- **首次配置向导**：连接账号 → 配置模型 → 人格设定 → 完成，全程在网页里点选完成。
- **就绪检查与诊断台**：缺什么、去哪修逐条列出；连上了不回话时可看账号最近收到的协议端事件。
- **配置包**：导出一个账号的可迁移配置（人格 / 模型池 / Agent 配置 / 插件参数与启停），
  应用到目标账号时事务写入 + 快照，失败自动回滚。
- **统计与日志**：token / 请求 / 收发消息统计，支持筛选与导出；分级日志（debug / warn / errors / user）
  与实时日志面板。
- **更新与回滚**：通过命令行完成暂存、切换、回滚，切换前备份 `data/app.db` 与 `.env`。

## 快速开始

### 前置条件

| 依赖 | 要求 |
|---|---|
| **Python** | 3.10 及以上（CI 覆盖 3.10 / 3.12），安装时勾选「Add Python to PATH」 |
| **协议端** | 任意 OneBot v11 协议端（NapCat / Lagrange / LLOneBot 等）。本程序通过 OneBot v11 WebSocket 收发消息，**不自带 QQ 客户端，也不处理 QQ 登录** |
| **网络** | 首次启动需访问 PyPI（或已配镜像）；完全离线时需使用带 wheelhouse 的发行包（构建时加 `--with-wheelhouse`） |

> 环境自检：首次运行请用 `python start.py`（会创建 `venv`）；环境建好后，`python start.py --check`
> 只做只读复查（不联网、不装包、不建 venv、不启动）。`--check` 要求已有可用的 `venv`：
> 退出码 `0` = 通过、`1` = venv 不可用、`2` = 依赖不齐、`4` = Python 版本不符。

### 安装与启动

**方式一：发行包（无需 git，也不必手动装依赖）**

1. 从 [GitHub Releases](https://github.com/tendokei521/QQbotNext/releases) 下载 `QQBotNext-<版本>-kit.zip`
   并解压（若当前尚未发布发行包，可直接走方式二）；
2. 运行 `python start.py`（Windows 也可双击 `start.bat`；macOS / Linux 用 `./start.sh`）；
3. 首次启动自动建环境、装依赖、打开浏览器进入「首次配置」向导。

发行包带 `deploy.json`（逐文件 sha256，可自查完整性）与 `README-FIRST.txt`（三步上手），
**不含** `venv/`、`data/`、`logs/`、`.env`——不会带着别人的数据或 Key。

**方式二：源码**

```bash
python start.py
```

`start.py` 是一键入口，四步全包：建 venv → 升 pip → 查 / 装依赖 → 启动。
依赖不齐时它会直接说明缺什么、下一步怎么做，而不是在启动时抛出一屏 traceback。之后每次启动也只是 `python start.py`。

| 命令 | 说明 |
|---|---|
| `python start.py` | 一键：缺什么补什么，然后启动 |
| `python start.py --check` | 只做只读检查（不联网、不装包、不建 venv、不启动） |
| `python start.py --no-launch` | 备好环境但不起进程 |
| `python start.py --recreate` | 删掉并重建 `venv`（环境坏掉时的自救入口） |
| `python start.py --dev` | 连带安装开发依赖（pytest / ruff 等） |
| `python start.py --vector` | 连带安装可选 `[vector]` 组（`sqlite-vec`） |
| `python start.py --pause` | 结束前等回车，方便双击运行时看输出 |

手动方式：

```bash
python -m venv venv
# Windows: venv\Scripts\activate
# macOS / Linux: source venv/bin/activate
pip install -e ".[dev]"
python main.py
```

依赖版本一律以 `pyproject.toml` 为准。

### 首次启动会发生什么

1. 没有 `.env` 就生成一份完整的（全部配置键 + 注释 + 随机令牌）；已存在时只补空令牌，不覆盖你的内容；
2. 初始化 `data/app.db`（SQLite），建表并执行幂等迁移；
3. 旧版 JSON 配置（`webserver/webconfig.json`、`webui/webui_config.json`、
   `data/module/configs/*/config.json`）会自动迁入数据库，之后以数据库为准；
4. 自动连接 `auto_connect=true` 的账号，装配对应插件；
5. 启动管理后台（默认 `http://127.0.0.1:9200`）与定时调度器。

浏览器打开后台，按向导完成四步：**连接账号 → 配置模型 → 人格设定 → 完成**。

### 默认行为

- **私聊回复默认开启，群聊回复默认关闭**：群里需要 @ 或命中关键词才会理，可在「AI 对话面板」
  与「回复与权限」里按账号 / 按群调整；
- 管理后台默认只监听 `127.0.0.1`，`WEBUI_TOKEN` 首次启动随机生成并打印在控制台。

## 接入 QQ

每个账号可单独选择连接方式，两种都是 OneBot v11 WebSocket：

| 方式 | 谁发起连接 | 地址填什么 |
|---|---|---|
| **正向 WebSocket** | 本程序连出去 | 协议端（NapCat / Lagrange / LLOneBot）暴露的 ws 地址，如 `ws://127.0.0.1:3001/` |
| **反向 WebSocket** | 本程序监听，协议端连进来 | 本程序的监听地址。新增账号时自动带出默认值（如 `ws://127.0.0.1:3001/onebot/v11/ws`，端口被占用会顺延）；`/setup` 向导的默认值为 `ws://127.0.0.1:4001/` |

以 NapCat 反向接入为例：

1. 启动 NapCat 并登录 QQ；
2. 在 NapCat 新建**反向 WebSocket**（NapCat 里叫「WebSocket 客户端」），地址填本程序「账号管理」里显示的监听地址，
   Token 与账号里填的一致（或都留空）；
3. 回到本程序「账号管理」新增账号（连接方式选**反向 WebSocket**），点「连接」，NapCat 会自动接入。

> 更省事：访问 `/setup` 向导，第 1 步会创建（或修改）默认账号并给出可直接复制的监听地址；
> 连不上时先看账号卡片的状态与错误提示（反向模式会显示「等待协议端接入」），并按「协议端怎么填」核对。
> `access_token` 两种方式都支持。

## 管理后台

管理后台是 Vue 3 + Vuetify 3 的静态前端，由 FastAPI 托管；`dist/` 随仓库发布，克隆即用。

| 页面 | 干什么 |
|---|---|
| **总览** | 运行状态、就绪检查（差什么、去哪修）、账号状态与诊断台 |
| **首次配置** | 四步向导：连接账号 → 配置模型 → 人格设定 → 完成 |
| **账号管理** | 增删账号、连接方式（添加时选择）、启停、协议端怎么填 |
| **AI 对话面板** | 回复总开关、触发方式、模型池、人格、参与度、谁能回复的只读摘要 |
| **AI 设置** | 对话行为（去抖 / 打断 / 审批）、会话管理（窗口 / 群历史 / 压缩）、高级功能（图片动图 / 联网 / 工具 / 输出与发言） |
| **回复与权限** | 群与私聊范围、响应权限、拥有者与管理者名册 |
| **功能插件** | 装 / 卸 / 启停插件，进入各插件自己的配置页 |
| **插件响应策略** | 多个插件 / 多个账号同时想回复时，仲裁出唯一赢家 |
| **工具管理** | 工具总览、系统工具、OneBot 工具、插件工具的清单、启停与权限覆盖 |
| **模型服务** | 连接预设（地址 + Key）+ 模型清单；拉取模型、测连接、标注能力（文本 / 图像 / 工具调用等） |
| **人格设定** | 人格预设的增删改；当前账号用了哪个人格、切过去 |
| **配置包** | 导出一个账号的可迁移配置，导入并应用到目标账号（失败自动回滚） |
| **会话数据** | 历史对话、思考内容展开、按账号实时刷新 |
| **数据统计** | token / 请求 / 收发消息统计，支持维度、时间范围、粒度筛选与导出 |
| **日志** | 实时日志（WebSocket）与分级日志查看、导出 |
| **高级选项** | 实验性开关：消息组装方式与工具总开关 |
| **设置** | 配置向导入口、系统信息、备份与恢复（配置包 / 聊天记录 / 日志） |

## 功能插件

框架本体负责底层机制（连接、会话、提示词装配、一次 LLM 请求、工具循环、定时服务）；
面向具体场景的能力以插件形态存在，随仓库内置 4 个，按账号开关：

| 插件 | 负责 |
|---|---|
| **触发与调度**（`llm_trigger`） | 谁在什么时候开口：主动消息（私聊随机间隔 / 群聊沉默后开口，免打扰时段）+ 定时任务（一次性 / 每天 / 每周 / 每月 / 固定间隔） |
| **上下文增强**（`llm_context`） | "刚才""那条"指什么；需要时按需展开上下文与群记录 |
| **长期记忆**（`llm_memory`） | 记住用户的偏好与事实：常驻画像 + 混合召回 + 后台蒸馏；可关 |
| **LLM 调试**（`llm_enhance`） | 把本轮发给模型的完整内容（按层）与模型返回写进日志，排障用 |

- 内置插件在 `app/plugins/<name>/`，本身就是最好的范本；
- 第三方插件放在 `data/module/plugins/<name>/`（不进仓库，升级不丢），在「功能插件」里上传 zip 安装；
- 两种位置目录形态一致，插件内部用相对导入。写插件见 [docs/MODULE_DEV.md](docs/MODULE_DEV.md)。

## 配置与数据

### 配置在哪

- **真源**：`data/app.db`（SQLite）。账号、模型预设、人格、插件配置、回复策略、统计都在这里；
- **`.env`**：只管进程级开关（监听地址、端口、令牌、日志目录、DB 路径等），变量名 = 字段名大写；
- **环境变量优先级高于 `.env`**；完整键说明见 [MAINTENANCE.md](MAINTENANCE.md)。

```dotenv
WEBUI_HOST=127.0.0.1      # 后台监听地址
WEBUI_PORT=9200           # 后台端口
WEBUI_TOKEN=              # 访问令牌；留空则首次启动自动生成
DB_PATH=data/app.db       # SQLite 路径
LOG_DIR=logs              # 日志目录
MODULE_DIR=data/module    # 插件数据 / 第三方插件目录
PLUGINS_DIR=app/plugins   # 内置插件代码目录
```

### 目录结构

```text
main.py / start.py                      程序入口与一键启动
app/
  core/            配置、日志、事件总线、任务、容器
  domain/          纯领域规则（消息 / 会话 / 触发 / 权限 / 节奏 / 切分），无 IO
  infrastructure/  唯一 IO 出口（SQLite、OneBot 编解码与连接、配置存储）
  plugin/          插件框架：发现 / 加载 / 生命周期 / 钩子 / 权限 / 仲裁
  agent/           LLM 内核：装配 / 循环 / 工具 / 投递 / 供应商适配
  plugins/         内置插件（业务能力，全部以插件形态存在）
  application/     用例服务（账号、模型、人格、会话、统计等）
  webui/           FastAPI + Vue 3 Dashboard（dist 已进仓库）
data/              运行时：app.db、module/、llm/、media/（不进仓库）
logs/              分级日志与归档
docs/              开发文档（架构、插件、配置、测试策略……）
scripts/           迁移、更新、打包、前端截图等工具
tests/             契约测试与架构闸门
```

### 安全建议

- 后台默认只监听本机。需要远程访问时，请走**反向代理 + HTTPS**，并设置强 `WEBUI_TOKEN`；
- `.env`、`data/`、`logs/` 都在 `.gitignore` 里，不要提交或分享；
- 高危工具（管理群、改资料、执行命令等）建议在「AI 设置 → 对话行为 → 工具审批」选「人工审批」
  （默认是自动审批：消息凭证鉴权通过即执行）；
- 协议端与机器人都可能触发平台风控，请自行评估账号风险。

## 更新与回滚

```bash
python scripts/update.py status                  # 当前版本 / 待应用 / 可回滚
python scripts/update.py stage --kit QQBotNext-<版本>-kit.zip   # 校验并暂存（不动线上）
python scripts/update.py apply                   # 备份 data 与 .env 后切换（重启后生效）
python scripts/update.py rollback                # 退回上一版（含数据库备份）
```

## 排障

先看**总览页的「运行状态」卡**，或访问 `GET /api/readiness`：它会把"差什么、去哪修"逐条列出。

**连上了但不回话**时用**诊断台**（总览「账号状态」每行的听诊器图标）：

- 诊断台里一直没有任何事件 → 问题在协议端（掉线 / 地址填反 / Token 不对）；
- 有事件但不回 → 回复开关、权限、模型配置的问题。

| 现象 | 先查 |
|---|---|
| 群里 @ 没反应 | 群聊回复是否开启、该群是否被单独关闭；「回复与权限」 |
| 私聊不回 | 私聊回复开关；「AI 对话面板」 |
| 有回复但内容空 / 报错 | 模型池是否为空、Key 是否有效、模型是否支持工具调用 |
| 账号连不上 | 反向：协议端是否填了同一地址 / Token；正向：地址与端口 |
| 启动直接退出 | 退出码 1 = venv、2 = 依赖、3 = 缺 main.py、4 = Python 版本不符（控制台已写明修复命令） |
| 改了配置不生效 | 是否账号级被群级覆盖（「回复与权限」里每项都标来源）；插件是否接管了该策略键 |

更多运维细节（日志轮转、备份恢复、端口占用、数据库维护）见 [MAINTENANCE.md](MAINTENANCE.md)。

## 技术架构

### 启动与装配

```text
main.py
  └─ app.bootstrap.run()
       ├─ Settings（.env + 环境变量）
       ├─ ConfigService（SQLite data/app.db）      ← 配置 / 账号 / 插件 / 模型 / 人格的唯一真源
       ├─ AgentManager（每账号一个 AgentRuntime）
       ├─ ModuleRegistry（按账号装配插件实例）
       ├─ OneBotGateway（正向 / 反向 WebSocket，断线自动重连）
       ├─ TaskScheduler / CronService（定时任务）
       └─ WebUI（FastAPI：/api/* + Dashboard 静态资源）
```

### 一次请求怎么走完

```text
OneBot 事件（WebSocket）
  └─ infrastructure/onebot     解析成 domain.events.*
      └─ plugin.dispatcher     按账号分发
          ├─ 插件自己 respond()（多插件争抢时由「插件响应策略」仲裁）
          └─ AgentRuntime → loop
                ├─ turn_input     归一化输入（去抖、合并、引用、图片）
                ├─ prompt_prep    取历史 / 记忆 / 上下文 / 工具，构造 PromptRequest
                ├─ assembly       五层装配成 messages
                ├─ providers      provider 链（重试 / 切换备用模型）
                ├─ tool_step      执行工具调用、回流结果、继续下一轮
                └─ delivery       按输出策略投递（纯文本 / 流式 / 发言通道；发言通道仅群聊）
                                  发言方式在这里生效：整段一条 / 收到就发 / 拆开按节奏发
```

流式与非流式共用同一套 prompt 准备与工具循环，差异只在投递（`stream_output` 选传输，`reply_send_mode` 选发言方式）；由架构测试守住。

### 提示词：五层装配

| 层 | 内容 | 谁产出 |
|---|---|---|
| **L1 系统提示词** | 规则与协议：安全规则、工作方式、表达底线、工具与消息格式说明 | 提示词方案 + 内置段落 |
| **L2 人格预设** | 「我是谁」，唯一来源是人格预设 | `persona_preset_id` |
| **L3 钩子注入** | 插件与本轮动态信息：记忆、上下文、群记录、表情标签…… | 插件 / 内置段落 |
| **L4 对话历史** | 窗口 / 压缩 / 开场对话 | 历史窗口、上下文压缩 |
| **L5 本轮消息** | 正文 + 元信息 + 引用 + 图片 | 输入归一化 |

层序优先于段的 `order`：插件段默认落 L3，要进 L1 必须显式声明。前缀稳定性由架构测试守护。

### 分层与依赖规则

| 层 | 职责 |
|---|---|
| `app/core/` | 最底层：配置、日志、事件总线、任务管理、容器 |
| `app/domain/` | 纯领域规则（无 IO）：消息、会话、触发、权限、节奏、切分 |
| `app/infrastructure/` | 唯一 IO 出口：SQLite、OneBot、配置存储、文件路径 |
| `app/plugin/` | 插件框架：发现 / 加载 / 生命周期 / 钩子 / 权限 / 工具源 / 仲裁 |
| `app/agent/` | LLM 对话核心：一次请求的完整路径（组装 → 调用 → 工具循环 → 投递） |
| `app/plugins/` | 内置插件（业务能力） |
| `app/application/` | 用例服务 |
| `app/webui/` | HTTP API + 静态前端 |

四条硬规则由 `tests/architecture/test_architecture_boundaries.py` 守着：
`domain/` 只依赖 `core/`；`infrastructure/` 不依赖上层；生产代码不 import `app.bootstrap`；
`app/agent/__init__.py` 保持薄壳。完整说明见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 开发者指南

### 环境

```bash
python start.py --dev          # 建 venv + 装运行与开发依赖
# 或手动
pip install -e ".[dev]"
```

### 测试与静态检查

```bash
venv\Scripts\python.exe -m pytest -q        # Windows
venv/bin/python -m pytest -q                # macOS / Linux
venv\Scripts\python.exe -m ruff check .
```

- 测试策略（什么该测、什么不该测）见 [docs/TEST_POLICY.md](docs/TEST_POLICY.md)；
- 架构闸门与契约测试在 `tests/architecture/`，新增功能请配套契约测试；
- CI 在 push / PR 上跑 pytest（Python 3.10 / 3.12）、ruff（当前为报告模式）、依赖清单校验与 Dashboard 构建；
  打 `v*` tag 触发 Release 打包。

### 前端

```bash
cd app/webui/dashboard
pnpm install
pnpm build      # vue-tsc --noEmit && vite build → dist/
```

需要 Node 20 与 pnpm（只运行后端时不需要 Node）。

`dist/` 是进仓库的产物，页面加载的是它而不是 `src/`。改前端后必须按
[docs/FRONTEND_VERIFY.md](docs/FRONTEND_VERIFY.md) 起独立测试库预览、截图并量布局，不要只看类型检查通过。

### 写插件

- 插件形态、生命周期、钩子、配置表单、工具与提示词注入：[docs/MODULE_DEV.md](docs/MODULE_DEV.md)
- 目录与命名规范：[docs/PLUGIN_STYLE.md](docs/PLUGIN_STYLE.md)
- 框架可声明的能力点：[docs/FEATURE_REGISTRY.md](docs/FEATURE_REGISTRY.md)
- 可直接复制的骨架：[docs/plugin-template/](docs/plugin-template/)

### 提交规范

- 提交信息用 Conventional Commits（`feat(scope): …` / `fix(scope): …` / `docs: …`），
  可 `git config commit.template .gitmessage` 启用模板；
- 一步一提交，一个提交只做一件事；不把无关改动混进来；
- 详细约定见 [AGENTS.md](AGENTS.md) 与 [STYLE.md](STYLE.md)。

## 文档

| 文档 | 读者 | 内容 |
|---|---|---|
| [docs/README.md](docs/README.md) | 所有人 | 文档地图：哪些是现行文档、各写给谁看 |
| [MAINTENANCE.md](MAINTENANCE.md) | 部署者 / 运维 | 目录结构、环境变量、日志、数据库备份恢复、模块管理、排障 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 改框架代码 | 分层、目录职责、一次请求的完整路径 |
| [docs/MODULE_DEV.md](docs/MODULE_DEV.md) | 写插件 | 插件生命周期、钩子、配置表单、工具与提示词注入 |
| [docs/AGENT_CONFIG.md](docs/AGENT_CONFIG.md) | 改配置 | 每个 Agent 配置键的含义、默认值与生效时机 |
| [docs/TEST_POLICY.md](docs/TEST_POLICY.md) | 所有人 | 什么该测、什么时候跑、删模块时怎么处理测试 |
| [docs/FRONTEND_VERIFY.md](docs/FRONTEND_VERIFY.md) | 改前端 | 构建、预览、截图、量布局的完整步骤 |
| [CHANGELOG.md](CHANGELOG.md) | 所有人 | 每个版本改了什么、修了什么 |

## 选型建议：与 AstrBot 的对比

如果你正在 QQ 机器人框架之间做选择：

| 维度 | QQBot Next | [AstrBot](https://github.com/AstrBotDevs/AstrBot) |
|---|---|---|
| 定位 | 轻量的类 AstrBot 框架，偏"能读能改" | 成熟的多平台 LLM 聊天机器人框架 |
| 稳定性 | **快速迭代中**，接口与配置仍可能变化 | 长期迭代，版本与升级路径更成熟 |
| 插件生态 | 内置 4 个插件，第三方插件靠自建 / 小范围分享 | 大量第三方插件与插件市场 |
| 平台 | OneBot v11（QQ） | QQ / QQ 频道 / Telegram / 微信 / 飞书等多平台 |
| 部署 | `python start.py`，单进程 + SQLite，无 Docker 依赖 | Docker / 命令行 / 桌面端等多种方式 |
| 适合谁 | 想轻量部署、按自己习惯改行为、愿意读源码 | **追求稳定、插件多、生产环境使用** |

> 结论：**要稳、要生态、要上生产 → 选 AstrBot；要轻、要改、要单进程 → 可以试试本项目。**
> 本项目会持续向成熟框架学习，但在达到同等成熟度之前，不会建议你把它用在对稳定性要求最高的场景。

## 贡献与致谢

欢迎 Issue 与 PR。提交前请先读 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [AGENTS.md](AGENTS.md)：
本项目对提交格式、测试策略与前端验证有硬性要求，一个"小而完整"的提交比一个巨大的 PR 更容易被合并。
安全漏洞请按 [SECURITY.md](SECURITY.md) 走私密渠道报告，不要公开 Issue。

**致谢**

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)、[MaiBot](https://github.com/Mai-with-u/MaiBot)、
  [LangBot](https://github.com/langbot-app/LangBot)、[qq-bridge](https://github.com/Derpyu520/qq-bridge)
  等开源机器人框架：在提示词组织、群聊社交仿真、稳定性与易用性设计上提供了重要参考；
- [OneBot](https://github.com/botuniverse/onebot-11) 协议标准，以及 NapCat / Lagrange / LLOneBot 等协议端实现；
- FastAPI、Vue 3、Vuetify、aiosqlite、Pydantic 等开源项目；
- 所有提出问题、提交反馈与使用本项目的人。

## 许可与免责声明

本项目采用 [MIT License](LICENSE)。

- 本项目**不提供** QQ 客户端或协议端，也不绕过平台限制；使用第三方协议端与自动化操作可能违反平台规则；
- 请遵守所在地区法律法规与平台用户协议，账号封禁、数据丢失等风险由使用者自行承担；
- 软件按"原样"提供，不附带任何明示或暗示的担保。
