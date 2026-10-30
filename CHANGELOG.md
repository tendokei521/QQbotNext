# Changelog

## [Unreleased]

### 前端整体优化：布局、文案与使用体验（2026-10-30）

针对管理后台全部 22 个路由做了一轮统一整改，实拍复核 1440 / 390 两档（`logs/review/final/`，
`metrics.json` 全部 `overflow=0`）。

- **统一壳层**：顶栏原先「连接/断开/重连」三个彩色按钮并排，改成一个随状态变化的主操作
  （连接 / 断开）+ 主题 + 「更多」菜单（重连、刷新状态、群与账号概览、退出登录）；
  状态 chip 窄屏只留图标（tooltip 给完整状态），账号选择不再被挤出屏幕。
  新增全局工具类 `.app-toolbar` / `.app-card-grid` / `.app-form-grid` / `.app-section-title`
  与 flex `gap-*`（Vuetify 3 本身没有）；`SaveStatusBar` 清态不再常驻「已保存 / 改动会自动保存」。
- **逐页**：总览统计卡紧凑横排（390 下 2×2）、账号状态与常用功能等高；账号管理与首次配置
  页头/工具栏重排、卡片主操作按状态只保留一个、协议端说明压成两行、下一步改主按钮；
  AI 面板状态改内容区小 chips、触发方式去掉孤立保存按钮；回复与权限搜索/计数/批量并入工具栏、
  长解释句删除；AI 设置页头只留返回 + 标题 + 保存状态、页内分组改顶部分段、字段去掉
  「卡片套卡片」；功能插件改自适应网格（奇数分组不再有大块空洞）；高级工具删除重复标题卡、
  工具行改为「名称 + 一行描述 + 行内详情」且不再显示裸 tool id；模型服务左栏显示域名、
  信息行单位统一；人格设定编辑区收缩、按钮分主次；配置包提示压成一行、应用记录状态中文化
  并修掉「名称时间粘连」；会话数据三栏等高撑满视口、删除「消息编号」等内部信息；
  数据统计筛选改固定四列网格；日志去掉逐行「普通」标签、工具栏控件合并为级别分段 + 搜索 +
  技术日志开关 + 导出 + 更多；设置页版本号改显示 `2.0.0`，不再暴露 commit。
- **构建**：新增 `__APP_VERSION__`（vite 从 package.json 注入）；`dist` 重新构建。

### 修复：应用配置包会把「插件开关全部打开」（2026-10-29）

- **根因（四个环节叠加）**：
  1. 导出时把「账号上没写过启停、走默认开」的模块固化成 `enabled: true` 写进包
     （`bundle/service.py` 用 `authority.get("enabled", True)`，`exporter.py` 同样默认 True）；
  2. 包清单归一化又把缺失的 `enabled` 兜底成 `True`（`bundle/manifest.py`）；
  3. 应用时同样默认 True（`bundle/applier.py`），于是给**每个模块**写一条显式
     `enabled=true` 的 account binding；
  4. 结果：应用一次配置包，账号上原本关掉的插件全部被打开（线上库可见 24 条 binding
     `enabled=1`、`updated_at` 完全相同，而应用前快照里它们是 `false`）。
- **修复**：导出/归一化/应用三处都改为「只有账号上**显式设过**启停才随包；包里没有该键
  就保持账号现状（binding 的 `enabled=NULL` = 继承账号默认值）」。包里显式写 `enabled`
  时仍按包执行。
- **顺带修两个同源问题**（`BotService.get_modules_data` / `POST /api/module/{name}/toggle`）：
  账号尚未装配模块实例时，原先回退到全局/别的账号实例，页面会把全局默认 `enabled=True`
  显示成「这个账号所有插件都开着」，点开关还会写到全局或别的账号；现在配置与启停统一按
  **请求的账号**解析，开关只写该账号的 binding，生命周期联动也只对属于该账号的实例执行。
- **数据修复**：从 `act_f168efc9ae23` 激活快照恢复账号 `123` 的 22 个插件开关（保持应用前
  的关闭状态）；已导出的 `data/bundles/account-123` 清单去掉固化的 `enabled`（重算校验和）。
  备份：`logs/review/app.db.bak-*`、`logs/review/account-123-1.0.0.zip.bak`。
- **排查结论**：仓库测试不会写线上库（`tests/conftest.py` 每个用例用 tmp SQLite）；
  这次写入来自一次「应用配置包」API 调用（激活记录 `started_at` 与 binding `updated_at`
  同为 20:15:44），不是 pytest 污染。

### 工程：测试政策改为「只测契约」+ 首批瘦身（2026-10-29）

- **规则**：`AGENTS.md` 硬性底线第 2 条从「新增/修改功能必须配套测试」改为「必须配套**契约测试**」——
  只测对外行为/契约/关键边界/曾经坏过的回归；私有实现、UI 文案全文、结构计数、mock 调用顺序
  不再强制。新增「删除 / 合并 / 重命名模块时，同一提交删掉对应测试」的豁免，重构不再被测试拖住。
  细则见 [`docs/TEST_POLICY.md`](docs/TEST_POLICY.md)，`STYLE.md §9` 同步改写。
- **工具**：新增只读审计脚本 `scripts/test_audit.py`（AST 分类 copy_literal / count_snapshot /
  private_api / mock_internal / plugin_coupled / no_assert，`--report` 出可读报告，`--path` 限定范围）。
- **首批瘦身**：3156 → **2871** 用例（-285，-9%），测试文件 317 → 312，行数 45,216 → 42,894，
  全量 122s → 105s。删除对象：私有 helper 单测、内部状态断言、结构/清单快照、UI 文案全文全等、
  重复覆盖；架构闸门里纯快照型（启动器缓存内部状态、接缝评级清单、键数计数）一并删除/降级，
  保留可执行规则（依赖方向、静默异常、插件布局、文案禁用词、Vue SFC 结构等）。
  分区：plugins 622→535、architecture+eval+scripts 194→153、agent 1410→1321、platform 907→849。
- **复核**：插件区首轮误删了 7 个文件的行为测试（memory 置信度/矛盾检测、模块配置链式视图、
  调度器触发、provider 能力分发），Lead 复核后恢复并保留（52 例重新纳入）。教训写进流程：
  整文件删除必须证明"该文件全部是内部实现"，否则只删用例。
