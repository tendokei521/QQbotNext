# QQBot Next 维护文档

> 适用版本：2.0（分层 + 插件架构）。日常维护、故障排查、备份恢复看这一份就够。

---

## 1. 系统概览

- **入口**：`main.py` → `app/bootstrap.py run()`（装配容器 → 初始化 SQLite → 连接 Bot → 启动 WebUI + 定时器）
- **运行环境**：`venv/` 虚拟环境（**必须用它**，见 §12.1）
- **三大常驻组件**：
  - OneBot 网关（`app/infrastructure/onebot/gateway.py`）—— 多账号 WebSocket 连接 / 断线重连
  - 模块分发器（`app/modules/dispatcher.py`）—— 事件 → 权限过滤 → 模块
  - WebUI（`app/webui/`）—— FastAPI 管理后台，默认 `http://127.0.0.1:9200`

## 2. 目录结构

```
main.py / pyproject.toml / .env.example
app/
├── bootstrap.py         装配与生命周期
├── core/                settings / container / logger / task_manager / event_bus
├── domain/              IBot 抽象 / 事件 / 消息模型
├── infrastructure/      onebot(网关/API/编解码) + config(配置中心) + persistence(SQLite)
├── llm/                 框架级 LLM Agent（provider/会话/定时/主动/工具）
├── modules/             BaseModule / registry / authority / dispatcher / api(插件API)
├── nodes/               MessageNode 框架（base/registry/outbound）——消息分发「一切皆节点」
├── services/            bot_service / scheduler(定时) / log_service / provider(数据源)
└── webui/               管理后台（api/ + schemas/ + templates/ + static/）
module/                  ★ 插件目录（三级结构）
├── modules/             业务插件主体（<name>/module.py、config_schema.py、service.py、pages/）
├── configs/             每模块配置（<name>/config.json + authority.json，迁移源）
└── data/                每模块可选持久化数据（get_data_path 自动创建）
data/                    SQLite（app.db）+ 框架级 LLM 数据（data/llm）
logs/                    debug.log / warn.log / errors.log / user.log + 归档目录
```

> **注意**：模块配置迁移源从旧的 `modules/<name>/configs.json`、`authority.json` 迁到
> `module/configs/<name>/config.json`、`authority.json`；模块主体代码在 `module/modules/<name>/`。
> 首次启动若 DB 为空，自动从 `module/configs/*/` 迁移一次，之后以 SQLite 为准。

## 3. 启动 / 停止

```bash
# 启动（推荐）
venv\Scripts\activate.bat
python main.py

# 或双击 start.bat
```

- **停止**：`Ctrl+C`。框架会顺序关闭：定时任务 → 网关(断开所有 Bot) → SQLite → 后台任务。
- **确认是否在跑**：WebUI 端口（默认 9200）有进程监听即运行中。
  ```bash
  netstat -ano | grep 9200
  ```

## 4. 配置管理

### 4.1 环境变量（.env，变量名 = 字段名大写，无前缀）

| 变量 | 默认 | 说明 |
|---|---|---|
| `WEBUI_HOST` | `127.0.0.1` | WebUI 监听地址（外网访问请配反向代理，勿直接 0.0.0.0 裸奔） |
| `WEBUI_PORT` | `9200` | WebUI 端口 |
| `WEBUI_TOKEN` | 空 | 非空则 API 需 `Authorization: Bearer <token>` |
| `DB_PATH` | `data/app.db` | SQLite 路径 |
| `LOG_DIR` | `logs` | 日志目录 |
| `WS_CONNECT_TIMEOUT` | `30` | 连接超时（秒） |
| `WS_PING_INTERVAL` / `WS_PING_TIMEOUT` | `30`/`10` | WebSocket 心跳 |

参考 `pyproject.toml` 与 `app/core/settings.py`。改完 `.env` 需重启生效。

### 4.2 配置分层

所有配置集中在 **SQLite（`data/app.db`）**，通过 WebUI 修改即落库：
- 账号配置（ws_url / owner_id / auto_connect）
- 模块配置 + 权限（启停 / 黑白名单）
- WebUI 偏好（日志显示级别 / “显示原始日志”开关 / 单一服务 / 多群管理）

