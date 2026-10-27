# 协议端归属设计：让「与协议端交互」只由账号管理配置

> **状态**：设计稿（只评审，未动代码）。
> **目标一句话**：账号是唯一的配置面——协议端的数据面、控制面、进程面、登录态全部挂在账号上；
> 程序级只保留「可用版本清单 + 安装缓存」这类与具体 QQ 登录无关的共享只读资源。
> **验收口径**：除版本清单外，任何协议端地址 / 版本 / 启动参数都只能从「账号管理」写入。

---

## 0. 先定义：协议端是干什么的

本项目的「协议端」指 **OneBot v11 实现**（NapCat / Lagrange / LLOneBot 等），是一个**外部独立进程**，
不是本程序的一部分。它承担四件本程序不做的事：

| # | 协议端负责 | 本程序负责 |
|---|---|---|
| 1 | **登录并维持 QQ 会话**：扫码、掉线重登、风控退避 | 不实现任何 QQ 协议；只在 WebUI 展示二维码并接收结果 |
| 2 | **上行**：把 QQ 消息/事件（群/私聊/通知/请求）编码成 OneBot v11 JSON，经 WebSocket 推给我们 | 反序列化、路由、业务处理 |
| 3 | **下行**：接收我们的 OneBot action（发消息、撤回、禁言、取群列表、戳一戳…）并翻译成 QQ 客户端操作 | 生成 action、处理返回 |
| 4 | **可选管理面**：HTTP API 提供二维码、登录状态、自身配置（NapCat 默认 `:6099`） | 代为请求并展示，让用户不必打开协议端自己的面板 |

> 边界：本程序**不自带 QQ 客户端**（README 已写明）。协议端可以完全由用户自己装、自己起，
> 本程序只当作「一个说 OneBot v11 的 WebSocket 对端」。

### 0.1 「与协议端交互」其实是四个面

| 面 | 内容 | 今天的落点 | 是否符合预期 |
|---|---|---|---|
| **数据面** | WS 方向（正向/反向）、地址、`access_token` | 账号 `ws_url` / `conn_mode` / `access_token`（`bots` 表 + 账号管理） | ✅ 唯一来源 |
| **控制面** | 协议端 HTTP API 地址与令牌（扫码、登录状态） | 硬编码 `http://127.0.0.1:6099`（全局单例） | ❌ 不在账号里 |
| **进程面** | 装哪个实现、哪个 pin 版本、启动参数、启停、日志、崩溃拉起 | 侧栏「协议端」页，按**协议名**（napcat/lagrange）管理 | ❌ 与账号脱钩 |
| **登录态** | 二维码、是否在线、登录的是哪个 QQ | 全局「协议端」页，不区分账号 | ❌ 不区分账号 |

**结论**：四个面里只有数据面归了账号。所谓「仅由账号管理配置」，就是把这四个面里
**凡与某个 QQ 登录绑定**的部分全部收进账号；清单/缓存这类共享资源留在程序级（见 §2.4 例外条款）。

---

## 1. 现状：为什么与预期不符（证据）

### 1.1 三处都能写账号连接配置，规则互不相同

| 入口 | 代码位置 | 实际规则 |
|---|---|---|
| 账号管理 | `app/webui/dashboard/src/views/BotsPage.vue:254-337` → `app/webui/api/bots.py:127-149` | 多账号；正/反双向；`auto_connect`/`owner_id`；保存时 `parse_endpoint` 校验 |
| 首启向导 | `app/webui/dashboard/src/views/SetupPage.vue:71-91` → `app/webui/api/setup.py:89-120` → `app/application/setup_account.py:127-204` | **只允许一个账号**（多于 1 个直接拒绝，:146-151）；强制 `reverse`（:134,:183）；强制 `auto_connect=True`（:184）；改 ID 会搬 bindings（:206-227） |
| 协议端面板扫码后 | `app/webui/dashboard/src/views/ProtocolPage.vue:119-130` → `app/webui/api/protocol.py:148-167` → `app/application/protocol/login.py:137-177` | 强制 `reverse`（:169）；覆盖 `ws_url`/token；**不校验地址**；默认值硬编码 3001（`protocol.py:162`、`login.py:25`） |