- **清理死引用**：bilibili_parser / douyin_parser / emoji_reply / group_log / log_sender 已迁为
  本地插件（`data/module/plugins/`，不进仓库），对应 13 个测试文件按 TEST_POLICY §4 同批删除；
  全量从「13 个收集错误 + 2 失败」回到 **2853 passed / 18 skipped**（107s）。


### 变更：系统提示词方案改为「必选三选一」（2026-10-29）

- **后端**：`prompt_preset` 的口径从「可留空 = 沿用旧文案」改为三选一（聊天 / 角色扮演 /
  助手）。配置里带该键但值为空/拼错时，`assembly.presets.resolve()` 统一回落默认 `chat`；
  新账号默认 `chat`、存量空值由启动迁移改写、保存路径按 schema 枚举校验拦截非法值。
  只有完全不携带该键的库层调用（测试/脚本）保留旧装配结果。
- **前端（AI 对话面板 → 人格卡）**：原先藏在「高级：提示词风格」折叠面板里的三张可取消卡片，
  改为常驻的**必选单选控件**（聊天 / 角色扮演 / 助手，带说明与选中态，再点已选项不会清空），
  标题行固定为「提示词风格 · 必选，三选一」，不再出现「沿用内置」这种第四种状态；
  选项文案与说明固定，点击只切换选中态（修掉「点击前后文字会变」的困惑）。
- 面板内「编辑这三套文案」可直接打开设置页的编辑弹窗（`/settings?prompt-presets=1`）；
  弹窗修复两处：父组件从外部打开时不加载内容、scrollable 下 tab 栏被压成一条线。


### 易用性：前端「直觉性 / 配置重复 / 功能分散」专项整改（2026-10-29）

先由 5 个子 agent 分区只读审查（AI 面板 / 账号与向导 / 插件与工具 / 数据与设置 / 文案与术语），
在真实构建产物上实拍 1440 与 390 两档，共记录 62 条问题；再分域并行修复，最后用 3 个独立
子 agent 交叉审查「新手可用性 / 配置重复 / 信息架构」，修掉复查发现的新问题。完整清单与
验收记录见 [`docs/plan/ui-intuition-round.md`](docs/plan/ui-intuition-round.md)。

**口径（每个配置只有一个编辑入口）**

- 模型：连接/Key/模型实例只在「模型服务」；按账号的顺序池只在「AI 对话面板」；全局设置的
  「备用模型 ID」手填入口删除（写同一件事、却要求抄内部 ID 且多数情况不生效）。
- 人格：「应用人格」只在面板；「人格设定」页改为编辑预设 + 跳转面板选用，删除第二条写路径。
- 工具日志：4 个开关只留在「AI 设置 → 高级功能 → 工具日志」；高级工具页改为指路卡。
- 插件开关：只在功能插件列表；插件配置页改只读状态 + 返回列表。
- 「谁能回复」：账号权限/名册只在「回复与权限」；插件配置页的重复权限面板改为链接卡。
- 账号连接：/bots 下隐藏顶栏连接组；连接/断开/重连只在账号卡；顶栏只留账号切换与状态。
- 删除失效入口：面板「配置模板」（后端 `/api/config-profiles` 已下线，点了必 404）；
  插件页「工具权限覆盖」（dynamic 数据源未实现，永远报错，改由 OneBot 工具页逐工具设置）。

**直觉性**

- 新增「协议端」白话说明组件，首启向导默认可见、账号页复用、顶栏状态 chip 带 tooltip。
- AI 面板：布尔开关统一行式布局；池空时若配置了全局默认模型则显示「使用全局默认模型」而不是
  报「还没选模型」；状态条指路改成「在下方加入模型」。
- 就绪卡主按钮改为跟随第一条阻塞项（此前会跳过“账号没连上”直接让你配模型）。
- 会话页：空态给下一步、副标题解释「会话 / 对话」、双列表搜索、对话行内导出、`?session=` 深链。
- 统计页：补页头与统计口径（按本机时区）、KPI 改「当前范围」、补齐模型/会话/结局筛选、按会话可跳转。
- 日志页：级别快捷筛选（全部/错误/警告）、技术日志默认收起且 warning/error 永不隐藏、
  清空改为确认弹窗（单入口、可先导出）、日志设置与导出收敛到日志页。
- 插件/工具页：工具中文名做主标题、原名降为副标题、描述两行截断、参数收进详情；
  OneBot 工具页改读插件配置（此前读错数据源，永远显示“全部关闭”且点不动），并删掉
  退化成 73 个「设置项」的整份配置表单。
- 危险操作统一 `ConfirmDialog` + 影响清单（删账号/删会话/删对话/删消息/删人格/删模型服务/
  卸载插件/清空事件/全部断开），不再混用 `window.confirm`。
- 术语统一：账号（不再 Agent/Bot）、功能插件（不再模块）、人格/人格预设、模型服务 vs 模型、
  上下文/聊天记录/会话、配置包；权限档位六档叫法全站一致。

**一致性**

- 新增 `SaveStatusBar`（已保存/未保存/保存中/失败 + 手动保存 + 自动保存说明），
  AI 面板、AI 设置、回复与权限、插件配置、账号页统一使用。
- 设置页补「系统信息」「备份与恢复」入口，去掉与顶栏重复的主题卡、与功能插件页重复的
  插件恢复入口，日志设置改为指路。