> **旧 JSON 迁移**：首次启动若 DB 为空，会从 `webserver/webconfig.json`、`webui/webui_config.json`、`module/configs/*/config.json`、`module/configs/*/authority.json` 自动迁移一次。迁移后以 SQLite 为准，旧 JSON 仅作迁移源保留。

## 5. 日志

- **文件**：
  - `logs/debug.log`：原始完整日志（全部级别）
  - `logs/warn.log`：WARNING+
  - `logs/errors.log`：ERROR+
  - `logs/user.log`：用户简洁日志（系统日志 + 消息收发/通知/请求 + API 错误，不含 API 底层成功日志与 DEBUG）
- **轮转**：`debug.log / warn.log / errors.log / user.log` 四个文件每 6 小时**同步归档**到 `logs/YYYY-MM-DD-HH/` 子目录，保留 48 份，超出自动清理
- **WebUI**：日志面板默认显示简洁日志；开启“显示原始日志”后读取 `debug.log` 显示完整技术日志
- **控制台**：控制台输出与 WebUI 的“显示原始日志”开关同步；无论开关状态，`debug.log` 始终完整记录
- **查看最近日志（按级别）**：
  ```bash
  tail -100 logs/debug.log
  tail -100 logs/user.log
  grep ERROR logs/errors.log | tail -50
  ```

## 6. 账号连接

- WebUI「账号连接管理」里增删改账号；账号配置输入框失焦会自动保存，也可点“保存所有配置”手动保存。`auto_connect=true` 的账号启动时自动连接。
- **断线重连**：auto_connect 账号掉线后按**指数退避**自动重连：
  `10s → 20s → 40s → 80s → 160s → 300s（封顶）`，连接成功归零。
- 连接失败常见原因：OneBot 服务未启动 / 地址端口错误 / `access_token` 不匹配 → 查 `logs/errors.log`。

## 7. 模块管理

### 7.1 模块结构

每个插件 = `module/modules/<name>/`（模块内部用相对导入）＋ `module/configs/<name>/`（配置迁移源）：

```
module/modules/<name>/
  module.py              插件声明（Module(BaseModule)）
  config_schema.py       WebUI 表单定义
  service.py             业务逻辑
  pages/index.html       可选自定义配置页
module/configs/<name>/
  config.json            配置迁移源（首次启动迁入 SQLite，之后以 SQLite 为准）
  authority.json         权限迁移源
module/data/<name>/      可选持久化数据（get_data_path 自动创建）
```

```python
# module.py —— 声明
from .config_schema import SCHEMA
class Module(BaseModule):
    name = "插件名"; sign = "Sign"
    permission = "member"            # everyone/member/group_admin/group_owner/owner
    subscribe = ("message_group",)   # 订阅事件类型
    SCHEDULES = {"05:00:00": "daily_push"}   # 可选：定时任务
    LIST_PROVIDERS = {"groups": "list_groups"}   # 可选：list 数据源
    DYNAMIC_PROVIDERS = {...}        # 可选：dynamic 数据源
    config_schema = SCHEMA           # 来自 config_schema.py
    async def handle(self, event): ...   # 事件入口
```

### 7.2 常用操作

| 操作 | 方式 |
|---|---|
| 新增模块 | 在 `module/modules/` 建目录写文件，重启；或 WebUI「刷新模块」 |
| 启停 / 权限 | WebUI 模块卡片开关 + 群/用户黑白名单 |
| 热重载 | WebUI「刷新模块」按钮（彻底卸载→重新导入，清空后台任务与定时器） |
| 删除模块 | 删目录 + 重启 |

### 7.3 注意

- **模块权限由框架统一过滤**：`permission` 声明 everyone/member/group_admin/group_owner/owner，黑白名单仍作为前置过滤。
- **LLM 是框架级能力**：WebUI 侧栏的「LLM服务」为虚拟模块卡（`app/llm/`），不依赖任何插件目录，
  删除全部插件后仍保留；其配置/定时/主动走框架运行时（`module_config("agent")`、`data/llm`）。