这直接违反本仓库自己的规则（`docs/plan/README.md` §3.6「不新增同一语义两处配置」）。

### 1.2 协议端进程与账号完全脱钩（结构性问题）

- `ProtocolSupervisor` 以**协议名**为 key（`app/application/protocol/supervisor.py:49`），
  `start/stop/restart` 不带任何账号上下文；「哪个账号的协议端实例」这个概念不存在。
- 账号模型只有 `ws_url / conn_mode / auto_connect / owner_id`
  （`app/infrastructure/persistence/database.py:128-141`、`app/infrastructure/config/account_store.py:137-144`），
  **没有**协议端种类、安装版本、控制面地址。
- 登录服务 HTTP base 在构造时写死 `http://127.0.0.1:6099`（`app/application/protocol/login.py:49`，
  `app/bootstrap.py:159`），且只接受 `127.0.0.1`/`localhost`（`login.py:35-43`）。
  第二个实例 / 别的端口想扫码，只能改代码。
- 设计稿本来写的是「**与账号绑定的生命周期**」（`docs/plan/onboarding-bundle.md` P5-2 行），实现没落地。

### 1.3 默认值漂移，用户直接踩坑

- `3001` + `/onebot/v11/ws`：`app/infrastructure/onebot/endpoint.py:147-151`、`app/application/protocol/login.py:25`、
  `app/webui/api/protocol.py:162`、`BotsPage.vue:268` 占位符。
- `4001/ws`：`app/application/setup_account.py:23`、`SetupPage.vue:27`。

同一产品两条默认反连地址，且**向导那条缺 OneBot 约定路径 `/onebot/v11/ws`**：
用户照向导填进 NapCat 的反向 WS 配置，大概率路径对不上。

### 1.4 协议端面板默认是空壳

- 内置清单 `versions` 全空（`manifest.py:33-47`），`/api/protocol` 永远没有可装版本 →
  安装按钮不出现（`ProtocolPage.vue:192-200`）。只有用户手写 `data/protocol/manifest.json` 才有版本。
  注释说是刻意（不指向未验证第三方构建），但 README「协议端托管（可选）」读起来像开箱即用。
- 启动命令探测写死（`supervisor.py:131-145`）；`args` 只能从 API body 传，UI 无入口。
  协议端怎么起、带什么配置，都不在账号管理里。

### 1.5 可观测性割裂

协议端页看不到「它服务哪个账号」；账号卡片只有监听/重连/当前 QQ
（`BotsPage.vue:306-327`），看不到协议端装没装、在不在跑、登录没。
用户必须在两个顶级页面之间对暗号——正是 `docs/oneclick-bundle-and-onboarding-design.md` §1.3 S5/S6 记录的通病。

### 1.6 现状 → 目标 对照

| 项 | 现状 | 目标 |
|---|---|---|
| supervisor key | 协议名 `napcat`/`lagrange` | `account_id`（实例） |
| 登录 HTTP base | 全局硬编码 `:6099` | 账号 `control_url` |
| 协议端种类/版本/参数 | 面板全局选择 / 写死 | 账号绑定字段 |
| 默认地址 | 3 处（3001、3001、4001） | 1 处派生函数 |
| 写账号连接配置 | 3 个入口 | 1 个服务入口（账号管理） |
| 导航 | 「账号管理」「协议端」并列 | 只有「账号管理」（协议端是卡片内区段） |

---

## 2. 设计原则

