# QQBot Next Dashboard

QQBot Next 新版管理后台：Vue 3 + Vuetify 3 + Vite + TypeScript。

## 功能

- **总览首页**：Bot 状态 / 模块统计 / 最近日志
- **账号管理**：WS 连接配置卡片（增删改查、输入框失焦自动保存）、连接/断开/重连、多群管理
- **功能模块**：搜索、分类分组、启停、schema 配置表单（boolean/integer/float/password/select/textarea/time/string_list/list/dynamic/repeater + 分组折叠 + showIf）、权限黑白名单、单一服务模式、插件自定义页 iframe
- **Agent 面板**：框架级 LLM 配置 + 定时任务 + 主动消息状态
- **日志控制台**：WebSocket 实时流、过滤、暂停、级别设置、高度拖拽、“显示原始日志”切换（默认简洁日志）
- **设置**：浅/暗双主题（AstrBot 同款设计令牌）、日志偏好

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

- `dashboard/dist/index.html` 存在时 `/` 渲染新版 Dashboard（注入 `window.WEBUI_TOKEN`）
- `/legacy` 保留旧版 UI 作回退
- 静态资源经根挂载提供（`/assets/*`）

## 目录结构

```
src/
├── api/          # http 客户端、WebSocket 管理器、API 契约文档（docs/api-contract.md）
├── stores/       # Pinia：bots / modules / webui / logs / theme / notify
├── components/
│   ├── config/   # schema 表单渲染器 + 四类 widget + 权限编辑器
│   ├── agent/    # Agent 定时任务 / 主动消息面板
│   └── LogConsole.vue / MultiGroupDialog.vue
├── layouts/      # 应用外壳（侧边栏 + 顶栏 + 控制台 dock）
└── views/        # 总览 / 账号 / 模块列表 / 模块配置 / Agent / 设置
```

## 设计语言

主题令牌对齐 `1/AstrBot-master/dashboard`（`src/plugins/vuetify.ts`）：

- 主色 `#3c96ca`（浅）/ `#5ba4d4`（暗）
- 卡片 1px 边框 + 16px 圆角，柔和色底徽章，胶囊按钮
- 字体栈：系统 UI + PingFang SC / Microsoft YaHei 回退
- 深浅主题切换持久化到 localStorage
