# Dashboard 前端界面架构（可动工版）

> 目标：一个从没接触过 QQ 机器人的用户，**4 个页面、5 个必填输入**之内让机器人在 QQ 里回第一句话；
> 同时不牺牲现有的 160 个 Agent 配置项，重度用户一个都不少。
>
> 现状基线（改动前）：11 个一级导航、23 个可达页面、Agent 区 130 个字段平铺、首启 5 步跨 5 页且无引导、
> 配置分级元数据（`importance`）后端已给但前端未消费。

---

## 1. 设计原则

| 编号 | 原则 | 具体含义 | 反面例子（当前代码） |
|---|---|---|---|
| P1 | **渐进披露** | 默认只给"能跑起来的最小集合"，进阶项按需展开，**永不删除** | 77 个字段平铺在"常用区" |
| P2 | **任务导向 IA** | 导航按用户目标分组，不按技术模块分组 | 「Provider 预设」「Tool 管理」「会话数据」当一级入口 |
| P3 | **Registry 驱动** | 导航 / 路由 / 分组 / 分级 / 就绪检查都从同一份 registry 派生 | 导航、路由、概览卡片各抄一份（虽已部分收敛） |
| P4 | **可回退** | 任何改动都能"恢复默认"；危险操作有确认或撤销 | 单字段无法重置，只能整份覆盖 |
| P5 | **状态可见** | 保存态、连接态、就绪态常驻视野 | WebSocket 断开无任何提示；保存语义三套并存 |

---

## 2. 信息架构：11 个一级入口 → 5 个

### 2.1 一级导航

| 区 | 图标 | 面向 | 包含 |
|---|---|---|---|
| **首页** | `mdi-view-dashboard-outline` | 所有人 | 就绪清单、健康概览、最近错误 |
| **机器人** | `mdi-robot-outline` | 日常 | 账号连接、功能模块（含插件配置） |
| **AI** | `mdi-creation-outline` | 日常 + 进阶 | 模型、人格、对话、记忆、进阶中心 |
| **运维** | `mdi-console` | 排障 | 日志、会话数据、工具、诊断 |
| **设置** | `mdi-cog-outline` | 偶尔 | 外观、显示偏好、插件恢复、关于 |

导航项收敛规则：**一级入口只放"我一辈子会点 100 次以上"的东西**。Provider 预设、人格预设、Tool 管理
全部降级为二级页面（从 AI 区或运维区进入），但**路由路径保留**，历史深链不断。

### 2.2 路由树（hash 路由保持不变）

```
#/
├─ (index)                  首页 OverviewPage            ← 重写
├─ setup                    上手向导 SetupWizardPage     ← 新增
│  └─ :step                 bot | model | persona | done
├─ bot/
│  ├─ accounts              账号 BotsPage                ← 改造
│  ├─ modules               模块列表 ModulesPage          （保留）
│  └─ modules/:name         模块配置 ModuleConfigPage     （保留）
├─ ai/
│  ├─ (index)               AI 概览 AgentOverviewPage    ← 由 AgentPage 改造
│  ├─ model                 模型与成本 AiModelPage        ← 新增（吸收 ProviderPresetsPage）
│  ├─ persona               人格 PersonasPage             ← 保留，加"应用给当前 Bot"
│  ├─ dialog                对话行为 AgentSectionPage(dialog)
│  ├─ memory                记忆与会话 AgentSectionPage(memory)
│  └─ advanced              进阶中心 AiAdvancedPage       ← 新增（吸收 AdvancedPage）
│     └─ :section           工具与能力 / 主动消息 / 消息与输出
├─ ops/
│  ├─ logs                  日志 LogsPage                 （保留）
│  ├─ sessions              会话数据 SessionsPage          （保留）
│  ├─ tools                 工具 ToolsPage（4 tab）        （保留）
│  └─ diagnostics           诊断 DiagnosticsPage          ← 新增
└─ settings                 设置 SettingsPage             ← 扩容
```

**深链兼容表**（router 里逐条 redirect，一次性写全）：