1. **A1 账号聚合根**：账号 = 一个 QQ 登录 + 承载它的协议端。协议端的一切「属于这个登录」的配置都挂在账号上。
2. **A2 一个事实一个来源**：连接地址/方向/token、控制面地址、版本、启动参数都只有一个写入口（账号管理服务）。
3. **A3 默认值只派生一次**：所有默认地址/端口来自同一个函数，UI、向导、API 不得各自写字面量。
4. **A4 外部模式永远可用**：不托管协议端的用户路径逐字节保持今天的行为；`external` 是默认模式。
5. **A5 托管是账号的能力，不是全局服务**：install/start/stop/QR 都以账号为作用域；实例目录、日志、端口按账号隔离。
6. **A6 UI 单入口**：协议端交互不出「账号管理」页面；向导只是账号管理第一屏的另一个皮，不是第二套逻辑。
7. **A7 失败可回滚**：新增表/字段默认值 = 旧行为；不自动启动任何进程。

### 2.4 例外条款：哪些东西**不**进账号

- **可用版本清单**（`data/protocol/manifest.json`）：程序级共享只读资源，描述「这个世界有哪些可装的 pin 版」，与具体账号无关。
- **安装缓存**（`data/protocol/versions/<kind>/<version>/`）：同一 pin 版被多个账号复用，按 kind+version 去重。
- 账号只引用「kind + version」，不复制二进制。

---

## 3. 目标模型

### 3.1 数据模型

`bots` 表**不改**（数据面字段保持现状，保证外部模式与旧库零迁移）。
新增 1:1 绑定表：

```sql
CREATE TABLE IF NOT EXISTS account_protocol (
    account_id    TEXT PRIMARY KEY REFERENCES bots(account_id) ON DELETE CASCADE,
    -- external：用户自己起协议端，本程序只连 WS（默认，= 今天的行为）
    -- managed ：本程序负责安装/启动/日志/扫码
    mode          TEXT NOT NULL DEFAULT 'external',
    kind          TEXT NOT NULL DEFAULT '',          -- napcat | lagrange | ...
    version       TEXT NOT NULL DEFAULT '',          -- pin 版本；managed 必填
    control_url   TEXT NOT NULL DEFAULT '',          -- 空 = 按 kind 派生默认（本机 HTTP API）
    control_token TEXT NOT NULL DEFAULT '',          -- NapCat 管理面令牌（可选）
    launch_args   TEXT NOT NULL DEFAULT '[]',        -- JSON 数组，启动参数
    instance_dir  TEXT NOT NULL DEFAULT '',          -- 空 = data/protocol/instances/<account_id>
    auto_start    INTEGER NOT NULL DEFAULT 0,        -- 账号连接时是否顺带启动托管进程
    updated_at    INTEGER NOT NULL DEFAULT 0
);
```

**兼容规则**：表里**没有行** ≡ `mode=external` ≡ 今天的账号。
所以升级瞬间不改任何账号行为；只有用户显式改成 `managed` 才会出现安装/启动/扫码按钮。

### 3.2 默认值单一来源

新增 `app/application/protocol/defaults.py`（纯函数，无 IO）：

```python
def data_ws_url(mode: str, port: int) -> str:      # reverse: ws://127.0.0.1:<p>/onebot/v11/ws
                                                   # forward: ws://127.0.0.1:<p>/
def control_url(kind: str, port: int) -> str:      # napcat: http://127.0.0.1:<p>
def allocate_data_port(taken: set[int]) -> int:    # 从 3001 起找空位（复用 endpoint.suggest_address 逻辑）
def allocate_control_port(kind: str, taken: set[int]) -> int  # napcat 从 6099 起找空位
```

- `endpoint.py` 的 `suggest_address` 迁移到此处（或反向复用），全仓只保留一份端口/路径知识。
- 删除：`setup_account.DEFAULT_LISTEN_ADDRESS`（4001）、`login.DEFAULT_REVERSE_WS`（3001）、
  `protocol.py:162` 的兜底字面量。
- 端口一旦写进 `account_protocol.control_url` / 账号 `ws_url` 就**固定**，重启不重新分配
  （否则用户填在 NapCat 里的反连地址会漂移）。
- 卫兵：`grep -rn "4001\|6099\|3001" app/` 只允许出现在 `defaults.py` 与测试里。

### 3.3 运行时

