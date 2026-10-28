# 优化轮次计划（对标 AstrBot / MaiBot / LangBot）

> 本轮目标：把 QQBotNext 往「轻量化、易配置、多账号、后端稳定、界面说人话」推进。
> 方法：先做三份**只读**审计（后端稳定性、前端 UX/文案、同类开源项目对标），
> 再按「小问题直接改、大问题先列计划」执行；完成标准是多个独立子 agent 复核无误。

## 0. 审计结论（摘要）

| 维度 | 结论 |
|---|---|
| 连接层 | 已有指数退避、僵尸连接扫描、按 account_id 对账、有界事件队列、readiness 同源；但事件队列满会阻塞接收循环、登录信息单次失败会「半上线」、WebUI 广播会被慢客户端卡死 |
| 配置持久化 | SQLite 事务化、内存缓存、变更广播齐全；但账号配置是「读改写全量覆盖」无并发锁（并发加号会丢号）、统计落库绕过 DB 锁会打断事务 |
| 插件 | 有清单/权限/依赖声明/热重载；但钩子按 account_id 注册、按 QQ 号匹配（永不触发的静默 bug）、多账号重复 import、安装时同步解压阻塞事件循环 |
| 前端 | 卡片化结构、主题、实时日志、就绪卡、保存状态机都在；但大量开发者文案、账号切换后部分页面不刷新、覆盖操作无确认且成功提示会撒谎、失败伪装成空、WS 无退避 |
| 易用性 | 首启向导 4 步齐；但协议端方向选择缺对照表、第二步要手填 base_url/模型、连上后「为什么不回话」无排障入口 |

## 1. 后端 P0（卡死 / 丢数据 / 静默失效）

- [x] 事件队列满时不再 `await put` 阻塞接收循环（丢最旧 + 计数告警），API 响应不再饥饿
- [x] 登录信息拉取失败改退避重试，避免「显示已连接但定时任务/主动消息不工作」
- [x] 迟到/未匹配 echo 不再被当成 unknown 事件派发进模块链
- [x] 反向账号主动断开不再先广播 error（事件序列 connected → disconnected）
- [x] 监督循环异常不再拖垮自动重连；多账号重连限并发
- [x] 非 ASCII `WEBUI_TOKEN` 不再让全站 500
- [x] 配置写路径加锁，并发加号不再互相覆盖
- [x] 统计落库走 Database 锁，不再提前 commit / 打断配置事务
- [x] 关停顺序：先 await 任务取消，再关统计与数据库，避免「数据库已关闭」
- [x] WebUI 广播改为队列/超时，慢客户端不再挂住网关状态机
- [x] 模块卸载/热重载 await 后台任务退出
- [x] 日志 WS 的同步文件读移到线程，端点统一走连接管理器发送

## 2. 后端 P1（插件生态与可观测性）

