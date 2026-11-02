# Dashboard 前端架构（现状）

> 读者：改 `app/webui/dashboard/**` 的人。
> 只写现在的结构与约定；页面/路由以 `src/router/index.ts` 为准。

## 1. 技术栈与产物

- Vue 3（`<script setup lang="ts">`）+ Vuetify 3 + Pinia + vue-router（**hash 路由**，`/#/ai`）
- 构建：Vite（`npm run build`），产物 `app/webui/dashboard/dist` **进仓库**，克隆后无 Node 也能用
- 后端只做静态托管：`app/webui/app.py` 挂 `dist`；缺产物时返回 503 提示页
- 类型检查：`vue-tsc --noEmit`（包含在 `npm run build` 里）

## 2. 目录

```
src/
  main.ts / App.vue          入口
  router/index.ts            路由表 + 旧路径重定向（唯一的地址真相）
  layouts/DefaultLayout.vue  侧边栏 / 顶栏 / 账号切换 / 主题
  views/*.vue                每个路由一个页面（见 §3）
  components/{agent,bots,bundles,common,config,log,policy,readiness,settings,tools}/
                             按领域分组的复用组件
  stores/*.ts                Pinia：状态与后端交互（agentConfig / bots / modules / policy /
                             readiness / logs / tools / bundles / responder / promptPresets / theme / webui）
  agent/*.ts                 Agent 领域的纯函数（persona 解析、schema 辅助等）
  api/http.ts                唯一 HTTP 客户端（错误提示、鉴权）
  utils/                     schema 渲染、日志解析、文案常量
  styles/                    全局样式与 Vuetify 主题
```

## 3. 页面地图（与侧边栏一致）

| 路由 | 页面 | 职责 |
|---|---|---|
| `/overview` | OverviewPage | 运行状态、就绪检查、账号状态与诊断台入口 |
| `/bots` | BotsPage | 账号增删、连接方式、启停 |
| `/provider-presets` | ProviderPresetsPage | 连接预设 + 模型实例（含拉取模型/能力预勾选/测连接） |
| `/personas` | PersonasPage | 人格预设增删改、当前账号绑定 |
| `/ai` | AiPanelPage | 这个号怎么说话（回复开关、触发、模型池、人格、行为/会话/上下文分区） |
| `/ai/settings/:section` | AgentSectionPage | AI 面板的分区页（behavior / session / advanced…） |
| `/reply` | PolicyPage | 群/私聊范围、响应权限、名册、高危工具审批 |
| `/modules` | ModulesPage | 功能插件列表（启停、进入配置、卸载） |
| `/modules/:name` | ModuleConfigPage | 插件自己的配置表单（由模块 schema 渲染） |
| `/responder` `/responder/:name` | ResponderOverviewPage / ResponderModulePage | 插件响应策略：多插件争抢时的仲裁 |
| `/bundles` | BundlesPage | 配置包：导出 / 导入 / 回滚 |
| `/tools/{system,onebot,mcp,module}` | ToolsPage 等 | 四类工具的总览与逐工具开关 |
| `/sessions` | SessionsPage | 会话数据浏览、导出、删除 |
| `/stats` | StatsPage | 用量统计（token/请求/消息） |
| `/logs` | LogsPage | 日志查看（简洁/原始）与导出 |
| `/settings` | SettingsPage | 后台与安全设置、主题 |
| `/setup` | SetupPage | 首启向导（建账号 → 选模型 → 选人格） |

旧地址（`/agent/*`、`/policy`、`/advanced`、`/config-profiles`）在 `router/index.ts` 里**重定向**到新页面，
不要再新增"一个功能两个路由"的入口。

## 4. 状态与数据流

```
views/*.vue
   └─ stores/*.ts（Pinia）
        └─ api/http.ts → /api/*
```

约定：

- **页面不直接 fetch**：请求走 store 的 action；store 负责缓存、失效与错误提示；
- **配置类页面**由后端 schema 驱动（`components/config/*`），加字段不改前端；
- **账号切换**是全局状态（`stores/bots.ts`），页面读"当前账号"而不是自己记 id；
- 实时性：日志页用 WebSocket（`app/webui/ws.py`），统计页用 `stats_updated` 推送 + 去抖重查；
- 保存语义统一：改动 → 保存按钮/自动保存 → 顶部状态条（`SaveStatusBar.vue`），不要每个页面自己发明。

## 5. 组件约定

- 卡片化布局：页面 = 若干 `v-card`；移动端 (`mdAndDown`) 用卡片行替代表格，避免横向滚动；
- 表格页必须照顾 390px：能换行就换行，不能换行就切卡片；
- 复用组件放 `components/<领域>/`，**页面私有组件留在页面文件内**，不要造 `components/common` 垃圾场；
- 文案规范见 [UI_COPY.md](UI_COPY.md)（有闸门测试，别自己造词）。

## 6. 构建与验收

```bash
cd app/webui/dashboard
npm run build        # 产出 dist（含 vue-tsc 类型检查）
```

改完前端必须按 [FRONTEND_VERIFY.md](FRONTEND_VERIFY.md) 走：**重建 dist → 起预览实例 → 两尺寸截图 → 量 overflow**。
`dist` 与源码分两个提交。

## 7. 已知约定/坑

- **hash 路由**：直接访问 `/ai` 需要 `/#/ai`；后端没有 history fallback。
- **dist 进仓库**：改了 `src` 不重建 `dist`，用户看到的是旧界面——这是最常见的事故。
- **不要在前端硬编码配置项**：字段、分组、显隐都来自后端 schema；前端只决定"怎么好看"。
- **旧页面不要复刻**：`/legacy`、`static/js` 那套已删；需要改的地方是 `src/`。