| 层 | 位置 | 生命周期 |
|---|---|---|
| 版本清单 | `data/protocol/manifest.json`（可选覆盖） | 只读；用户维护 |
| 安装缓存 | `data/protocol/versions/<kind>/<version>/` | 跨账号共享；重装先下载到 staging，校验后原子替换 |
| 账号实例 | `data/protocol/instances/<account_id>/`（配置/日志/运行期文件） | 跟随账号：删除账号即停止并清理（日志可保留） |
| 进程 | `ProtocolSupervisor._state[account_id]` | 跟随账号连接/断开；框架退出 `stop_all()`；崩溃拉起按账号限次 |

> 现有布局是 `data/protocol/<kind>/<version>/` + 每 kind 一份 `current.json`/`previous.json`，
> 语义是「这个协议端默认装哪版」。改成账号 pin 后，**版本以账号绑定为准**；
> `current.json` 只保留为安装页的「建议版本」，升级/回滚按 `(kind, version)` 共享缓存操作。
> 迁移时旧布局按原样可读（读不到 `versions/` 就回落旧路径），不做破坏性搬移。

- **安装并发**：同 `(kind, version)` 加锁；重装/升级前若该版本正被账号运行 → 拒绝，提示先停。
- **实例隔离**：托管进程的 cwd/配置目录指向实例目录，避免多账号共用 NapCat 全局配置。
- **NapCat 多实例**（⚠ 待实测）：需要独立配置目录与端口参数；若上游不支持，
  `managed` 在 v1 退化为「一个账号一个托管实例，第二账号只能用 external」，文档必须写明。

### 3.4 服务层

- `ProtocolBindingService`（application）：读写 `account_protocol`，并与 `bots.ws_url/conn_mode` 联合校验
  ——**唯一**的账号+协议端保存入口。向导、账号页、套装导入都调用它。
- `ProtocolInstaller`：算法不变（staging → sha256 校验 → 原子替换 → 保留上一版），
  只把安装根从 `data/protocol/<kind>/` 调整为 `data/protocol/versions/<kind>/<version>/`
  （与 `instances/` 分开），并提供「旧布局可读」的兼容回落。
- `ProtocolSupervisor`：key 改 `account_id`；`start(account_id)` 从绑定读 kind/version/args/instance_dir；
  `status/log/stop/restart` 同作用域。
- `ProtocolControlClient`（原 `ProtocolLoginService`）：构造从「全局 base_url」改为「per-call (control_url, token)」；
  `capabilities(kind)` 声明是否支持 HTTP 扫码（NapCat 支持；Lagrange 走文件/日志二维码，v1 可只支持 external）。
- `SetupAccountService`：**删除**。向导第一步直接调 `ProtocolBindingService`（或账号管理同一个 `BotService.add_bot`），
  不再有「唯一账号 / 强制反向 / 后台 2s 循环」特例；已有账号时向导跳转 `/bots`。

### 3.5 API 形状（账号作用域）

```
GET    /api/bots                                  # 账号列表（含 protocol 子对象，脱敏）
PUT    /api/bots/{account_id}/protocol            # 保存绑定（唯一写入口）
GET    /api/bots/{account_id}/protocol/status
GET    /api/bots/{account_id}/protocol/log?lines=
POST   /api/bots/{account_id}/protocol/install    # body: {version?}
POST   /api/bots/{account_id}/protocol/start|stop|restart
GET    /api/bots/{account_id}/protocol/qrcode
GET    /api/bots/{account_id}/protocol/login-status
POST   /api/bots/{account_id}/protocol/login/complete   # 只回填该账号；不猜「当前账号」
GET    /api/protocol/manifest                     # 全局只读版本清单（保留）
```

旧 `/api/protocol/{name}/*` 标记弃用（一个版本周期后 410）。`login/complete` 不再接受
「覆盖 ws_url 成默认 3001」，一律返回绑定里已配置的数据面。

### 3.6 UI 形状

- **账号管理**（`/bots`）：每张账号卡片增加可折叠「协议端」区段：
  - 模式：`使用外部协议端`（默认）/ `由本程序托管`
  - 托管时：kind 下拉（来自清单）、版本下拉（pin）、控制面地址（只读自动分配，可改）、
    启动参数（高级）、状态（已装/运行/PID/重启次数）、[安装][启动][停止][日志][扫码]
  - 外部时：只显示一行「使用你自带的协议端，连接信息在上方」+ 数据面字段（现有）