- 侧栏「高级工具」改为单入口（不再与页内 Tab 形成两套导航）；/ai/settings/* 旧路径统一兜底。

验证：`pnpm typecheck` / `pnpm build` 通过；`pytest tests/architecture` 167 passed；
全量 `pytest -q` → **3292 passed, 18 skipped**；18 个路由在 390 宽下 `scrollWidth == innerWidth`。

复查（第二轮 3 个独立子 agent）后追加修复：

- AI 面板「人格：未选择」与「当前人格：X」自相矛盾 → 统一用 `agent/persona.ts::resolvePersona()`
  （绑定 / 内容相同未绑定 / 未选择），状态条与人格卡共用；模型区 4 个重复 CTA 收敛为按状态一个。
- 390 宽下 AI 设置被页内导航挤成 96px、字段竖排 → `PageWithSections` 加 720px 断点，
  窄屏改顶部横滑 chips（实测内容列 138→358px，页面高度 -78%）。
- OneBot 工具配置双入口且逐工具开关写错存储（工具总览停用后在 OneBot 页仍显示开启）→
  逐工具开关改走 `/api/agent/tools/toggle`；插件页隐藏 OneBot 开关/名单/调试四个键并指路
  「高级工具 → OneBot 工具」；工具页重复的日志指路、「账号」旧页名、跳转落点一并修正。
- 批量允许/关闭、OneBot「恢复默认」补统一确认弹窗与影响清单；人格页重复「新建」、
  配置包页重复「导入」去掉；免打扰与群聊背景两组同名不同义的文案改名并互相指路。

### 新增：插件/预设依赖改为「联网一键安装」（2026-10-29）

不把依赖打进配置包（跨平台/ABI 组合不可控），改为在目标机联网安装，两个入口共用同一条 pip 通道：

- **插件安装**：上传/更新 zip 时可勾选「安装时自动安装依赖」，后端 `install_from_zip(auto_deps=True)`
  在**加载前**装上 `module.json` 声明的依赖（只装清单里、当前缺失的包），回执带 `deps`
  （已装/缺失/pip 输出）；失败不阻断安装，若模块因此加载失败会回滚并给出可复制命令。
  原有的模块页「检查依赖 / 安装依赖」手动通道保持不变（装完自动重载）。
- **配置包导入/应用**：预检里的 `BUNDLE_MODULE_DEP_MISSING` 从阻断导入改为可修复告警；
  导入对话框可勾选「自动安装缺失依赖」，随包模块安装时带 `auto_deps`，
  已满足版本被跳过的模块在应用阶段补一次依赖；应用回执新增 `modules_deps`。
- **缺依赖可见**：导入时把缺依赖的模块记进 `meta.json`，配置包卡片显示「缺依赖 N」，
  应用对话框列出缺依赖模块并默认勾选自动安装（旧包无此字段时按 0 处理）。
- 模块开发文档补充 `requirements` 清单规则与三个安装入口（见 `docs/MODULE_DEV.md` §1.2.1）。
- 安全边界不变：必须显式勾选才装包（不会因点开页面触发）；只装清单声明的包名
  （拒绝 `-e`/`-r`/URL）；pip 串行 + 超时 + 镜像白名单；pip 失败不会静默。

验证：`pytest tests/plugin/test_pip_deps.py tests/application/test_bundle_modules.py tests/webui/test_bundles_api.py`
→ 全绿（新增 8 个用例）。

### 重构：AI 对话设置拆分为「AI 对话面板 / 回复与权限 / AI 设置」

拆分方案见 [`docs/plan/ai-panel-split.md`](docs/plan/ai-panel-split.md)。原则：**面板管日常、
设置管参数、每个字段只有一个编辑入口**。

- **AI 对话面板（`/ai`）**：新增高频操作台——状态条（回复开关/模型/人格/加载错误）、回复总开关、
  群聊/私聊开关、触发方式（@ / 关键词）、模型池、人格、权限摘要，以及配置模板/搜索/复制配置/
  诊断台入口。替代原「AI 对话设置 → 概览」。
- **回复与权限（`/reply`）**：原「回复设置」+ 原「权限管理」归一成一页三个 Tab——群与私聊
  （逐群三态/批量/静音/高级规则）、谁能回复（响应权限档位、拥有者/管理者名册、高危工具审批）、
  高级规则（账号默认规则）。
- **AI 设置（`/ai/settings/*`）**：只保留 3 页深度参数——对话行为（移出触发设置）、会话管理、
  高级功能；原「基础配置」「权限管理」页移除。
- 路由兼容：`/agent*`、`/policy`、`/advanced*` 全部重定向到新位置，旧书签不白屏。
- 后端字段归属表新增两个「页面外工作台」（`PANEL_GROUPS` / `REPLY_GROUPS`），
  `CORE_KEYS` 仍为 57 键且每个键恰好一个编辑入口；新增生成物 `ai-panel.json`，
  `scripts/gen_sections.py` 同时校验两份清单。
- 删除：`AgentPage.vue`（概览）、`AgentBasicSection.vue`、`AgentPermissionSection.vue`；
  搜索配置对话框支持跳转到面板/回复与权限。

验证：全量 `pytest -q` → **3283 passed, 18 skipped**；`pnpm typecheck` / `pnpm build` 通过；
`/ai`、`/reply?tab=who`、`/ai/settings/behavior`、旧 `/agent/basic` 重定向均实机核对。

### 重大：对标 AstrBot / MaiBot / LangBot 的稳定性与易用性整改（2026-10-28）

先做三份只读审计（后端稳定性、前端 UX/文案、同类项目对标），再按「小问题直接改、大问题列计划」
执行。完整清单与验证记录见 [`docs/plan/optimization-round.md`](docs/plan/optimization-round.md)。

**后端稳定性（P0，防卡死/丢数据/静默失效）**

- 事件队列满时不再阻塞 WS 接收循环（丢最旧 + 汇总告警），API 响应不再因洪峰饿死。
- 登录信息拉取失败改退避重试，修掉「显示已连接但定时任务/主动消息不工作」的半上线状态。
- 迟到/未匹配 echo 不再被当作 unknown 事件派发进模块链；反向账号主动断开不再先广播 error。
- 监督循环异常不再拖垮自动重连；多账号重连限并发（Semaphore 3）。
- 配置写路径加可重入锁，并发加号/保存不再互相覆盖；统计落库改走 Database 受锁接口，
  不再提前 commit 打断配置事务；SQLite 补 `busy_timeout` / `synchronous=NORMAL`。
- 关停顺序固定为「网关 → Agent（await 回合任务）→ 残留任务 → 统计 → DB」，
  LLM 回合任务在关库前真正结束。
- WebUI 广播改每连接发送队列 + sender 任务，慢客户端 2s 超时断开，不再挂住状态机/保存接口。
- 非 ASCII `WEBUI_TOKEN` 不再让全站 500；插件安装的 zip 解压/校验移到线程，避免事件循环卡死踢掉全部连接。

**插件生态（P1）**

- 修复 `@send_hook` 等 5 类钩子「按 account_id 注册、按 QQ 号匹配」导致的**静默永不触发**。
- 同一模块多账号复用已 import 的模块对象（不再 N 个号 exec N 次），仅热重载/覆盖安装时 purge。
- 新增插件依赖闭环：预检返回缺失清单 → 需显式确认的 `POST /api/modules/{name}/install-deps`
  （pip 子进程、镜像可选、180s 超时、输出回传）；功能插件页提供「检查依赖 / 安装依赖」自助修复。
- 新增**账号诊断台**：每账号保留最近 100 条 OneBot 原始事件 + 「发一条测试消息」，
  让「连上了但 @ 没反应」可以在界面内定位（总览/账号管理均可打开）。
- 缓存清理由「每次 set 全表扫」改为节流清理；日志 handler 重建前 close；启动缓存只在依赖检查通过时写入。

**前端稳定性与账号一致性（P0）**

- 切账号后 policy/bundles/responder 等 store 按账号失效重拉并加请求序号，不再串号/旧响应覆盖。
- 覆盖式「应用配置模板」先弹确认；保存失败不再同时提示「已应用」。
- 加载失败不再伪装成空（Bundles/Bots/Sessions/Tools 均有错误态 + 重试）。
- WS 指数退避重连（1s→30s）+ 全局断线提示条；4401 跳登录；通知改队列不再互相截断。
- 401 跳登录前先 flush 未保存草稿；BotsPage/ModuleConfigPage 卸载与切号前落盘。
- 全局 `errorHandler` / `unhandledrejection` 兜底 + ErrorBoundary，页面崩溃不白屏。

**界面与文案（去开发者化）**

- 侧边栏按「运行 / 能力 / 数据 / 系统」分组；新术语：模型服务、功能插件、配置包、回复设置、
  AI 对话设置、高级工具；移除侧栏构建戳、Owner ID、裸字段名/内部 id/路径/枚举等开发者文案。
- 账号管理：连接/断开/重连术语统一、协议端怎么填指引 + 复制地址、批量连接/断开、未保存提示、
  诊断入口。
- 首次配置：协议端方向对照表、模型服务卡片化（选厂商自动带地址与默认模型，只需填 Key）、
  保存改为失焦/下一步触发。
- 总览：就绪卡去重、日志去技术前缀、诊断入口、常用功能卡清理；设置页日志级别中文化与关于页重写。
- 浅色主题次要文字颜色修正；可点击 div 补键盘可达；三个 Tool 页在 `<script setup>` 前误粘的
  游离函数删除。

**自动化闸门（防回归）**

- `tests/architecture/test_vue_sfc_structure.py`：SFC 块外游离代码、单 script setup、Tool 页 toggleTool 唯一。
- `tests/architecture/test_dashboard_copy.py`：`.vue` 模板文本 / 用户可见属性 / sections.json 的文案检查。
- 新增后端测试：队列背压、登录重试、echo 不入队、配置并发、事务隔离、慢 WS 客户端、关停顺序、
  钩子账号匹配、模块 import 复用、事件环形缓冲、诊断台 20 条契约、cron/向导退避等。

验证：全量 `pytest -q` → **3284 passed, 18 skipped**；`pnpm typecheck` + `pnpm build` 通过；
浅色/暗色实机截图核对总览/账号管理/功能插件/模型服务/设置/首次配置。

### 新增：预设中心「一键化」——来源、依赖预检、密钥复用、向导接入


目标是把"导入 → 应用 → 可用"压到两次点击，并让随包模块的来源/依赖/安全边界透明：

- **模块来源台账**：新表 `module_origins`（按模块名记最后来源）。预设装/升级模块时写台账、
  回滚卸载时清；`GET /api/modules` 合并 `origin`，模块页显示「来自预设「xxx」」徽标（可点回预设中心）。
- **一键导入并应用**：新增 `POST /api/bundles/import-apply`（file + account_id + secrets +
  accept_code + reuse_provider_preset_id）；导入对话框内联填密钥，预检通过后一次点击完成
  导入 + 应用。应用失败保留已登记工件（`imported=true`），补密钥后可在列表重试。
- **导出可选模块**：新增 `GET /api/bundles/export-options`（名称/版本/内置/体积/启停），
  导出接受 `modules=a,b`；预设卡片显示「含模块 N（随包 M）」。
- **预检模块清单**：报告新增 `modules[]`（随包/将安装/将升级/已满足/缺失 + 体积 + 缺失依赖），
  导入对话框逐条渲染。
- **依赖与信任前置**：解析模块 `module.json` 的 `requirements` 文件并比对当前环境，
  缺包直接 `BUNDLE_MODULE_DEP_MISSING`（附 `pip install` 命令，框架不自动安装）；
  随包模块与内置同名 → `BUNDLE_MODULE_BUILTIN_CONFLICT`；含模块代码的预设必须勾选
  「我信任此预设的模块代码」，否则后端 `BUNDLE_CODE_CONSENT_REQUIRED`。
- **密钥复用**：应用/一键导入可传 `reuse_provider_preset_id`，直接复用本机已连通的
  Provider 预设密钥（手填 > 复用 > 上次创建）。
- **应用后自检 + 卸载保留**：应用响应带 `health`（agent 引用是否落位、随包模块是否加载、
  加载错误原因）；卸载/回滚支持 `keep_modules=true`（只回滚配置，模块与来源都保留）。
- **首启向导接入**：向导第 3 步「选预设 / 导入预设包」：列出已导入预设、可直接导入 .zip、
  内联填密钥并一键应用（以预设里的模型连接为准）。

验证：`pytest tests/application/test_bundle_modules.py tests/webui/test_bundles_api.py
tests/webui/test_module_api.py -q` + `npm run build`；逐项 commit 见
[`docs/plan/onboarding-bundle.md#p7`](docs/plan/onboarding-bundle.md#p7-预设一键化2026-10-27-追加)。

### 新增：预设迁移补齐——随包模块 / 人格卡 / 模型池全量 / 密钥回写

此前预设只迁"参数"：第三方模块只声明依赖（换机后模块不会出现）、人格只带
`system_prompt`（工具/技能/预设对话丢失）、模型池只带第一个，且除
`providers[0].api_key` 之外的密钥只在预检里提示、应用时被静默丢弃。现在补齐：

- **模块代码随包**：导出把第三方（zip 安装）模块打成 `modules/<name>-<ver>.zip`
  （确定性 zip，`__pycache__`/venv 等噪声不进包），内置模块只声明版本（随框架走）；
  应用时先走既有 zip 安装通道安装/升级模块，再写账号配置；回滚只卸载"本次新装"的
  模块（升级的保留，避免删掉用户原有安装）；模块安装失败则整套不写、留 `failed` 记录。
- **人格卡完整迁移**：`description` / `begin_dialogs` / `tools` / `skills` 随包，
  应用时重建角色预设；工具/技能按名引用，模块缺失由预检给出 warning。
- **模型池全量**：连接预设的其余配置 + 池内全部模型（模型名/类型/启用/参数）都进包，
  应用时逐个建模型并入池，主模型取第一个；升级仍复用未重填的密钥。
- **密钥按声明回写**：`providers[0].*` / `provider_models[i].*` / `agent.*` /
  `module:<name>.*` 四类键路径都写回对应对象；必填密钥在写库前校验，缺失直接
  `BUNDLE_SECRET_MISSING`（不再"装完了才发现没 key"）。
- 预检报告新增 `modules_bundled` / `modules_to_install`，前端显示「随包模块 / 将安装」；
  修正预设中心与导入对话框里"应用到账号在后续版本提供"的过期文案。

验证：`tests/application/test_bundle_modules.py`（打包/预检/应用/回滚/密钥）+
既有预设用例，见 `docs/plan/onboarding-bundle.md#p6-预设迁移补齐2026-10-27-追加`。

### 变更：插件运行时目录 `module/` 取消，统一到 `data/module/`

仓库根的 `module/` 是「只剩运行时数据、却还叫 module」的历史遗留：它与 `data/`
（SQLite / LLM 数据）并列成两套运行时目录，找数据要看两处，且和 `app/plugin/`（框架）、
`app/plugins/`（业务插件）三个 "module" 概念持续撞车。现在彻底去掉：

- `Settings.module_dir` 默认值 → `data/module`（`.env` 的 `MODULE_DIR` 同步为 `data/module`）；
- 新增 `app/infrastructure/fs/migrate.py`：启动时幂等把旧 `<root>/module/` 整棵树搬进
  `data/module/`（**只移动不改写**；目标已有同名文件时保留目标并日志点名冲突，不覆盖用户数据）；
- 旧 JSON 配置迁移同时接受 `data/module/configs/`（新）与 `module/configs/`（老）；
- `pyproject.toml` 去掉 `module*` 打包/覆盖率/ruff 路径，`.gitignore` 改为 `data/module/`；
- `module/uninstalled_modules.json` 是**机器本地状态**，此前被误提交入库（`af3ed7e`），
  现已 untrack 并忽略。

升级说明：直接启动即可，无需手工搬文件；若你的 shell 里手工设过环境变量 `MODULE_DIR=module`，
请删掉它（环境变量优先于 `.env`，否则数据会继续写进老目录）。

### 变更：账号「存在」即可编辑配置，不再要求连接成功

历史前提是「`bot_id` 只有登录后才知道」，于是运行时与模块实例都挂在登录回调上，连带一堆
界面在未连接时不可用：Agent 面板整页 404「无 Agent 运行时」、人格「应用」永久灰、模块配置
静默回退到全局实例。v4 之后 `account_id` 在「添加账号」时就生成且不可变，这套限制已经没有
依据，本次全部拆掉：

- **装配时机前移**：启动、建号时即为每个账号装配 `AgentRuntime` 与模块实例（`bot=None`），
  登录只负责绑定连接；删号会回收运行时与模块实例（此前会留下幽灵运行时）。
- **接口判据改变**：`/agent/config`、`/agent/telemetry*`、`/api/module/agent/*` 只要求
  账号存在；不存在才 404，且消息点名账号 id。
- **前端**：Agent 面板判据从「选中并连接」改为「已添加账号」，未连接时顶部给离线提示；
  `agentConfig.load()` 失败不再弹红条（改页内提示 + 重试）；人格设定页明确「可离线编辑，
  『应用到 Agent』需要账号」；模块页在回退到全局实例时给警告条。

### 新增：`on_online` / `on_offline` 生命周期，离线一律不跑任务

装配与"可以跑"解耦：

- `AgentRuntime.online` 标志（登录置 True，断开/出错置 False）；
- `BaseModule` 新增 `on_online()` / `on_offline()`（默认空实现），由框架在连接状态变化时
  逐个派发；`ModuleRegistry` 同时补/撤模块级定时任务注册；
- 离线装配期间**不武装任何计时器**：`TaskScheduler` / `ProactiveManager` 的 `_restore()`
  从构造挪到 `start()`（由 `on_online` 调用），`stop()` 由 `on_offline` 调用；
  `register_daily_schedule()` 离线时直接返回 False，上线后补注册；
- 顺带修掉断线不断任务的老问题：此前断开只广播状态，定时任务仍对着死连接发消息。

### 新增：首次启动生成**完整** `.env`

此前只有 `WEBUI_TOKEN` 会被自动写进 `.env`（文件不存在时就只写这一行），其余键全走
`Settings` 默认值——用户想调端口/路径得自己去 `.env.example` 对照抄，容易抄漏或抄错。

现在 `.env` 不存在时，启动会按 `app/core/env_template.py` 渲染一份**带注释的完整配置**
（所有被读取的键 + 当前生效值 + 随机令牌）落盘。`.env` 已存在时行为不变：**只补空令牌**，
其余键、注释、换行风格一字不动。

配套改动：

- 键清单收敛到 `app/core/env_template.py` 一处，`.env.example` 由它渲染
  （`python -m app.core.env_template`）；`tests/core/test_env_template.py` 双向守门
  "字段 ↔ 键"严格一一对应，不会再有登记了却没人读的摆设；
- 删掉两个**从未被任何代码读取**的配置项 `DEBUG` / `SCHEDULER_INTERVAL`
  （日志级别写死在 `app/core/logger.py`，定时任务按 job 自己的时间点 sleep）；
- `ensure_env_file()` 取代启动路径上的 `ensure_webui_token()`（后者保留给"只补令牌"场景）；
- 补齐文档：`README` / `MAINTENANCE` 的变量表补上 `WEBUI_AUTO_OPEN` / `LOG_DIR` /
  `MODULE_DIR` / `PLUGINS_DIR`，并说明 `.env` 不会进入 `os.environ`
  （`QQBOT_LLM_DATA_DIR` 这类只能作为 shell 环境变量）。

### 变更：单一服务模式改为「声明制」能力

此前「单一服务模式」是全局开关：任意模块都能在 WebUI 上勾选，但绝大多数勾了也没用——
纯 LLM 策略插件（`subscribe = ()`）根本走不到权限节点，开关只是误导；而未声明期间若库
里残留 `true`，反而会让模块在同群多账号的群下被静默跳过。现在改成**声明制**：

- 模块声明 `supports_single_service = True` 才有这个功能（默认 False）；
- WebUI 只在声明模块上显示开关；`POST /webui/single-service` 拒收未声明模块并在
  `rejected` 里回报；
- 运行期 `is_single_service_skipped()` 先查声明，未声明者配置残留也不生效。

顺带修掉一个静默缺陷：框架级 Agent 的单一服务此前永远不生效——UI 存的是 `"agent"`，
而 `_AgentGate.module_name` 是遗留值 `llm_chat_v2`，两边对不上。现在用
`single_service_key = "agent"` 显式钉住。

本地插件里 11 个「按群响应」的模块已声明（`bilibili_parser` / `douyin_parser` /
`df_password` / `emoji_reply` / `group_log` / `group_request` / `log_sender` /
`poke_reply` / `recall_back` / `time_sign_in` / `what_to_eat`，另加框架级 `agent`）；
纯 LLM 策略模块与 `example` 骨架不声明。

### 新增：账号可正反向连接 OneBot（正向 / 反向 WebSocket 都支持）

此前账号只能填一个 ws 地址，框架只能**连出去**——OneBot 实现（NapCat 等）与框架不在
同一台机器、或只允许出站连接的部署里就用不了。现在每个账号可选连接方式：

- **正向 WebSocket**（默认，存量账号行为不变）：框架连出到 OneBot 实现的 ws 地址；
- **反向 WebSocket**：框架在配置的地址上监听，OneBot 实现连进来。路径与 `access_token`
  双向校验（`Authorization: Bearer` 或 URL 参数；不匹配关闭码 4401），接入后与正向走
  同一套消息循环、登录快照、监督重连。

配置层：`bots` 表新增 `conn_mode` 列（老库启动时自动补列，缺字段一律按正向处理）。
实现分层：`forward.py` / `reverse.py` 是两个方向的传输，`reverse_link.py` 管反向监听
生命周期，`introspect.py` 出账号对外视图（`gateway.py` 因此守在 800 行闸门内）。

### 破坏性变更：`app.llm` 改名为 `app.agent`，并按职责拆成 11 个子包

框架级 Agent 原先是一个 91 文件 / 32,900 行的平铺包（顶层 55 个文件挤在
`app/llm/` 下），改任何一个功能都要在 50 多个同层文件里靠文件名猜。现在按职责分包：

```
app/agent/{loop,assembly,session,persona,context,tools,delivery,media,proactivity,
           providers,memory,knowledge,group_log}
```

- **`app.llm` 不再存在，也没有保留兼容 shim**：一个概念只能有一个名字，shim 会让
  两套导入路径长期并存并重新制造环。`tests/test_architecture_boundaries.py` 里
  `test_old_llm_package_is_gone` 守门。
- 插件侧唯一的破坏性写法变化：
  `from app.llm import tool` → **`from app.agent import tool`**（`skill` / `ToolContext` /
  `LlmContext` / `ToolCallContext` 同理）。其余插件 API（`app.modules.*`）未变。
- `handle` 不再从包级导出（它会让 `app.agent` 反向依赖整个主循环）；需要请直接
  `from app.agent.loop.chat import handle`。
- `app.llm.storage` 仍在，只是搬到 `app/agent/storage/`。

顺带的架构清理（详见 `docs/ARCHITECTURE.md §5.2`）：

- `app/agent/tools/onebot/manifest.py` 的 4,789 行工具字面量外置为
  `manifest.json`，加载器约 100 行；175 条工具由 `tests/test_onebot_manifest_data.py` 校验；
- `app/modules/api.py` 不再从 `app.bootstrap` 取容器，改用
  `app.core.container.get_active()`（全库唯一允许的服务定位）；
- `app/modules/media_cache.py` 下沉到 `app/infrastructure/fs/media_cache.py`；
- 工具注入策略从 `tools/system.py` 拆出到 `app/agent/tools/policy.py`。

实测：最大强连通块从 27 文件 / 13,547 行降到 **3 文件 / 3,355 行**，
`chat.py` 的可达闭包从 158 文件 / 41,717 行降到 **92 文件 / 25,342 行**
（`venv\Scripts\python.exe scripts\gen\arch_closure.py`）。

### 修复：token 消耗那一行不再把回退模型记成主模型名

`[Api] 本次请求消耗: ... | model=deepseek-chat | 流式` 此前固定取**回退链第一个配置**
（`chain[0]`）的名字，而真正服务请求的可能是链上的 backup：主模型 429/超时/空回复后回退，
这一行仍写主模型名，于是 backup 的 token 消耗被记在主模型头上（成本/排障都会读错）。

- **按实际服务者取名**：`chat_with_fallback` / `iter_stream_with_fallback` 记录真正回了
  响应（或已产出事件）的那个配置，`provider_model_id` 优先、否则 `model`；
- **流式把名字回传给调用方**：带 `usage_sink` 时写进保留键 `SERVED_MODEL_KEY`
  （`"_served_model"`，字符串非 token 数，`merge_usage` / `format_usage` 都当没看见），
  `chat.stream_response` / `initiative_stream` 打那一行时读它；
- **遥测同源**：流式 `_record_stream_telemetry` 也改用实际服务者，`/agent/telemetry`
  里的 model 不再与日志打架；
- 全部 provider 零产出（没有实际服务者）时退化为最后一个尝试过的模型名；
- 测试：`tests/test_llm_token_usage.py` 新增非流式/流式回退后写 backup 模型名的用例。

### 修复：服务端正常关闭（1000）不再变成僵尸连接（断线静默死亡）

线上案例（2026-09-27 23:48:26，远端 SnowLuma 日志与 app 日志逐秒对照）：SnowLuma 运行时重启，
三个 session 同时关闭，并以 **1000（正常关闭）** 断开所有客户端；远端 23:48:33/34 就恢复监听
（只停了约 7 秒），而 QQbotNext 一直僵死到人工重启 —— 00:00 打卡 18 次全失败、06:00 仍在报
`received 1000 (OK) normal; then sent 1000 (OK) normal`，那条在途合并转发则干等满 60s 超时。

- **根因**：`websockets` 的 `Connection.__aiter__` 会把「正常关闭」的 `ConnectionClosedOK`
  直接吞掉、让 `async for` **正常退出**（不抛异常），而 `_bot_server` 只处理了 `except` 分支 →
  不记日志、不清 `conn.websocket`、不改 `status`、不放行在途请求；状态永远停在 `connected`
  → 监督循环判定「在线」永不重连，之后每次发送都抛 `ConnectionClosed` 立即失败。
  异常断线（1006/1011 等）本来就会重连，**只有 1000 这一路是静默的**，所以表现为「莫名其妙」。
- **修法**：连接收尾统一为 `_handle_connection_end()`（留痕 + `status=error` + 广播 +
  清 ws + `fail_pending`），异常断线与正常关闭都必须走；正常关闭现在会打出
  `WebSocket 连接结束: 服务端关闭连接 (code=1000 …)`（WARNING，此前完全静默）。
- **加固**：监督循环新增 `_sweep_zombies()` —— `status=connected` 但 ws 已带 `close_code`、
  或接收任务已消失时按掉线处理，再走指数退避重连。
- **顺带**：未知事件（`未知类型 <uid>`）打出原始 payload。线上那条「未知类型 3437542570」
  实为 SnowLuma 重启前推的 session-closed 通知，此前只能靠时间猜。
- 测试：新增 `tests/test_gateway_silent_close.py`（真实 ws 服务端以 1000 关闭 → 必须转入掉线态、
  在途请求立刻失败、监督循环自动连回；僵尸判定单元用例）；`scripts/log_timeline.py` 的时间线
  现在也会标注 `WebSocket 连接结束`。

### 新增：LLM 请求结束后 info 一次本次 token 消耗

此前只有非流式路径在**每一轮**（含工具循环的中间轮）打一行"回复 N 字符 | 输入 X / 输出 Y"，
流式路径完全没有 token 记录。现在：

- **一次请求只落一行**：整轮请求（含工具多轮、空回复重试、provider 回退）完全结束后统一 info：
  `[Api] 本次请求消耗: 输入 1234（缓存命中 1000 / 未命中 234） / 输出 567 / 合计 1801 tokens | 回复 27 字符 | model=... | 流式`；
- **上游报什么就用什么**：DeepSeek `prompt_cache_hit_tokens`/`prompt_cache_miss_tokens`、
  OpenAI `prompt_tokens_details.cached_tokens`、Anthropic `cache_read_input_tokens`/
  `cache_creation_input_tokens`、Gemini `cachedContentTokenCount` 直接取用，不自己估算；
  上游一个字没报就不打这一行；
- **流式也能拿到 usage**：OpenAI 兼容流自动带 `stream_options.include_usage`
  （`extra_body.stream_options` 可覆盖；上游 400/422 不认该参数时自动去掉重发一次），
  Anthropic `message_start`/`message_delta`、Gemini `usageMetadata` 同样会解析；
  流式工具循环由调用方（`chat.stream_response` / `initiative_stream`）汇总多轮后只打一行；
- **顺带修正**：非流式 `chat()` 与 Anthropic/Gemini 的工具循环现在**累加**各轮 usage 再上报，
  遥测（`/agent/telemetry`）不再只记最后一轮；流式遥测也开始记录 input/output tokens；
- 测试：新增 `tests/test_llm_token_usage.py`（usage 归一 / 跨轮累加 / 只打一行 / 无 usage 不打）。

### 新增：表情回应词表改为 QQ 系统表情全量映射

此前 `set_msg_emoji_like` 的 `reaction` 只有十来个手写的常见表情，模型想表达别的只能猜数字
（贴错**不可撤回**）。现在：

- 新增生成物 `app/llm/qq_faces.py`：QQ 客户端下发的系统表情表（`face_config.sysface`，
  296 个名字 → `QSid`）。它是 OneBot `face` 段 id 与表情回应 `emoji_id` 的同一空间；
- 新增 `scripts/export_napcat_faces.py`：从本机 NapCat 包导出/核对这张表
  （`--write` 重新生成、`--check` 升级后比对），**不再手抄网上的列表**——
  流传最广的那份把「微笑」记成 1，QQ 实际是 `撇嘴=1、微笑=14、爱心=66、赞=76`；
- `emoji_lexicon`：`DEFAULT_TAGS` 扩到全量表 + 口语别名（点个赞/狗头/问号/无语/加油/
  强→赞/弱→踩/大兵→悠闲），修正了 `吃瓜 4→271`、`比心 307→319`、`问号 263→32`
  这类错 id；单字名（困/茶/哦…）只认精确匹配（"很困难"不再被当成贴「困」）；
  数字名字（客户端表里的「666」）与真 id 区分开；
- `LARGE_FACES` 标出 `QSid>=222` 或带 `AniStickerType` 的大表情/动态表情
  （捂脸/吃瓜/比心/打call…），这批 id 在表情回应上的形态仍待真机校准；
- `set_msg_emoji_like` 的提示词（工具说明与 `reaction` 参数）改为全量词表的用法；
- 测试：`tests/test_onebot_emoji_like.py` 增加全量映射、真实 id 抽查、别名桥接、
  单字名精确匹配、数字名字、大表情标记等用例。

### 新增：显示名映射（句子型昵称不再进上下文）+ 一次判定缓存

线上案例：群成员把昵称写成「老师，今年的学费也是一次性交吗」，`expand_image` 的工具结果
直接把这整句话当人名回灌给模型（`sender.card or nickname` 没有过脱敏）。修法不是逐个调用点
打补丁，而是把「用户 id → 显示名」收成一个出口：

- 新增 `app/llm/display_names.py`：确定性层（明显句子/明显名字，零调用）+ 灰区一次廉价判定
  （只要 0/1）+ verdict 缓存（按 `(qq, 名字)` 键控，**改名即重判**）+ 单 bot 每小时 30 次闸门
  （超限**静默脱敏**）+ 后台预热（渲染同步、模型调用不进请求路径）
- 接入工具结果与引用行：`context_tools`（expand_image / expand_message / expand_recent /
  expand_user / 转发节点）、`enhance` 引用行；**底层 OneBot 工具一行不改**
- 失败一律倒向脱敏（无 key / 超时 / 回包不可用 / 闸门超限）→ 只会更还原，不会更不安全
- 测试：`tests/test_display_names.py` 19 条 + `test_llm_context_tools.py` 7 条回归
  （含线上那条原样昵称的用例）

### 改名：NapCat 工具 → OneBot 工具（旧配置键仍生效）

「OneBot」是协议名，NapCat 只是本项目使用的协议端实现之一，因此把四处对外命名统一到协议名：

- 包路径 `app/llm/napcat/` → `app/llm/onebot_tools/`；
  符号 `NAP_CAT_TOOLS` / `build_napcat_tools` → `ONEBOT_TOOLS` / `build_onebot_tools`；
  工具来源标识 `spec.source` 由 `"napcat"` 改为 `"onebot"`
- 配置键 `napcat_tools_*` / `napcat_tool_overrides` → `onebot_tools_*` / `onebot_tool_overrides`；
  **旧键仍会被读取**（新键优先，只做内存适配、不改写用户数据）——否则升级后
  `onebot_tools_enable` 回落默认 `False`，等于把用户已打开的开关静默关掉（见
  `tests/test_llm_config_legacy_keys.py`）
- Dashboard：路由 `/tools/napcat` → `/tools/onebot`、页面 `OnebotToolsPage.vue`、
  tab id 与 localStorage 字段名同步；展示文案 "NapCat Tools" → "OneBot Tools"
- 上游 API 文档地址（`napcat.apifox.cn`）**保持不变**：那是三方协议端的真实文档站，改了就是死链

### 重构：会话历史「基础信息 + 轮次信息」，查看过的内容留在历史里

历史此前是「一条冻结文本」：`enhance` 把「时间/群号/发送者/发送了」拼成字符串整条落库，
渲染有两套实现（会话历史 / 在线背景），且只有在线背景接「已展开登记」——第 1 轮展开展开的
被引用消息，第 2 轮又变回 `[引用456]` 占位。

- 新增 `app/llm/history_model.py`：`BaseInfo`（时间/发送者/群号/正文/原始段/附注行）
  + `TurnInfo`（轮次内取回的内容）+ `HistoryEntry` + **唯一渲染器** `render_history_entry`；
  四条渲染路径（会话历史、群聊背景、私聊背景、工具回读）收敛到它
- 写侧结构化：历史条目存 `base` 与原始消息段，正文与「时间/群号/发送者」分离，
  由渲染器按当前配置重排；保留 `message_id` 作为补全锚点；旧数据仍按原样渲染
- 新增 `app/llm/history_enrich.py`：持久补全登记（`data/llm/history_enrich.json`）。
  工具取回内容 → 本轮记账 → 请求收尾落库 → 下一轮渲染时把
  `[引用456]` / `【未展开:合并转发…】` 升级为 `【已展开:… → 摘要】`；
  工具取回的单条消息作为**附加块**排在对应历史之后（不混进用户正文）
- 历史图片不再只有占位：渲染为可展开标记 `[图片#1003]`，新增 `expand_image` 工具按需取回，
  取回的图作为「补全材料」随下一轮直接传给视觉模型；讨论焦点那张仍与既有路径一样直接同传
- 新配置：`history_background_enable`（默认开；关掉后会话历史成为唯一历史来源）
- 提示词补充 `【已展开】` / `[图片#id]` 的语义说明

### 重构：LLM 消息组装收敛为「块表 + 一次装配」

同一类请求此前在四处各自组装 messages（`chat.generate_response`、`chat.stream_response`、
`scheduler._build_messages`、主动消息内联），块顺序与取值口径各写一遍。现在：

- 新增 `app/llm/assembly.py`：`PromptRequest`（一次请求的全部输入）+ 声明式块表
  `BLOCKS`（人设 / 定时协议 / 主动性 / 格式说明 / 技能 / 记忆 / 背景 / 历史 / 本轮）
  + `PromptAssembler`。**想改顺序只改块表**，想加块只加一个 `build_xxx(req)` 函数
- 取值收敛为唯一入口 `chat.prepare_prompt`：会话、历史去重与渲染、上下文压缩、
  工具与技能、记忆召回、指代块、图片解析都在此完成；`generate_response` 与
  `stream_response` 只差 provider 调用方式
- 图片归位（`place_images`）移到清洗之前，模态清洗恢复为**最后一层兜底**；
  文本模型仍是 `[图片]` 占位，声明 `image` 模态的模型直接收到图块
- 统一三处不一致：记忆检索与意图判定一律用**用户原始正文**；历史去重与
  "刚追加的 user 消息"同源比较；上下文压缩在四条路径上一致生效
- 主动消息与定时任务获得与普通回复相同的块结构（此前缺「消息格式说明」），
  焦点行从手工拼接收进 `referent` 块
- 删除不可达的旧回复路径 `call_llm_and_reply` / `handle_group` / `handle_private`
  （约 150 行）、重复的 `_message_meta_instruction`、死参数 `schedule_nudge`
  与常量 `RECENT_SCHEDULE_NUDGE`；`chat.py` 1562 → 1285 行
- 新增 `describe(req)`：输出 `[{block, role, chars}]`，不开 debug 也能看清本轮发了什么

### 稳定性修复

- 修复 OneBot API 超时一律 10s 导致的假失败：改为按 action 分级（合并转发 60s、
  `get_forward_msg`/`get_image`/`get_record` 30s、群列表与历史 20s），
  且超时后迟到响应改为留痕日志（服务端可能已执行），不再静默丢弃
- 新增空回复重试 `empty_reply_retries`（默认 1 次，0 = 关闭）：请求成功结束却
  既无文本也无工具调用时同 provider 重试，避免偶现空回复把「暂时无法回答」发给用户；
  已有文本/工具产出时绝不重试（防止重复发送与工具重复执行），流式、非流式与
  主动消息/定时任务共用同一开关

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

- 全量测试基线：`168 passed`（该数字为**当次发布时点**的快照，后续版本会增长，不代表当前基线；
  当前基线以 `pytest --collect-only -q` 实际输出为准）