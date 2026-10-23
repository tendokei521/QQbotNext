# QQBot Next Dashboard

QQBot Next 新版管理后台：Vue 3 + Vuetify 3 + Vite + TypeScript。

这是**唯一**一套前端。历史上手写的 `app/webui/static/` + `templates/index.html`
与 `/legacy` 回退路由已经删除（见 `STYLE.md` §12.4）——同一张配置表存在两遍时，
schema 每加一个字段要改两处，第二处极易漏且没有测试会红。

## 功能

- **总览首页**：Bot 状态 / 模块统计 / 最近日志
- **账号管理**：WS 连接配置卡片（增删改查、输入框失焦自动保存）、连接/断开/重连、多群管理
- **功能模块**：搜索、分类分组、启停、schema 配置表单（boolean/integer/float/password/select/textarea/time/string_list/list/dynamic/repeater + 分组折叠 + showIf）、权限黑白名单、单一服务模式、插件自定义页 iframe
- **Agent 面板**：5 个配置分区（基础配置 / 权限管理 / 对话行为 / 会话管理 / 高级功能），分区由 `src/agent/sections.json` 驱动
- **日志控制台**：WebSocket 实时流、过滤、暂停、级别设置、高度拖拽、“显示原始日志”切换（默认简洁日志）
- **设置**：浅/暗双主题、日志偏好、实验性选项开关

## 开发

```bash
pnpm install
pnpm dev        # http://localhost:3000，/api 与 /ws 代理到 http://127.0.0.1:9200
```

## 构建

```bash
pnpm build      # 产物输出 dist/，FastAPI 挂载后访问 /
```

构建产物由 `app/webui/app.py` 自动挂载：

- `dashboard/dist/index.html` 存在时 `/` 渲染新版 Dashboard（**不再注入 `window.WEBUI_TOKEN`**，
  鉴权改由登录页签发的 HttpOnly 会话 cookie 承担）
- `dist` 缺失时 `/` 返回 503 + 一段「请先 `cd dashboard && pnpm install && pnpm build`」的提示页
- 静态资源经根挂载提供（`/assets/*`）

## 目录结构

```
src/
├── api/          # http 客户端（token + 统一信封解包）、WebSocket 管理器
├── stores/       # Pinia：bots / modules / agentConfig / webui / logs / tools / theme / notify
├── agent/        # 配置分区 registry：sections.json（生成物）+ sections.ts + customSections.ts
├── components/
│   ├── config/   # schema 表单渲染器（ConfigForm/FieldControl + 四类 widget + 权限编辑器）
│   ├── agent/    # Agent 页的定制卡片（基础配置 / 权限 / 模板 / 搜索 / 复制）
│   ├── common/   # PageSectionNav / PageWithSections 等通用件
│   ├── log/      # 日志导出
│   └── LogConsole.vue / MultiGroupDialog.vue / EmptyState.vue
├── layouts/      # DefaultLayout（侧边栏 + 顶栏 + 控制台 dock）、AgentLayout（Agent 区外壳）
├── views/        # 各页面；Agent 分区共用 AgentSectionPage
├── utils/        # schema 分组过滤（buildSectionSchema 等纯函数）
└── styles/       # main.css 全局样式
```

**分区清单不要手改**：`src/agent/sections.json` 由 `scripts/gen_sections.py` 从
`app/agent/config_partition.py::UI_PAGES` 生成，`tests/agent/test_agent_section_contract.py`
会断言它与生成器输出逐字节一致。

`docs/api-contract.md` 是前后端字段级契约（端点、schema 形态、字段类型语义）。

## 设计语言

主题令牌对齐 AstrBot Dashboard 的明暗双主题（见 `src/plugins/vuetify.ts`）：

- 主色 `#3c96ca`（浅）/ `#5ba4d4`（暗）
- 卡片 1px 边框 + 16px 圆角，柔和色底徽章，胶囊按钮
- 字体栈：系统 UI + PingFang SC / Microsoft YaHei 回退
- 深浅主题切换持久化到 localStorage