- **删掉**侧栏「协议端」与 `/protocol` 路由（重定向到 `/bots`）。
- **向导**第一步：复用账号卡片组件（或同一组 API），默认值来自 `defaults.py`；没有「另一个 DEFAULT_ADDRESS」。
- **总览/就绪检查**：`readiness_service.py:180-198` 的 `protocol_listen` 检查改为读账号绑定的实时状态，
  文案指向账号卡片，不再指向 `/bots` 的泛泛「查看账号」。

### 3.7 安全与脱敏

- `control_token` 与 `access_token` 同策略：配置接口回显真实值（现有约定），页面/HTML 渲染与日志打码。
- 控制面仍只允许本机（`127.0.0.1`/`localhost`），与今天一致；托管模式天然本机。
- 套装导出：绑定里的 `control_token` 按密钥处理（剥离）；导入时标「需重填」。
- 安装包 sha256 校验、staging 原子替换、回滚上一版：沿用现有实现，不降级。

---

## 4. 用户路径（目标）

**托管路径（小白）**
新建账号 → 选「由本程序托管」→ 选 NapCat + pin 版本 → 保存（自动分配数据面 3001/`/onebot/v11/ws` 与控制面 6099）
→ [安装] → [启动] → [扫码] → 自动回填并连上。**全程不离开账号卡片，不打开 NapCat 面板。**

**外部路径（老手）**
新建账号 → 保持「使用外部协议端」→ 填 `ws_url`/token/方向 → 连接。与今天完全一致。

**升级路径**
旧库无 `account_protocol` 行 → 全部按 external 处理；不自动安装、不自动启动、不改默认地址以外的行为。

---

## 5. 分阶段落地（每步独立提交、可回滚）

| 阶段 | 内容 | 验证 | 回滚 |
|---|---|---|---|
| **O0 止血（无 schema）** | ①默认地址收敛到 `defaults.py`（删 4001/3001 字面量）；②`SetupAccountService` 的多账号特例改为「已有账号 → 跳转账号管理」；③协议端页加横幅「仅管理托管实例，连接配置在账号管理」 | 相关 pytest + `npm run typecheck` + 手动走一遍向导 | 纯 UI/常量改动，revert 即回 |
| **O1 数据** | 建 `account_protocol` 表（空=external）；`ProtocolBindingService`；账号 API 支持 protocol 子对象（只读+保存）；脱敏 | `pytest tests/application -q -k protocol` + `tests/webui -q -k bots` | 表空即旧行为；删表/停用服务可回 |
| **O2 运行时** | installer 目录调整；supervisor key 改 `account_id`；control client 按账号；实例目录与端口；删除全局 6099 | 单测假进程 + 手动双账号；`pytest -q` 全量 | 旧全局 API 保留一版并行 |
| **O3 UI** | 账号卡片协议端区；删 `/protocol` 路由；向导复用；`dist` 重建提交 | typecheck/build + 手动 3 条路径 | 路由重定向随时可恢复 |
| **O4 清理** | 删旧 API/`SetupAccountService`/硬编码；文档（README 协议端段与重复段）；测试收敛 | `grep` 断言 + 全量 `pytest -q` + `ruff check .` | 逐步小提交 |

> 每一步按 `AGENTS.md` 跑对应档位测试；O2/O4 属大工程收尾，必须全量绿 + ruff 无新增。

---

## 6. 验收标准

1. **单写入口**：除版本清单外，协议端地址/版本/启动参数只能经 `PUT /api/bots/{id}/protocol` 与账号保存接口写入
   （代码评审 + `grep` 断言）。
2. **单默认源**：`grep -rn "4001\|6099\|3001" app/` 只命中 `defaults.py` 与测试；
   向导、账号页、API、登录服务无字面量。