```
/agent                → /ai
/agent/basic          → /ai/model
/agent/permission     → /ai/dialog
/agent/session        → /ai/memory
/agent/behavior       → /ai/dialog
/agent/stream         → /ai/dialog
/agent/memory         → /ai/memory
/agent/role|model     → /ai/persona | /ai/model
/agent/tools|mcp|knowledge → /ai/advanced/tools
/agent/panels         → /ai/advanced/proactive
/agent/onebot         → /ops/tools/onebot
/advanced             → /ai/advanced
/advanced/*           → /ai/advanced/*
/tools/*              → /ops/tools/*
/bots                 → /bot/accounts
/modules/*            → /bot/modules/*
/provider-presets     → /ai/model?tab=presets
/personas             → /ai/persona
/sessions             → /ops/sessions
```

### 2.3 AI 区的分区重划（6 页 → 4 页 + 进阶中心）

| 新页面 | 吸收原分区 | 字段数 | 默认可见（basic） |
|---|---|---|---|
| 模型与成本 | basic(模型部分) + 全部 Provider 概念 | 11 | ~8 |
| 人格 | basic(人格部分) + persona_preset_id | 8 | ~6 |
| 对话行为 | behavior + stream(仅 4 个 basic) + permission | 32 | ~14 |
| 记忆与会话 | session + memory(仅 2 个 basic) | 34 | ~10 |
| 进阶中心 | stream 其余 + memory 其余 + adv_tools + adv_proactive + adv_output | 106 | —（这里是进阶本身）|

合计仍是 130 个字段，**一个都没删**，只是默认视图从 130 → 38。

---

## 3. 三个体验支柱（本次架构的核心）

### 3.1 就绪清单 Readiness —— 解决"下一步干什么"

**为什么关键**：它把首启的 5 步隐形链变成界面上一条可勾选的清单，并且**不依赖 Bot 已连接**
（当前 `AgentLayout.vue:42,121-128` 用 `bot_id === null` 直接阻断渲染，导致没连上 QQ 的人配不了 AI）。

```ts
// composables/useReadiness.ts
export type ReadinessState = 'done' | 'todo' | 'blocked' | 'warn'

export interface ReadinessStep {
  id: 'account' | 'connected' | 'provider' | 'model' | 'persona' | 'agent_on'
  label: string          // 「连接一个 QQ 账号」
  detail: string         // 未完成时的原因/代价说明，1 句
  state: ReadinessState
  weight: number         // 排序权重，越小越靠前
  cta?: { to: string; text: string }
  /** 可选：一步到位的修复动作（如"用默认值创建"），失败要回滚 */
  quickFix?: () => Promise<void>
}
```

**数据源与判定规则**（全部走已有 API，无需后端改动即可先跑起来）：

| step | 数据源 | done 条件 | blocked 条件 |
|---|---|---|---|
| `account` | `useBotsStore().bots` | `bots.length > 0` | — |
| `connected` | `useBotsStore().currentBot.status` | `=== 'connected'` | `account` 未完成 |
| `provider` | `GET /api/provider-presets` | `presets.length > 0` | — |
| `model` | `GET /api/provider-models` + `agent.poolModelIds` | 池内 ≥ 1 个启用模型 | `provider` 未完成 |
| `persona` | `GET /api/role-presets` | 有预设或 `draft.system_prompt` 非空 | — |
| `agent_on` | `GET /api/agent/config` | `enabled && poolModelIds.length > 0` | `model` 未完成 |

**复用点（同一个 composable，四处消费，保证口径一致）**
1. `OverviewPage` 顶部主卡片（6 行进阶条）
2. `SetupWizardPage` 的 4 步（把 6 项折叠成 4 步：连接 → 模型 → 人格 → 完成）
3. `AgentOverviewPage` 顶部精简版（只显示与 AI 相关的 3 行）
4. 顶栏：未完成时显示一个 `mdi-alert-circle` 角标 → 点进 `/setup`

**缓存与失效**：`stores/readiness.ts` 缓存结果；订阅 WS 事件 `bot_status_updated` /
`resources_updated` / `webui_config_updated` 触发重算（300ms 防抖）。

### 3.2 配置分级 Progressive Disclosure —— 解决 130 个旋钮

