# 维护与运维

> 读者：自己部署、自己维护这台机器人的用户。
> 安装与首次配置看 [README.md](README.md)；改代码看 [docs/](docs/README.md)。

---

## 1. 它由哪些进程内组件组成

一个 Python 进程里跑四件事（没有独立守护进程，重启 = 停这一个进程）：

| 组件 | 作用 |
|---|---|
| **OneBot 网关** | 每账号一条 WebSocket（正向连出去 / 反向监听），断线自动重连 |
| **模块分发器** | 入站事件 → 权限过滤 → 模块；多个模块抢同一条消息时由「插件响应策略」仲裁 |
| **调度器** | 定时任务（精确到点）+ 模块的后台任务 |
| **WebUI** | FastAPI + 静态前端，默认 `http://127.0.0.1:9200` |

入口：`main.py` → `app.bootstrap.run()`（装配容器 → 初始化 SQLite → 连账号 → 起 WebUI）。
**务必用仓库自带的 `venv/`**，不要用系统 Python 直接跑。

## 2. 目录一览

```
main.py  start.py  start.bat  start.sh     启动入口（start.py 负责建环境/装依赖）
pyproject.toml                             依赖与工具配置（唯一真源）
.env                                       首次启动自动生成（进程级配置 + 令牌）
data/
  app.db                                   配置 / 账号 / 模块 / 模型 / 人格 / 会话（SQLite）
  llm/  media/  module/                    模块数据、媒体缓存、第三方插件与配置
logs/                                      debug/warn/errors/user 四份日志 + 归档目录
app/                                       代码（见 docs/ARCHITECTURE.md）
app/webui/dashboard/dist/                  前端构建产物（已进仓库，克隆即有）
scripts/                                   迁移、更新、打包、前端截图等工具
```

## 3. 启停

```bash
python start.py          # 建 venv → 查/装依赖 → 启动（Windows/macOS/Linux 同一条）
python start.py --check  # 只做只读检查，退出码 1 = 依赖不齐
python start.py --recreate   # venv 坏掉时删掉重建
```

- Windows 可双击 `start.bat`；Linux/macOS 用 `./start.sh`（首次若报换行符错误，`dos2unix start.sh`）。
- 停止：前台 `Ctrl+C`；后台运行建议用 systemd / NSSM 之类的守护，进程退出即全停。
- 不要同时起两个实例：SQLite 与反向 WebSocket 端口都会冲突。

## 4. 进程级配置（.env）

首次启动生成完整 `.env`（含随机 `WEBUI_TOKEN`）；已存在时只补空令牌，不覆盖你的内容。
变量名 = `app/core/settings.py` 字段名大写，**环境变量优先于 `.env`**。

| 变量 | 默认 | 说明 |
|---|---|---|
| `WEBUI_HOST` / `WEBUI_PORT` | `127.0.0.1` / `9200` | 后台监听地址与端口（外网访问请配反向代理 + HTTPS） |
| `WEBUI_TOKEN` | 自动生成 | 后台登录令牌；一键登录链接形如 `/?accesstoken=<令牌>` |
| `WEBUI_AUTO_OPEN` | `false` | 启动后自动打开浏览器（本地 start 脚本默认开） |
| `DB_PATH` | `data/app.db` | SQLite 路径（换库=换一整套配置/账号） |
| `LOG_DIR` | `logs` | 日志目录（日志页与 `/log` 指令都读它） |
| `MODULE_DIR` | `data/module` | 模块数据根（configs / data / plugins / 卸载记录） |
| `PLUGINS_DIR` | `app/plugins` | 内置模块代码目录（可指向别处换一套内置集） |
| `WS_CONNECT_TIMEOUT` / `WS_PING_INTERVAL` / `WS_PING_TIMEOUT` | `30` / `30` / `10` | 连接超时与心跳（秒）；代理/弱网下可调大 |

> 键清单的唯一来源是 `app/core/env_template.py`；漏登记会被 `tests/core/test_env_template.py` 拦下。

## 5. 账号连接

| 方式 | 谁发起 | 填什么 |
|---|---|---|
| 正向 WebSocket | 本程序连出去 | 协议端暴露的 ws 地址，如 `ws://127.0.0.1:3001/` |
| 反向 WebSocket | 本程序监听，协议端连进来 | 监听地址，如 `ws://0.0.0.0:3001/onebot/v11/ws` |

- 两种方式能力一致；`access_token` 都支持（正向放 URL，反向校验 `Authorization: Bearer` 或 URL 参数，不匹配以 4401 关闭）。
- 反向监听端口需自行保证未被占用；对端重启会自动重连。
- 连不上先看「账号管理」里的状态与诊断台，再看协议端日志。

## 6. 日志

| 文件 | 内容 |
|---|---|
| `logs/debug.log` | 完整技术日志（默认写） |
| `logs/warn.log` / `errors.log` | 只含对应级别 |
| `logs/user.log` | 给人看的简洁日志（后台默认显示这份） |