- 模块卸载时框架会自动取消其后台任务（`TaskManager`）与定时任务（`SchedulerService`）。

## 8. 定时任务（精确到点）

模块在 `Module` 类声明 `SCHEDULES = {"HH:MM[:SS]": "方法名"}`，加载时自动注册、卸载时注销。

```python
class Module(BaseModule):
    SCHEDULES = {"00:00:00": "daily_job"}
    async def daily_job(self):
        # self.ctx.bot = 当前账号 IBot；self.config = 该账号配置
        ...
```

- 每天在指定时刻触发**一次**，精确到秒、不漂移（monotonic deadline）。
- 仅对**真实 Bot 实例**生效；全局（WebUI 展示）实例不注册。
- 内置示例：`msg_df_password`（05:00 推密码）、`time_sign_in`（00:00 打卡）。
- 已移除旧 `time_core` 轮询广播。

## 9. 配置 Schema 与动态数据源

字段类型见 `module/CONFIG_SCHEMA.md`。要点：

- **`list`**：后端数据列表（勾选/拖拽排序/全开-部分-全关）。内置 `groups`（群）、`friends`（好友）数据源，模块也可自注册 `LIST_PROVIDERS`。
- **`dynamic`**：后端动态下拉 + 每选项独立表单，`DYNAMIC_PROVIDERS`。
- **`string_list`**：可增删字符串列表（曾用名 `list`，已改名）。
- **`showIf` / `time` / `repeater`**：条件显示 / 时间选择 / 可增删分组。
- **保存**：`module.config.set(key, value)` 会自动按旧值类型做安全转换（bool/int/float/str），支持点号 `config.api_key`。

## 10. 数据库维护

### 10.1 备份

```bash
# 推荐：SQLite 在线备份（WAL 模式下直接拷文件可能丢最新数据）
venv\Scripts\python.exe -c "import sqlite3; sqlite3.connect('data/app.db').backup(sqlite3.connect('data/app_backup_$(date +%F).db'))"
```
或停机后直接复制 `data/app.db`。

### 10.2 恢复

把备份文件改名为 `data/app.db` 后重启即可。恢复的是：账号配置、模块配置/权限、WebUI 偏好。

### 10.3 重置

删除 `data/app.db`（及其 `-wal`/`-shm`）后重启 → 会从旧 JSON **重新迁移**一次。若已不需要旧配置，先备份旧 JSON 再删除 DB。

### 10.4 手动查库

```bash
venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/app.db'); print(c.execute('select module_name,bot_id from module_config').fetchall())"
```

## 11. WebUI 维护

- **打不开**：确认进程在跑；确认 `WEBUI_HOST/PORT`；外网访问需放行端口或反代。
- **模块列表为空**：首次打开时模块列表来自「全局实例」，若为空说明模块未加载成功 → 查 `logs/errors.log` 的 `[Module]` 加载失败记录。
- **实时日志不显示**：控制台走 `/ws/logs?mode=simple|raw` WebSocket，若浏览器控制台报错刷新即可；后端日志仍会写入 `logs/`。
- **改了模块配置不生效**：点「保存配置」后模块运行逻辑读 `module.config`；个别模块需「刷新模块」重载。

## 12. 常见问题排查

### 12.1 环境 / 启动类

| 现象 | 原因与处理 |
|---|---|
| 启动报 fastapi/starlette 相关错误 | 用了**基础 Python**。必须用 `venv\Scripts\python.exe`（venv 内 fastapi 0.138 + starlette 1.2 配套；基础 Python310 的 fastapi 0.104 与其 starlette 1.3 不兼容） |
| 端口被占用 | `netstat -ano | grep 9200` 找到 PID 后结束，或改 `WEBUI_PORT` |
| 首次启动很慢 | 正在从旧 JSON 迁移 + 加载 14 个模块，正常 |

### 12.2 连接类

| 现象 | 原因与处理 |
|---|---|
| 某 Bot 反复「连接失败」 | 地址/`access_token` 错，或 OneBot 未启动；改配置或关掉它的 auto_connect |
| 连接后立即报 `token验证失败` / `WebSocket失败响应` | OneBot 端 `access_token` 不匹配；现在会视为连接失败并断开，检查 token 后重连 |
| Bot 连上了但群列表空 | 登录信息获取失败 → 查 `logs/errors.log` 的 `获取群聊列表失败` |
| 重连太频繁 | 已是指数退避；若仍嫌吵，把该 Bot 的 auto_connect 关掉改手动 |