后端 `app/agent/config_schema.py:1015-1017` 已经给每个字段标了 `importance: basic|advanced`
（basic ≈ 55 / advanced ≈ 105）。**前端现在完全没用**（只在 `agent/sections.ts:208` 的死函数里读过）。
架构上把它做成三层：

**Layer 1｜默认视图 = 只看 basic**
- `ConfigForm` 新增 prop `mode: 'basic' | 'all'`，默认 `'basic'`
- `showIf` 已隐藏的字段不计数；全为空的组自动隐藏
- 组标题右侧显示 `进阶 3 项`

**Layer 2｜页内展开 = 看全部**
- 每个分组标题右侧一枚 `AdvancedToggle`：
  `显示进阶字段 (12)` ⇄ `收起进阶字段`
- advanced 字段在展开态渲染时带 `<v-chip size="x-small">进阶</v-chip>` 和左侧虚线边框
- 展开状态写进 URL `?all=1`，可分享、可回退

**Layer 3｜全局偏好**
- 设置页一个开关：`默认显示进阶字段`，持久化到 `localStorage('qqbot_show_advanced')`
- `useUiPrefs()` composable 统一读写；`ConfigForm` 从它取默认值

**跟搜索打通**：`AgentConfigSearchDialog` 命中 advanced 字段时跳转 `?all=1&group=<gid>&focus=<key>`，
`ConfigForm` 读到 `focus` 就滚动 + 高亮该字段 2 秒。

**实施风险与对策**：`importance` 名单可能漏标（`_BASIC_FIELDS` 硬编码在 `config_schema.py:990-1013`）。
对策：`ConfigForm` 对缺失 `importance` 的字段一律按 `basic` 处理（宁多显示、不漏），并在
`AgentAdvancedPage` 顶部显示一句 `共 N 项进阶参数`（N 由前端算，与后端名单天然对账）。

### 3.3 保存与状态 —— 一套语义

问题现状：模块配置自动存（2s 防抖）、Agent 自动存（2s 防抖）+ 顶栏又给一个"保存配置"大按钮、
账号页"失焦自动存 + 保存所有配置"。用户无法建立预期。

统一为 `composables/useAutoSave.ts`：

```ts
const { state, lastSavedAt, dirtyCount, saveNow, reset } = useAutoSave(draft, {
  debounceMs: 1500,
  save: (snapshot) => api.save(snapshot),
  guard: { beforeunload: true, routeLeave: true, scope: 'ai-config' },
})
```

配套 `components/common/SaveStateChip.vue`，四种状态一体：

| state | 文案 | 交互 |
|---|---|---|
| `clean` | `已保存 · 12:03:41` | hover 显示"恢复默认" |
| `dirty` | `未保存 · 3 项改动` | 点击立即保存 |
| `saving` | `保存中…`（转圈） | 禁用 |
| `error` | `保存失败 · 重试` | 点击重试，展开错误详情 |

**改动点**：移除 `AgentLayout.vue:103-112` 的「保存配置」大按钮，换成 `SaveStateChip`（保留手动保存能力）。
`ModuleConfigPage` / `BotsPage` 一起迁移到同一 composable，三处文案和时序一致。

---

## 4. 页面级规格

### 4.1 首页 OverviewPage（重写）

```
┌ ReadinessCard ─────────────────────────────────────────────┐
│ 还差 3 步就能让机器人说话                     [继续配置 →] │
│ ✓ 已添加 QQ 账号                                            │
│ ✓ 已连接（Bot 1234567）                                     │
│ ○ 配置一个 AI 模型                          [去配置 →]      │
│ ○ 选一个人格                                [去配置 →]      │
│ ○ 打开 Agent 总开关                          [去开启 →]     │
└────────────────────────────────────────────────────────────┘
┌ HealthGrid（4 张可点卡片）──────────────────────────────────┐
│ QQ 连接  1/1 已连接   │ AI 模型  2 个可用 · 1 个未测        │
│ 功能模块  12 启用/18  │ 最近错误  2 条（最近 14:22）        │
└────────────────────────────────────────────────────────────┘
┌ RecentErrorsCard ──────────────────────────────────────────┐
│ 14:22 LLM 请求失败：401 invalid api key        [查看日志 →] │
│ 13:05 Bot 连接超时：ws://127.0.0.1:3001        [诊断 →]     │
└────────────────────────────────────────────────────────────┘
```

