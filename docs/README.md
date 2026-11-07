# 本地文档地图

本仓库的文档只有两层：**给人用**（根目录 `README.md` / `MAINTENANCE.md`）与**给改代码的人**（本目录）。
不保留历史稿：过程记录以 commit message 和 PR 为准，文档只写**当前事实**。

> 规则：只有本目录顶层这 12 份文档（含本索引）+ 根目录 README/MAINTENANCE/STYLE/AGENTS/CONTRIBUTING/SECURITY
> 属于现行文档，必须与代码一致。写新文档前先在这里登记；过时内容直接改掉或删除，不要"留着以后看"。

---

## 一、用户 / 运维

| 文档 | 读者 | 内容 |
|---|---|---|
| [../README.md](../README.md) | **使用者** | 这是什么、装、启动、接 QQ、后台各页干什么、更新回滚、常见故障 |
| [../MAINTENANCE.md](../MAINTENANCE.md) | **部署者 / 运维** | 目录结构、环境变量、日志、数据库备份恢复、模块管理、排障手册 |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | **贡献者** | 环境、改哪里看哪份文档、提交规范、验证与 PR 要求 |
| [../SECURITY.md](../SECURITY.md) | **所有人** | 漏洞上报渠道、支持版本、部署安全建议 |

## 二、开发者（必须与代码一致）

| 文档 | 读者 | 内容 |
|---|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 改框架代码 | 分层、目录职责、一次请求从入口到回复的路径、改哪里看哪 |
| [MODULE_DEV.md](MODULE_DEV.md) | 写插件 | 插件生命周期、钩子与可声明能力、配置表单、工具注入、提示词注入、打包与闸门 |
| [PLUGIN_STYLE.md](PLUGIN_STYLE.md) | 写插件 | 目录/命名/导入规范与自动检查 |
| [FEATURE_REGISTRY.md](FEATURE_REGISTRY.md) | 写插件 | 框架提供的可声明/可订阅能力点清单 |
| [TOOL_STATE.md](TOOL_STATE.md) | 改工具逻辑 | 工具在一轮里的状态机：可用性判定、权限、作用域、结果回应 |
| [AGENT_CONFIG.md](AGENT_CONFIG.md) | 改配置项 | 每个 Agent 配置键的含义、默认值、生效时机 |
| [dashboard-frontend-architecture.md](dashboard-frontend-architecture.md) | 改前端 | Dashboard 结构、状态、组件与路由 |
| [UI_COPY.md](UI_COPY.md) | 改界面文案 | 文案规则、术语表、反例 |
| [FRONTEND_VERIFY.md](FRONTEND_VERIFY.md) | 改前端后验收 | 构建 + 预览 + 截图 + 量布局的完整步骤 |
| [TEST_POLICY.md](TEST_POLICY.md) | 所有人 | 什么该测、什么时候跑、删模块时怎么处理测试 |
| [DATA_STATS_DESIGN.md](DATA_STATS_DESIGN.md) | 改统计 | 统计什么、口径、数据来源、页面怎么呈现 |
| [plugin-template/](plugin-template/) | 写新插件 | 可直接复制的插件骨架 |

---

## 怎么写文档

1. 先问"读者是谁"：用户能看懂的不写实现细节，改代码的人需要能照做。
2. 只写**当前事实**；"当初为什么这么做"用一句 commit message 交代，不单独存档。
3. 同一事实只写透一次，其它地方用相对链接引用。
4. 改完核对内部链接（`docs/` 内用相对路径），确保点得开。