### 12.3 消息 / 模块类

| 现象 | 原因与处理 |
|---|---|
| 模块不响应 | ①模块开关没开 ②权限黑白名单拦了 ③`permission` 角色不满足 ④`subscribe` 不含该事件 |
| 单一服务模式下群消息不触发 | 该群未在「多群管理」指定服务账号，或当前账号非服务账号 |
| 消息处理慢（曾出现每条约 1.7s） | 已知旧 bug（`_eventable_sync` off-by-one）已修复；若仍慢查模块内是否调用网络 API |
| 群消息被静默丢弃 | 旧 bug（多 Bot 去重 `_message_indexes` 为空时丢弃、等待超时无兜底）已修复：无跟踪直接放行、有界等待超时由等待方处理 |
| LLM 不回复 | WebUI「LLM服务」卡（框架级 Agent）里检查 api_key 是否已填、群/私信开关是否开启 |

### 12.4 WebUI / 前端类

| 现象 | 原因与处理 |
|---|---|
| `list` 字段空白 | 未连接 Bot（数据源需从 Bot 拉群/好友）→ 连接后自动加载，或点「刷新」 |
| 拖拽排序无效 | 浏览器需支持 HTML5 drag；已修复 `setData` 与作用域问题，强刷新（Ctrl+F5） |
| 刷新后账号回到 #0 | 选择账号会写入 localStorage；若仍回 #0 说明记忆的是 #0 |
| 首次进页面模块列表空 | 正常，加载「全局实例」；Bot 连接后切换账号即显示其配置 |

## 13. 安全建议

- **默认只绑 `127.0.0.1`**；需要远程管理时，用反向代理（Nginx/Caddy）+ HTTPS，并设置 `WEBUI_TOKEN`。
- **token 鉴权**：设置 `WEBUI_TOKEN` 后，所有 `/api/*` 与 `/ws/logs` 需携带 `Authorization: Bearer <token>`（前端页面本身无需 token）。
- 不要在群里转发 `.env` / `data/app.db`；`data/`、`logs/` 已在 `.gitignore`。

## 14. 测试

```bash
venv\Scripts\python.exe -m pytest -q
```
- 覆盖：领域编解码 / 权限 / 配置中心(含迁移) / 模块注册表 / 事件分发 / Provider 与 list/dynamic API / 定时任务。
- 新增功能后跑一遍全量，确保 165+ 用例全绿。

## 15. 架构演进备忘（本版相对 v1 的变化）

- 旧 `basic/`、`webserver/` 单体 → `app/` 分层（core/domain/infrastructure/modules/services/webui）
- JSON 直读写 → **SQLite 配置中心**（首次自动迁移）
- `time_core` 轮询广播 → **精确到点定时器**（SCHEDULES）
- 模块配置 `list` 类型改名 `string_list`；新增 `list`（后端数据）/ `dynamic` / `repeater` / `showIf`
- 前端账号选择持久化（localStorage）
- **消息分发「一切皆节点」**：dispatcher 硬编码过滤 → `app/nodes` 入站节点链
  （Router → Permission → Invoke），出站发送可拦截；`app/modules/nodes.py` 为内置节点，
  `app/modules/keyword.py` 为共享关键词库；支持父模块递归子模块（`parent.child` 配置命名空间）。
- **模块流水线 + LLM 流水线**：模块流水线在前（`@module_hook`），LLM 流水线在后
  （`@llm_hook`：pre_request / post_response / pre_send / post_send）；LLM 请求池
  `app/llm/pool.py` 支持同会话防抖合并；`event.llm.resume()` 手动放行。
- **日志双系统**：`debug.log` 保留原始完整日志，新增 `user.log` 简洁日志；
  `debug/warn/errors/user` 四文件同步 6h 轮转；WebUI“显示原始日志”开关与控制台输出同步。