- 删除现状的「最近日志」全量列表（`OverviewPage.vue:212-230`），它提供的是噪音而非决策信息。
- 4 张健康卡**必须可点**（现状 `OverviewPage.vue:111` 挂了 `app-card-hover` 抬升样式却无点击，
  是明确的错误引导）。
- `HealthGrid` 里"最近错误"从 `useLogsStore()` 过滤 `warning|error` 取最后 5 条。

### 4.2 上手向导 SetupWizardPage（新增）

**进入条件**：`localStorage['qqbot_setup_dismissed'] !== '1'` 且 `readiness.connected !== 'done'`
→ `/` 自动 replace 到 `/setup`。任何一步都能"跳过，直接进后台"（写入 dismissed）。

**Step 1｜连接 QQ**
- 三张卡片二选一：`NapCat / Lagrange 反向 WS`（推荐）/ `已有 ws 地址` / `稍后再说`
- 选反连时：端口输入默认 `3001`，下方折叠说明"在 NapCat 的后台把反向 WebSocket 指向
  `ws://127.0.0.1:3001`"，并给一个"如何安装 NapCat"外链（`docs` 里的部署文档）
- 表单校验：非空 + `/^wss?:\/\//` + 端口 1–65535
- **「测试连接」按钮**（不落库）：调 `POST /api/bots/{index}/test-connection`（见 §7），
  成功后显示 `已连上：昵称(QQ号)`，直接进 Step 2
- 失败时显示可读原因（后端错误码 → 人话映射表放在 `utils/errorHints.ts`）

**Step 2｜配置 AI 模型**
- 服务商模板网格（数据来自 `GET /api/provider-templates`，见 §7）：
  DeepSeek / 通义千问 / 智谱 / OpenRouter / 硅基流动 / 本地 Ollama / 自定义
- 选中 → 自动填 `api_base` + 默认模型名 → 只需填 API Key
- 提交走 `POST /api/provider-presets/quick-setup`：一次创建
  **连接预设 + 模型实例 + 加入 Agent 模型池**（现状要 3 个页面 6 次操作）
- 提交后自动调 `/api/provider-models/{id}/test`，展示真实回复

**Step 3｜选一个人格**
- 内置 3 个人格卡片（温柔陪伴 / 简洁助手 / 群聊气氛组）+ 「自己写 System Prompt」
- 选中即 `agent.onChange('system_prompt', ...)` 并保存

**Step 4｜完成**
- 展示"给 Bot 发一条消息试试" + 一个 **「发送测试消息」** 按钮（见 §7 `/api/agent/test-message`）
- 显示本轮实际配置摘要（模型 / 人格 / 开关），「进入后台 →」

### 4.3 AI 概览 AgentOverviewPage

- 顶部：精简 `ReadinessCard`（模型 / 人格 / Agent 开关 3 行）
- 健康警告从 3 条扩展到 6 条（补：Agent 总开关关闭、群聊与私聊都没开、
  模型池里的模型全部未通过测试）
- 「常用」4 张卡（模型与成本 / 人格 / 对话行为 / 记忆与会话），每张带状态徽标（沿用
  `agentSectionBadge` 思路，见 `agent/sections.ts:149`）
- 「进阶」1 张卡 → `/ai/advanced`，副标题 `106 项高阶参数 · 出问题或要精调时再来`
- 右上角三个工具：`配置模板` / `搜索配置` / `复制到其他 Bot`（现有三个 Dialog 全部保留）

### 4.4 模型与成本 AiModelPage（最大的重构）

现状 `ProviderPresetsPage.vue` 908 行，把「连接预设」「模型实例」「原始 JSON」「全局设置」
四层概念堆在一屏，且空态是死胡同（`AgentModelPool.vue:90-92` 提示加模型，点开下拉是空的）。

新结构（**默认只展示模型池，其余全部折叠为"高级"**）：