- [x] 钩子注册/匹配统一按 account_id（`@send_hook` 等不再静默失效）
- [x] 同一模块多账号复用已 import 的模块对象，仅在热重载时 purge
- [x] 插件安装/校验的 zip 解压移到线程，避免 ping 超时踢下线
- [x] 插件依赖：安装预检返回缺失依赖，提供需显式确认的「安装依赖」端点（pip 子进程、超时、输出回传）
- [x] 诊断台：按账号保留最近 N 条 OneBot 原始事件 + 发送测试消息（「连上了但 @ 没反应」可自助排查）
- [x] 缓存清理从「每次 set 全表扫」改为节流清理
- [x] 日志句柄泄漏修复；启动缓存仅在依赖检查通过时写入
- [x] 反向监听存活判断改用 `is_serving()`（websockets 16 无 closed 属性）；修复跨事件循环 stop 的未 await 告警
- [x] LLM 回合任务接入 async 关停链（pipeline/AgentRuntime/AgentManager 全 async，关库前 await 结束）
- [x] 插件依赖安装前端闭环（功能插件页「检查依赖 / 安装依赖」）
- [x] `cron_service` 暴露 
ext_run`（wall-clock epoch 秒，list_jobs 返回）+ 墙钟跳变时丢弃 cron 缓存重算
- [x] 向导后台连接循环改指数退避（2→60s），连上/监听就绪/账号删除即退出

## 3. 前端 P0（状态一致性 / 稳定性）

- [x] 切账号后各页面数据失效重拉（modules/policy/bundles/responder 均按 account 维度 + 请求序号）
- [x] 覆盖式操作（应用模板）先确认；保存失败不再提示「已应用」
- [x] 加载失败不再伪装成空（Bundles/Bots/Sessions/Tools 页均有错误态 + 重试）
- [x] WS 指数退避重连 + 全局断线提示条（socket_status 事件）
- [x] 通知队列化，后一条不再被前一条定时器提前关掉
- [x] 401 跳登录前先 flush 未保存草稿；BotsPage 卸载前落盘
- [x] ModuleConfigPage 自动保存卸载前 flush；iframe ResizeObserver 清理
- [x] 日志控制台按设置行数渲染，并清洗技术前缀
- [x] 全局 Vue errorHandler / unhandledrejection 兜底，不白屏

## 4. 前端 UI / 文案 / 易用性

- [x] 侧边栏按 运行 / 能力 / 数据 / 系统 分组；移除构建戳等开发者信息
- [x] 术语统一：模型服务、功能插件、配置包、群聊权限、AI 对话设置、高级工具
- [x] 账号管理：去掉 Owner ID、统一「连接/断开/重连」、协议端怎么填指引、复制地址、批量连接/断开、未保存提示
- [x] 首次配置：协议端方向对照表、模型服务卡片化（选厂商自动带 base_url/模型）、文案去「入池/模型池」
- [x] 总览：就绪卡去重、日志去技术前缀、诊断入口（事件监视 + 测试消息）、常用功能卡清理
- [x] 设置页：日志级别中文化、关于页去技术栈信息、构建戳移到关于
- [x] 工具页/模型服务页/配置包子页/高级规则对话框等开发者文案清理
- [x] 浅色主题次要文字颜色修正（白字白底）
- [x] 可访问性：可点击 div 补 role/tabindex/键盘事件
- [x] 三个 Tool 页 `<script setup>` 前的游离函数删除（SFC 解析器忽略、tsc 不报错的隐性 bug）

## 5. 自动化闸门（防回归）

- [x] `tests/architecture/test_vue_sfc_structure.py`：SFC 块外不得有游离代码、单 script setup、Tool 页 toggleTool 唯一
- [x] `tests/architecture/test_dashboard_copy.py`：.vue 模板文本 + 用户可见属性 + sections.json 的文案检查
- [x] `tests/infrastructure/*`：队列背压、登录重试、配置并发、事务隔离、慢 WS 客户端、关停顺序
- [x] Python 侧文案闸门沿用 `tests/architecture/test_ui_copy.py`

## 6. 验证记录

- 全量 `pytest -q`：**3243 passed, 18 skipped**（整改前基线：3090 passed / 10 failed）
- 前端 `pnpm typecheck` / `pnpm build`：均通过（dist 已重建入库）
- 实机截图核对：总览 / 账号管理 / 功能插件 / 模型服务 / 设置 / 首次配置（含浅色主题）
- 诊断台 API 实测：`GET /api/bots/{id}/events` 返回契约 JSON；未连接时发测试消息返回可读 400

## 7. 独立复核（3 个只读子 agent）与修复

复核结论：没有发现"崩溃/丢数据/卡死/安全绕过"的 P0；但发现 1 个前端 P0（ModuleConfigPage 把点击事件当账号 id 传入，导致"已保存"但写错作用域）与若干 P1，已全部修复：

| 类别 | 问题 | 修法 |
|---|---|---|
| 前端 P0 | 模块配置页 `@click="doSave"` 把 PointerEvent 当 account_id → 保存到错误作用域 | 模板改 `doSave()` + 参数类型守卫 |
| 前端 P1 | 诊断台 2s 轮询切页不停；安装成功对话框关不掉可重复装；ErrorBoundary 不随路由复位；顶栏"等待协议端接入"显示红色错误；BotsPage 改地址后直接点连接用旧配置、保存中丢 pending、刷新静默丢草稿；人格应用只写被忽略的 system_prompt；modules store 切号不重拉；agentConfig 无请求序号；SetupPage「稍后再配」仍建号 | 逐项修复并补静默轮询/确认/串行保存/loadSeq/账号 watch 等 |
| 后端 P1 | 反向"连接中点断开"竞态；反向 token 未做 URL 编解码（中文/含 & 连不上）；非 ASCII 会话 cookie 500；诊断事件在 worker 才记录（丢弃事件看不到）；关停 await 无超时；关停取消配置落盘任务；超时任务脱管；模块 reload 按 QQ 号查连接；登录限流 XFF 可伪造 | epoch 校验、unquote/quote、encode 比较、记录移到入队前 + drops、超时 + best-effort、flush_pending、保留登记、account_id 查连接、可信反代白名单 |
| 文档/文案 | 「0 表示」写法、框架级/后端/上游/落库 等残留、README 与实现不一致（旧版 UI 回退、就绪卡入口） | 统一改法并扩闸门：`test_ui_copy` 拦「0 表示」；`test_dashboard_copy` 黑话表新增 4 词；README 更正 |

复核后的验证：全量 `pytest -q`、`pnpm typecheck`、`pnpm build` 结果见最终提交；修复过程中新增/更新约 20 个测试文件。