- **轮转**：每 6 小时切段，四份同步归档到 `logs/<日期-小时>/`，保留 48 份。
- **后台**：日志页可切"简洁 / 原始"；可按时间段导出 ZIP。
- **开启提示词调试**：启用内置「LLM 调试」模块后，`[Prompt]` 头行会打「层摘要 + system token + 前缀指纹」，
  随后是完整 messages，可用于逐轮对比。

## 7. 数据备份与恢复

**唯一需要备份的是 `data/`（核心是 `data/app.db`）和 `.env`。**
`logs/`、`app/webui/dashboard/dist`、`venv/` 都不用备份。

```bash
# 在线备份（SQLite 官方方式，WAL 下直接 copy 可能丢最新写入）
sqlite3 data/app.db ".backup 'backup-$(date +%F).db'"
# Windows 没装 sqlite3 时：先停进程再复制 data/app.db（及其 -wal/-shm）
```

恢复：停进程 → 用备份覆盖 `data/app.db`（同时删掉 `app.db-wal`/`app.db-shm`）→ 启动。
`scripts/update.py apply` 每次都会自动备份 `data/` 与 `.env`，回滚用 `scripts/update.py rollback`。

数据库里有哪些表：配置与账号（`agent`、`bots`、`modules`…）、模型与人格（`provider_*`、`role_presets`）、
会话（`sessions`、`conversations`、`messages`）、统计数据。想手工查：`sqlite3 data/app.db ".tables"`。

## 8. 模块（插件）管理

- 内置模块在 `app/plugins/`（随仓库，只读语义）；第三方模块在 `data/module/plugins/<name>/`。
- 页面「功能插件」可安装 zip、启停、进入配置页、卸载；装/卸会执行模块的 `on_load` / `on_unload`。
- **禁用 ≠ 不加载**：禁用的模块不会跑 `on_load`（因此不会注册定时器/工具），重新启用才重新加载。
- 模块配置与授权存在 `data/app.db`，模块自己的数据在 `data/module/data/<name>/`；升级不会丢。
- 卸载模块不会自动删它的数据目录——确认不再用可以手动删。

## 9. 更新

```bash
python scripts/update.py status                  # 当前版本 / 待应用 / 可回滚
python scripts/update.py stage --kit <kit.zip>   # 校验并暂存（不动线上）
python scripts/update.py apply                   # 备份 data 与 .env 后切换
python scripts/update.py rollback                # 退回上一版（含数据库备份）
```

从更早的大版本升级时，按提示跑迁移脚本（都支持 `--dry-run`、自动备份 DB），例如
`scripts/migrate_v3_owner_to_roster.py`（拥有者名册）、`scripts/migrate_v3_split_config.py`（配置拆分）。

## 10. 常见故障

先看**总览页「运行状态」**或 `GET /api/readiness`：它按"差什么、去哪修"逐条列。

| 现象 | 先查 |
|---|---|
| 启动直接退出 | 退出码：1=venv、2=依赖、3=缺 main.py、4=Python 版本不符（控制台已写明修复命令） |
| 端口被占用 | 后台端口（默认 9200）或反向 WS 端口（默认 3001）；`netstat -ano \| findstr :9200` |
| 账号一直连不上 | 反向：协议端是否填同一地址/Token；正向：地址端口；诊断台是否有事件 |
| 连上但不回话 | 回复开关（群聊默认关）、权限、模型是否配置 |
| 有回复但内容空/报错 | 模型池为空、Key 失效、模型不支持工具调用 |
| 改了配置不生效 | 账号级被群级覆盖（「回复与权限」标了来源）；模块配置是否在该模块自己的页面 |
| 前端打不开/白屏 | `app/webui/dashboard/dist` 是否在；改了源码要重新 `npm run build` |
| 内存/磁盘涨得快 | `data/media`（媒体缓存，可在设置页清理）、`logs/` 归档、`data/llm` 下的模块数据 |

## 11. 开发侧验证（改代码后）

```bash
venv\Scripts\python.exe -m pytest tests/architecture -q     # 结构性闸门
venv\Scripts\python.exe -m pytest -q                        # 最终验收跑全量
venv\Scripts\python.exe -m ruff check app tests
```

CI（`.github/workflows/ci.yml`）在每次 push/PR 跑：后端全量测试（py3.10 / 3.12）、
ruff、依赖元数据校验、前端构建。本地日常改动不必每次全量，见 [docs/TEST_POLICY.md](docs/TEST_POLICY.md)。

## 12. 安全建议

- 后台**不要直接暴露公网**：保持 `WEBUI_HOST=127.0.0.1`，需要外网就上反向代理 + HTTPS + 额外鉴权。
- `WEBUI_TOKEN` 泄露等于后台泄露；怀疑泄露就改 `.env` 里的令牌并重启。
- `.env` 与 `data/app.db` 含 Key 与聊天记录，不要提交到公开仓库（`.gitignore` 已覆盖）。
- 高危工具（OneBot 全量 API 等）建议开「高危工具需人工审批」，并按拥有者/管理者名册限制触发者。