```
┌ 生效中的模型（按顺序请求）──────────────────── [ + 添加模型 ]┐
│ 1  deepseek-chat        DeepSeek 官方   ✓ 测试通过   ⋮       │
│ 2  qwen-plus            通义千问        ? 未测试     ⋮       │
│ 空态：没有任何模型 → [按服务商模板添加]（打开 AddModelWizard）│
└──────────────────────────────────────────────────────────────┘
▸ 高级：连接预设（3）           api_base / 重试 / 超时 / 密钥
▸ 高级：模型实例参数            温度 / 最大输出 Token / 上下文窗口 / 思考模型 / 模态
▸ 高级：原始 JSON 配置          按预设编辑 headers / extra_body
▸ 高级：全局默认与回退          default_preset_id / fallback_model_ids / provider_pool
```

- `AddModelWizard` 是**唯一**新增模型入口：服务商模板 → Key → `拉取模型` 多选 → 完成
- 拉不到模型时降级为手动输入模型名（保留现状能力）
- 每个模型行支持拖拽排序（现状只有上下箭头，`AgentModelPool.vue:78-86`）
- 「测试」按钮统一走 `/api/provider-models/{id}/test`，并把结果缓存成 `testedOk` 徽标

### 4.5 进阶中心 AiAdvancedPage

- 顶部固定工具条：`显示进阶字段` 开关 · `配置搜索` · `共 106 项` · `重置本区为默认`
- 三个 tab：工具与能力（27 项）/ 主动消息与定时（13 项）/ 消息与输出（13 项）
- 每个 tab 内复用 `AgentSectionPage` + `PageSectionNav`（现有 scroll-spy 保留）
- 顶部那颗 info 提示保留（`AdvancedPage.vue:37-43`），文案改成
  "这里的默认值都经过调优；只有当你明确知道要改什么时才动它们"

### 4.6 机器人 Accounts（改造 BotsPage）

- 「新增账号」从"直接插一张空卡片"改成 `AddBotWizard` 弹窗：
  方式选择 → ws 地址（带校验 + 端口提示）→ Token（`type="password"` + 显示切换）→
  Owner QQ → 测试连接 → 保存
- 卡片上的 Access Token 改成密码输入框（现状 `BotsPage.vue:234-244` 是明文，
  而 Provider 页的 api_key 反而是密码框，两处不一致）
- 新增 `最近事件` 折叠区（最近 3 条与该账号相关的错误，来自日志过滤）

---

## 5. 目录结构（新增部分）

```
dashboard/src/
├─ composables/
│  ├─ useReadiness.ts          # 就绪检查（§3.1）
│  ├─ useAutoSave.ts           # 统一保存语义（§3.3）
│  ├─ useUiPrefs.ts            # 显示偏好（show_advanced 等）
│  ├─ useConnectionHealth.ts   # API/WS/token 健康
│  └─ useProviderTemplates.ts  # 服务商模板拉取 + 缓存
├─ stores/
│  ├─ readiness.ts             # 就绪结果缓存 + WS 失效
│  ├─ provider.ts              # 从 ProviderPresetsPage 抽出的全部请求
│  └─ ui.ts                    # 侧边栏折叠 / 进阶模式 / 上次访问
├─ components/
│  ├─ onboarding/
│  │  ├─ ReadinessCard.vue
│  │  ├─ ReadinessItem.vue
│  │  └─ SetupStepper.vue
│  ├─ health/
│  │  ├─ HealthGrid.vue
│  │  ├─ HealthCard.vue
│  │  └─ RecentErrorsCard.vue
│  ├─ common/
│  │  ├─ PageHeader.vue        # 收敛 app-page-header 的重复写法
│  │  ├─ SaveStateChip.vue
│  │  ├─ AdvancedToggle.vue
│  │  ├─ ConfirmDialog.vue     # 替换全部 window.confirm
│  │  └─ SecretField.vue       # 密钥输入 + 显隐 + "留空=不改"语义
│  ├─ config/
│  │  ├─ ConfigField.vue       # label + desc + 校验 + 进阶徽章 + 单字段重置
│  │  ├─ FieldGroup.vue        # 分组卡 + 进阶计数 + 折叠
│  │  └─ ConfigForm.vue        # 改造：mode / focus / advancedCount
│  ├─ model/
│  │  ├─ ModelPoolCard.vue
│  │  ├─ AddModelWizard.vue
│  │  ├─ ProviderTemplatePicker.vue
│  │  └─ ProviderPresetList.vue
│  └─ bot/
│     └─ AddBotWizard.vue
├─ layouts/
│  ├─ DefaultLayout.vue        # 5 区导航 + 全局健康条
│  └─ WorkbenchLayout.vue      # AI 区/运维区共用的二级 tab 外壳（替代 AgentLayout）
├─ views/
│  ├─ OverviewPage.vue         # 重写
│  ├─ SetupWizardPage.vue      # 新增
│  ├─ ai/AiModelPage.vue       # 新增
│  ├─ ai/AgentOverviewPage.vue # 由 AgentPage 改造
│  ├─ ai/AiAdvancedPage.vue    # 由 AdvancedPage 改造
│  └─ ops/DiagnosticsPage.vue  # 新增
├─ agent/
│  └─ sections.json            # 增加 area: 'ai' | 'advanced'，字段归属重划
└─ utils/
   └─ errorHints.ts            # 后端错误码 → 用户可读中文
```

