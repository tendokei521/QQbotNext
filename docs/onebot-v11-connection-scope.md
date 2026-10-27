# 范围界定：本程序只做 OneBot v11 连接，不托管协议端

> **状态**：设计定稿（替代并删除 `docs/protocol-account-ownership-design.md` 的错误前提）。未动代码。
> **一句话**：QQ 协议端（NapCat / Lagrange / LLOneBot 等）是**用户的外部依赖**，安装、登录、启停、
> 日志、版本全部不归本项目；本项目与它只有一条交互——**一条 OneBot v11 WebSocket 连接**，
> 这条连接的配置**只**由「账号管理」负责。

---

## 1. 决策

1. 本项目**不管理 QQ 协议端**：不安装、不升级、不启停、不扫码、不转发登录态、不维护版本清单。
2. 本项目与协议端的唯一交互是 OneBot v11 WebSocket（正向：我们连出去；反向：我们监听、它连进来）
   以及 `access_token` 鉴权。除此之外不假设协议端是什么、装在哪、怎么起。
3. 这条连接的**唯一配置面是账号管理**。向导只是「第一个账号」的入口，不得有第二套默认值、唯一性
   特例或额外的连接循环。
4. 已有的 P5「协议端托管」属于越界功能，**整体移除**（清单见 §4）。这是范围收缩，不是重构。

## 2. 边界

| 本项目负责 | 本项目**不**负责 |
|---|---|
| OneBot v11 WS 客户端/服务端、事件与 action | 安装 / 升级 / 启动 / 停止 / 杀死协议端进程 |
| 账号连接配置：`ws_url` / `conn_mode` / `access_token` / `auto_connect` | 二维码获取、登录态轮询、自动回填账号 |
| 反向 WS 监听与「未监听」就绪提示 | 协议端版本清单、下载、sha256 校验、回滚 |
| 账号级连接状态、重连、启动自动连接 | 协议端日志、进程 PID、崩溃拉起 |
| 文档说明「自备一个 OneBot v11 实现」 | 代替协议端完成 QQ 登录 / 风控处理 |

**判断口径**：凡是需要知道「协议端内部怎么运行」的功能，都不属于本项目。

## 3. 保留的连接模型

- 账号字段保持现状：`id` / `name` / `ws_url` / `access_token` / `conn_mode` / `auto_connect` / `owner_id`
  （`app/infrastructure/persistence/database.py:128-141`），**不改表**。
- 默认地址只允许一处派生：`app/infrastructure/onebot/endpoint.py:147-178`（`suggest_address`）。
  向导里的 `4001` 另立默认（`app/application/setup_account.py:23`、`SetupPage.vue:28`）要删掉，
  统一走 `endpoint.py`。
- 写入口收敛为一个：账号管理的保存服务（`app/webui/api/bots.py:127-149`）。
  `SetupAccountService` 的「唯一账号 / 强制 auto_connect / 独立默认地址」都退化为对同一服务的调用。
- 就绪检查 `protocol_listen`（`app/application/readiness_service.py:180-198`）保留：它检查的是
  **我们自己的反向 WS 监听**，与协议端托管无关。

## 4. 移除清单（P5 托管层）

### 后端
- `app/application/protocol/`（`__init__.py` / `manifest.py` / `installer.py` / `supervisor.py` / `login.py`）——整目录删除。
- `app/webui/api/protocol.py`；`app/webui/app.py:35`（import）与 `:185`（`include_router`）。
- `app/bootstrap.py:148-159`（注册 installer/supervisor/login）与 `:564-574`（退出时 `stop_all()`）。
- `data/protocol/`：代码不再读写；**不主动删除**用户已有目录（留给用户自己的协议端）。

### 前端
- `app/webui/dashboard/src/views/ProtocolPage.vue`
- `app/webui/dashboard/src/stores/protocol.ts`
- `app/webui/dashboard/src/router/index.ts:41-46`（`/protocol` 路由）
- `app/webui/dashboard/src/layouts/DefaultLayout.vue:80`（侧栏「协议端」）
- `dist/` 重建后一起提交。

### 测试
- `tests/application/test_protocol_installer.py`、`test_protocol_login.py`、`test_protocol_supervisor.py`
- `tests/webui/test_protocol_api.py`

### 文档
- `README.md:215-222`「协议端托管（可选）」整段删除；前置条件表（`:66`）保留（用户自备协议端）。
- `docs/plan/onboarding-bundle.md:90-101,113`：P5 标记为「范围撤销」，保留历史 commit 记录。
- `docs/plan/README.md:31,76,132-135`：状态板同步为撤销。
- `docs/exe-packaging-design.md:35,43,98`：删掉「托管」相关表述，只保留「协议端外置、用户自备」。

## 5. 不变量与兼容

- **无数据库迁移**：`bots` / `bot_accounts` 表不动，历史账号照常连接。
- 移除的只是「替用户管协议端」的能力；用户自备 NapCat、填反向/正向地址的路径完全不变。
- 框架退出不再需要停止外部进程（`stop_all()` 随之删除），不存在孤儿进程问题。
- `data/protocol/` 中用户已放的清单/安装包保留在磁盘，但程序不再读取。

## 6. 验收标准

1. 侧栏无「协议端」，不存在 `/protocol` 路由。
2. `grep -rn "application\.protocol\|webui\.api\.protocol\|ProtocolSupervisor\|ProtocolInstaller\|ProtocolLoginService" app/ scripts/` 无命中。
3. 启动流程不再注册协议端服务；退出流程不再有子进程清理。
4. 账号管理：新增 / 保存 / 连接 / 断开 / 反向监听行为不变；向导只写账号连接配置。
5. 全量 `venv\Scripts\python.exe -m pytest -q` 绿 + `ruff check .` 无新增；前端 `npm run typecheck` 通过、`dist` 重建。
6. README / plan / 代码三方一致，不再出现「可托管协议端」表述。

## 7. 实施顺序（一提交一件事）

| 编号 | 提交 | 内容 |
|---|---|---|
| C1 | `docs(scope): narrow protocol scope to OneBot v11 connection` | 本文档；删除错误的设计稿 |
| C2 | `refactor(protocol)!: remove managed protocol-end backend` | 删 `application/protocol/`、API、bootstrap 注册、对应测试 |
| C3 | `refactor(webui)!: remove protocol panel from dashboard` | 删页面/路由/侧栏/store + 重建 `dist` |
| C4 | `docs: sync protocol-end references after scope reduction` | README / plan / exe-packaging |
| C5 | `fix(setup): reuse account defaults instead of a second 4001 default` | 默认地址统一到 `endpoint.py`；向导去重复语义 |

> C2/C3 是跨层删除，按 AGENTS.md「大工程收尾」档位：全量测试 + ruff + 前端构建。
> 若工作区有他人并行改动（当前确有账号相关未提交改动），先与在途改动对齐再执行 C2/C5。

## 8. 为什么不再讨论「账号托管协议端」

上一版设计稿曾提议「账号聚合 ProtocolBinding（种类/版本/控制面/实例目录）」，前提是
「协议端交互由本项目负责，只是配置要收进账号」。该前提与项目定位冲突：

- QQ 协议实现是**用户的选择**（NapCat / Lagrange / 其它），版本与运行方式不该由本项目锁定；
- 托管会引入进程管理、端口分配、多实例隔离、安全边界、上游版本漂移等长期维护成本；
- 项目已有正确路径：**用户自备 OneBot v11 实现 → 在账号管理填 WS 地址与 Token**。

因此本项目的设计空间里不再有「协议端托管」这一项。