3. **双账号隔离**：A=3001/6099、B=3002/6098，可同时运行，日志/实例目录互不覆盖。
4. **external 兼容**：不写绑定行的账号，行为与今天一致（bots 表结构不变；升级后无进程被拉起）。
5. **生命周期**：删除账号 → 进程停止、实例目录清理；框架退出 → `stop_all()` 无孤儿进程；崩溃拉起按账号限次。
6. **单入口 UI**：侧栏不再有「协议端」；从新建账号到连上全程只在 `/bots` 完成。
7. **测试**：更新 `tests/application/test_setup_account.py`（唯一账号语义删除）、
   `tests/webui/test_protocol_api.py`（账号作用域）、`tests/webui/test_webui_bots_config_save.py`；
   新增 `tests/application/test_protocol_binding.py`、`test_protocol_runtime.py`；全量 `pytest -q` 绿。

---

## 7. 风险与未决问题

| # | 问题 | 影响 | 建议 |
|---|---|---|---|
| 1 | **单账号还是多账号**（未拍板） | 决定向导是否保留「唯一账号」、账号管理是否允许多个 | 数据模型按多账号设计；向导只负责「第一个」，已有账号时跳转账号管理；**不**在服务层硬拒绝 |
| 2 | NapCat 多实例是否支持独立配置目录/端口参数 | managed 多账号能否成立 | 先实测；不支持则 v1 明确「托管仅单实例，其余账号走 external」 |
| 3 | Lagrange 无 6099 类 HTTP 扫码接口 | 控制面能力不一致 | 用 `capabilities(kind)` 声明；v1 扫码只对支持的实现开放，其余走 external |
| 4 | 共享安装缓存被升级时正在运行 | 升级/回滚影响多账号 | 升级前强制停用该版本的所有账号，拒绝静默替换 |
| 5 | 端口占用（3001/6099 被别的程序占） | 新建账号失败 | 分配时探测并给可读错误；允许手动改，保存时校验 |
| 6 | Windows 下结束子进程树 | 孤儿 NapCat | 现有 `stop_all()` + terminate→kill；补充进程组/`taskkill /T` 实测项 |

### 已知的文档瑕疵（顺手记录，不在本设计范围内）
- `README.md`「### 协议端托管（可选）」整段重复出现两次。
- `docs/plan/onboarding-bundle.md` P5-2 写了「与账号绑定的生命周期」，与本设计一致，但实现未落地，应在 O2 时回填。

---

## 8. 影响面清单（供评审/排期）

**后端**
- `app/application/protocol/{manifest,installer,supervisor,login}.py`
- `app/application/setup_account.py`（计划删除）
- `app/infrastructure/config/account_store.py`、`ws_url.py`
- `app/infrastructure/onebot/{endpoint,gateway,reverse,reverse_link}.py`
- `app/webui/api/{protocol,setup,bots}.py`、`app/bootstrap.py`、`app/application/readiness_service.py`
- `app/infrastructure/persistence/database.py`（新表）、迁移脚本

**前端**
- `views/{ProtocolPage,SetupPage,BotsPage}.vue`、`stores/protocol.ts`、`router/index.ts`、`layouts/DefaultLayout.vue`
- `dist/` 重建（改了 `src/` 必须一起提交）

**测试**
- `tests/application/test_protocol_{installer,login,supervisor}.py`、`test_setup_account.py`
- `tests/webui/test_protocol_api.py`、`test_setup_api.py`、`test_webui_bots_config_save.py`
- 新增 `test_protocol_binding.py`、`test_protocol_runtime.py`

---

## 9. 需要你拍板的两件事

1. **账号数量**：单账号（向导即唯一入口，账号管理也只允许一个）还是多账号（向导只负责第一个）？
   本文默认按**多账号模型**写，向导只维护首个。
2. **协议端页的处置**：本设计取「并入账号卡片、删独立页」。
   另一条可接受路线是「保留独立页，但只做全局托管实例的运维，不再写任何账号配置」——
   若你倾向这条，§3.5/§3.6 需要改写为「账号引用共享托管实例」。