---

## 6. Registry 扩展（sections.json v4）

现有 `agent/sections.json` 只区分 `area: agent | advanced`。升级为：

```jsonc
{
  "version": 4,
  "sections": [
    {
      "id": "ai_model",
      "path": "/ai/model",
      "title": "模型与成本",
      "area": "ai",
      "order": 10,
      "customFirst": true,
      "customFields": ["provider_model_pool"],
      "groups": [
        { "id": "model_pool", "label": "生效中的模型", "fields": [] },   // 纯定制卡
        { "id": "model_params", "label": "模型参数", "fields": ["model", "temperature"] }
      ]
    }
  ]
}
```

规则补充：
- `area` 取值扩为 `'ai' | 'advanced'`
- `groups[].fields` 里出现的字段若 `importance === 'advanced'`，在默认视图隐藏（不是删除）
- 新增 `groups[].emptyHint`：分组在默认视图下为空时显示的引导文案（如"暂无模型 → 去添加"）

前后端契约测试（现有 `tests/` 里已有读 `sections.json` 的用例）需同步：
**断言 `sections.json` 覆盖 schema 中除 `retired` 外的全部字段**——这条现在就该有，防止加字段漏挂页面。

---

## 7. 后端契约增补（最小集，按价值排序）

| # | 端点 | 用途 | 价值 |
|---|---|---|---|
| 1 | `POST /api/bots/{index}/test-connection` | 试连不落库，返回 `{ok, nickname, user_id, error}` | 向导 Step1 的"测试连接"；现状只能靠 connect 猜 |
| 2 | `POST /api/agent/test-message` | 用当前配置跑一轮，返回 `{reply, elapsed_ms, tokens}` | **唯一能验证"我调完参数效果如何"的闭环** |
| 3 | `GET /api/provider-templates` | 服务商模板 `{id,name,icon,api_base,models[],docs_url}` | 去掉前端硬编码；向导 Step2 的心脏 |
| 4 | `POST /api/provider-presets/quick-setup` | 一次创建预设+模型+入池 | 3 页 6 步 → 1 步 |
| 5 | `GET /api/readiness?bot_id=` | 后端算就绪清单（可选） | 前端可自行拼装，非必需 |

前两项是体验闭环的关键，后两项是效率优化。**没有它们架构也能落地**（前端做降级路径），
但落地后体验会有明显台阶。

---

## 8. 实施阶段（每个阶段 = 一个可独立提交的单元）

### P0 基座：让"下一步"可见（不改变视觉大结构）

| 步 | 提交信息 | 主要文件 | 验收 |
|---|---|---|---|
| 1 | `feat(dashboard): add readiness composable and store` | `composables/useReadiness.ts`、`stores/readiness.ts` | 控制台能打出 6 步状态，口径与 API 一致 |
| 2 | `feat(dashboard): rebuild overview with readiness and health` | `OverviewPage.vue`、`components/health/*` | 首页出现可勾选清单；健康卡可点；旧日志列表移除 |
| 3 | `fix(dashboard): unblock agent config without a live bot` | `AgentLayout.vue` | 没连 Bot 也能进 AI 区，显示只读/离线提示而不是空白 |

### P1 渐进披露：收益最大

| 步 | 提交信息 | 主要文件 | 验收 |
|---|---|---|---|
| 4 | `feat(config): add importance-driven basic mode to ConfigForm` | `ConfigForm.vue`、`ConfigField.vue`、`FieldGroup.vue`、`AdvancedToggle.vue` | 默认视图字段数 ≤ 60；`?all=1` 恢复 130 |
| 5 | `feat(config): add per-field reset, default hint and validation` | `ConfigField.vue` | 每个字段可单独恢复默认；非法值即时红框 |
| 6 | `refactor(dashboard): split agent area into ai and advanced` | `router/index.ts`、`sections.json` v4、`views/ai/*` | 深链兼容表全部 302 到位 |

### P2 上手动线

| 步 | 提交信息 | 主要文件 | 验收 |
|---|---|---|---|
| 7 | `feat(onboarding): add four-step setup wizard` | `SetupWizardPage.vue`、`onboarding/*` | 从零到机器人回话 ≤ 4 页 |
| 8 | `feat(model): add provider template picker and quick setup` | `AddModelWizard.vue`、`ProviderTemplatePicker.vue`、`stores/provider.ts` | 空态不再死胡同 |
| 9 | `feat(bot): add account wizard with validation and test` | `AddBotWizard.vue`、`SecretField.vue` | 明文 Token 问题消失 |

### P3 闭环与打磨

| 步 | 提交信息 | 主要文件 | 验收 |
|---|---|---|---|
| 10 | `feat(agent): add test-message dialog` | `components/agent/TestMessageDialog.vue` | 后台能发一条消息看到真实回复 |
| 11 | `refactor(common): unify autosave and save-state chip` | `useAutoSave.ts`、`SaveStateChip.vue`、三处调用点 | 三页保存语义一致，离开有拦截 |
| 12 | `feat(shell): add connection health banner` | `DefaultLayout.vue`、`useConnectionHealth.ts` | WS/API 断开 3 秒内可见 |
| 13 | `chore(dashboard): accessibility pass` | 全局 | 开关有可访问标签；确认框统一为 `ConfirmDialog` |

---

## 9. 验收标准（可量化，写进 PR 模板）

1. **上手动线**：全新环境从打开后台到 QQ 里收到机器人第一条回复，**≤ 4 个页面、≤ 5 个必填输入**。
2. **默认视图密度**：AI 区默认可见配置项 **≤ 60**（当前平铺 130）；进阶中心入口显式标注 106 项。
3. **不丢能力**：`sections.json` 覆盖 schema 全部非 retired 字段（契约测试断言），
   任意字段 **≤ 3 次点击**可达（AI 概览 → 分区 → 分组 → 字段）。
4. **状态可见**：任何配置改动都有明确保存态；未保存离开被拦截；WS 断开 3 秒内可见提示。
5. **无死胡同**：所有空态都有下一步动作按钮，且按钮指向的页面**真的能完成这件事**
   （现状反例：模型池空态 → 添加模型 → 空下拉）。
6. **一致性**：所有删除走 `ConfirmDialog`；所有密钥字段走 `SecretField`；
   所有页面头部走 `PageHeader`。

---

## 10. 明确不做（划清边界，避免过度设计）

- **不换 UI 组件库**：Vuetify 3 够用，换库收益远小于成本；`plugins/vuetify.ts` 的主题令牌继续用。
- **不改 hash 路由**：产物由 FastAPI 静态挂载，没有 SPA fallback，改 history 模式是自找麻烦。
- **不做多语言**：当前全中文单语，引入 i18n 只会让 160 个字段的中文说明多一份维护负担。
- **不重写 schema 渲染器的类型体系**：`Record<string, any>` 虽然不严谨，但重写它的收益
  低于本次 IA 重构；仅在新增组件上用局部强类型。
- **不删除任何配置字段**：`retired` 名单已经存在（`sections.json:4-38`），退役决策继续走那个流程，
  本次架构只改"怎么展示"，不改"有哪些"。
