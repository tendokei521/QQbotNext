# 身份 × 能力 × 作用域：Bot 拥有者与「开关便携性」的设计意见

> 面向：框架维护者 / 产品设计。回答两个问题：
> ① 为什么「账号页 owner」和「Agent 页拥有者/管理者」都不是预期形态；
> ② 如果目标是「一个面板决定回不回、怎么回、谁能用」，该按什么模型重排。
>
> 基线：`main@acb83fc`。以下"现状"结论均带 `文件:行号` 证据，可按行号复核。
> 与既有文档的关系：本文是**身份层与 UI 信息架构**的准一来源；
> 作用域存储/仲裁沿用 [`multi-account-design.md`](multi-account-design.md)（bindings + channel_policies），
> 配置键归宿沿用 [`../app/agent/config_partition.py`](../app/agent/config_partition.py)。

---

## 0. 一页结论

| 判断 | 结论 |
|---|---|
| 账号页的 owner | **不是"配得不对"，是死字段**。后端状态接口恒返回 `None`，权威判定只认 Agent 名册；但 DB 仍在写这一列、UI 仍在渲染一个可编辑输入框。用户填了当然"没反应"。 |
| Agent 页的拥有者/管理者 | **不是"位置不对"，是实体选错**。名册按 `account_id` 存，等于把"人"复制进每个账号的配置；新增账号即失权，同一个人在不同账号下身份可能不一致。 |
| 开关分散 | **不是排版问题，是没有"能力（capability）"这个一等对象**。能改变"回不回 / 谁回 / 怎么回"的入口有 **6 类表面、17 个落点**（§1.3），各自读不同存储、各自保存。只换 UI 不立模型，就是把分散搬个家。 |

**建议的顺序**：先立「身份（人）」与「能力（功能）」两个对象，再谈面板。三步之后，你想要的
"一个面板"是一个**能力的抽屉**（开关＋怎么回＋谁能用＋在哪生效），而不是又一个汇总页。

---

## 1. 现状审计

### 1.1 账号页 owner：一个还会被写回数据库的死字段

| 证据 | 事实 |
|---|---|
| `app/webui/dashboard/src/views/BotsPage.vue:286-295` | 每张账号卡片仍渲染 `Owner ID` 输入框，placeholder 写的是「管理员QQ号」 |
| `BotsPage.vue:94-102`、`:76` | 前端把它当数字解析、放回 payload 提交，读不到时还会回退到状态接口的值 |
| `app/webui/app.py:338-339` | 状态接口**恒返回** `"owner_id": None`，注释明确「v3 账号级 owner QQ 已取消」 |
| `app/infrastructure/config/account_store.py:137-158` | 写入链路仍在 `INSERT OR REPLACE INTO bots (… owner_id …)`，把用户输入原样落库 |
| `app/plugin/authority.py:98-105` | 判定链里 `is_bot_owner = in_owner_roster`，**只读名册**，不读 `bots.owner_id` |

结论：这是"三处状态都不一致"的典型——UI 可编辑、DB 会存、判定不读、接口返回 None。
用户看到的是一个**承诺了权限却不产生权限**的框，这比"没有这个框"更糟。

### 1.2 Agent 页的拥有者/管理者：按账号复制的一份名册

| 证据 | 事实 |
|---|---|
| `app/agent/config_schema.py:513-522` | 两个字段 `agent_owner_list` / `agent_manager_list`，`string_list`，位于「权限管理」页 |
| `app/agent/config_partition.py:74-89` | 五页布局里，`permission`（档位）+ 名册 + 高危审批同在「权限管理」 |
| `docs/AGENT_CONFIG.md:117-119` | 「v3 起这是拥有者身份的**唯一来源**；**留空则谁都不是拥有者**」 |
| `app/agent/config.py` + `_runtime(agent_manager, bot_id)`（`app/webui/api/agent.py:48-83`） | Agent 配置**按 account_id** 存取（`get_module_config("agent", bot_id)`） |

后果链条：

1. 名册是"每个账号一份"，所以**新增账号 = 所有人重新失权**（`permission="owner"` 的 3 个内置模块、
   工具里的 owner 档一律静默失效；管理指令另有独立问题，见 §1.3 末段）；
2. 同一个真人在 3 个账号下要登记 3 次，任一处漏改就出现「A 账号认他是 owner、B 账号不认」；
3. 现在靠「复制配置」对话框（`AgentCopyConfigDialog.vue`）在账号间搬配置来补，这是把
   **数据模型缺陷**做成了功能；
4. 名册与群角色被拉进同一个排序（`authority.py:103-112`：`owner > manager > group_owner > group_admin > member`），
   于是"群主"看起来比"管理者"低一档——这是把"平台事实"和"自授身份"混成一根尺子。

### 1.3 决定"回不回 / 谁回 / 怎么回 / 谁能用"的全部入口

**6 类表面、17 个落点**（开关类）：

| # | 表面 | 落点 | 存哪 | 维度 |
|---|---|---|---|---|
| 1 | 账号管理 | Owner ID 输入框（**死**） | `bots.owner_id` | 账号（无人读） |
| 2 | Agent → 基础配置 | 「启用 Agent」总闸 `AgentBasicSection.vue:23-39` | `bindings(agent, account).enabled` | 账号 |
| 3 | Agent → 基础配置 | 响应场景 `group_enable` / `private_enable`（`config_partition.py:66`） | `bindings(agent, account).config` | 账号 × 场景 |
| 4 | Agent → 权限管理 | 响应权限档位 `permission` | 同上 | 账号 |
| 5 | Agent → 权限管理 | 拥有者 / 管理者名册 | 同上 | 账号（**应为全局**） |
| 6 | Agent → 权限管理 | 名单范围（群/用户黑白名单，`AgentPermissionSection.vue:18-28`） | 同上 | 账号 × 群/人 |
| 7 | Agent → 权限管理 | 高危工具审批 `tool_approval_enable` | 同上 | 账号 |
| 8 | 功能模块 | 列表卡片开关 `ModulesPage.vue:451-460` | `bindings(module, account).enabled` | 账号 × 模块 |
| 9 | 模块详情 | 顶部「启用」开关 `ModuleConfigPage.vue:373` | 同上（同一事实两处入口） | 账号 × 模块 |
| 10 | 模块详情 | 「响应范围控制」`ModuleConfigPage.vue:467-477` | `bindings(module, account).permission` | 账号 × 模块 |
| 11 | 框架功能 → 插件响应策略 | 总览卡片 + 单插件页 `ResponderOverviewPage.vue` / `ResponderModulePage.vue` | `channel_policies(module, group)` | 模块 × 群（全局） |
| 12 | Tool 管理 | System Tools 每工具开关 | `bindings(agent, account).config.tools_disabled` | 账号 × 工具 |
| 13 | Tool 管理 | OneBot Tools 每工具开关 + 档位展示 | 同上 | 账号 × 工具 |
| 14 | Tool 管理 | MCP Tools 每工具开关 | 同上 | 账号 × 工具 |
| 15 | Tool 管理 | 模块 Tools 每工具开关 | 同上 | 账号 × 工具 |
| 16 | 顶栏 | 多群管理对话框（只读 + 指路）`MultiGroupDialog.vue:100-102` | 无（提示去模块页） | 群 |
| 17 | Agent 面板 | 配置模板 / 复制配置（跨账号搬 config） | 无（批量写） | 跨账号 |

**权限判定**另有多条独立入口，各自读不同来源：

| 入口 | 文件 | 读什么 |
|---|---|---|
| 事件身份计算 | `app/plugin/authority.py:72-114` | 群角色 + 名册 → `permission_role` |
| 模块过滤 | `app/plugin/authority.py:203-224` | binding.permission 名单 + 模块类级 `permission` |
| 工具过滤 | `app/agent/tools/spec.py:168-202`（`allowed_for_event`） | 工具 `permission` + `event.is_bot_owner` |
| 跨账号响应权 | `app/plugin/responder.py` + `app/application/responder_service.py` | `channel_policies` |
| 名册注入 | `app/plugin/nodes/inbound.py:288-325` | 再读一次 Agent 配置 |

**其中还有一个已经跑偏的判定**：`#llm` 管理指令只看 `event.is_admin`
（`app/agent/loop/command_dispatch.py:237`），而 `is_admin` 在 `authority.py:88` 只按
**QQ 群角色**赋值 `role in ("group_admin", "group_owner")`，名册身份只改 `permission_role`、
不改 `is_admin`。后果：

- 名册里的拥有者**在私聊里用不了**任何管理指令（私聊没有群角色 → `is_admin=False` → 「权限不足」）；
- 帮助文案写「仅管理员」（`app/agent/commands.py:32`）、`AGENT_CONFIG.md` 写「仅 Bot 拥有者」、
  代码实际认「群管理/群主」——**三处说法互不相同**。

这不是笔误，而是 §2 A4 的直接证据：同一份"管理员"概念被四个入口用四种口径实现。

24 个内置模块里，类级 `permission` 是 `everyone` 17 个、`member` 4 个、`owner` 3 个——
也就是说"谁能用某个模块"在多数情况下**写死在代码里，界面上根本没有对应开关**，
界面只提供"名单范围"（排除谁）这一种减法。

### 1.4 文档也已经分叉

| 文件 | 说法 | 与代码的关系 |
|---|---|---|
| `docs/AGENT_CONFIG.md:117-119` | 名册是唯一来源 | ✅ 与代码一致 |
| `docs/MODULE_DEV.md:132` | 「Bot 拥有者 ← 账号的 `owner_id`（连接配置）」 | ❌ 代码已不读，属**过期权威文档** |
| `docs/dashboard-frontend-architecture.md`（§4.6，约 :310） | 新增账号向导要填「Owner QQ」 | ❌ 设计稿仍在放大这个死字段 |

当"准一来源"本身出现两个版本，实现者与用户都会各自挑一个相信——这本身就是设计缺陷的证据。

### 1.5 为什么用户的感受是"都不是预期内的"

1. **账号 owner**：以为它是"这个 QQ 号的主人"，会授予最高权限；实际它是历史残留，写入即沉默。
2. **Agent 名册**：以为它是"Bot 的主人名单"，填一次即全局生效；实际它是"这个账号的 Agent 的名单"，
   每加一个账号就要重填，且与"群主/群管"混在同一张排序表里。
3. **开关**：以为"关掉一个功能"是一次操作；实际要先找对页面（Agent？模块？工具？群响应权？），
   而同一件事在列表页和详情页各有一个开关（#8 与 #9 是同一个 `enabled`）。

---

## 2. 根因：三件事被塞进同一个词

任何"谁能做什么"的系统，只有三个正交维度。本项目把它们压进了 `owner` / `permission` 两个词里：

| 维度 | 回答的问题 | 合理的存储维度 | 现状 |
|---|---|---|---|
| **身份 Identity** | 这个人是谁？（能管到哪一层） | 人 × 平台（全局），可选 account/channel 授权 | 存在账号字段（死）+ 每个账号的 Agent 配置（复制） |
| **能力 Capability** | 这个功能是什么？（模块 / Agent / 工具组） | capability_id | 分散在 modules / agent / tools 三套 API |
| **作用域 Scope** | 在哪生效、谁响应？ | global → account → channel（最具体者胜） | `bindings` 已有三档，但 UI 只暴露 account 一档 |

由此得到四条反模式（可直接当评审清单）：

- **A1 同一语义两处配置**：`bots.owner_id` 与 `agent_owner_list`；`modules` 列表开关与模块页开关；
  `webui.single_service` 与 `channel_policies`。
- **A2 把"人"按"连接"复制**：身份只要进了 per-account 配置，就一定会漂移。
- **A3 开关不是契约**：`enabled=false` 若只拦事件派发、不拦定时/主动消息/工具暴露，
  用户就会觉得"关了还在动"（`multi-account-design.md` §2 A2 已把它定为公理，需继续守住）。
- **A4 代码声明与用户配置混在一张脸上**：模块类级 `permission`（代码）与名单（配置）
  在界面上没有"默认 / 覆盖"的视觉区分，用户不知道哪个能改。

---

## 3. 外部参考：成熟项目怎么切这三刀

> 以下均为官方文档/源码结论，链接见 §9。只取与本题相关的部分。

### 3.1 逐项对照

| 项目 | 身份（谁）存在哪 | 开关（回不回）在哪 | 作用域（在哪生效） | 对本项目有用的点 |
|---|---|---|---|---|
| **AstrBot** | `admins_id` **字符串数组**，在「配置文件 → 访问控制」（原生多 owner，但随 profile 走） | 插件卡齿轮＝插件参数（全实例共用）；「配置文件 → 插件」＝该 profile 用哪些插件；**两层 AND** | 配置文件可按 UMO/会话路由 + 白名单；人格属配置文件 | 多 owner 是数组不是单值；**UI 必须显示"最终是否生效"**，因为两层开关会让人以为改了总开关就开了 |
| **NoneBot2** | `.env` 的 `SUPERUSERS`，**全局唯一集合**；`Permission(SUPERUSER)` 先于 Rule、任一 checker 通过即可 | 核心**没有**启停配置：禁用＝不 `load_plugin`；社区 `plugin-manager` 用 `npm enable/disable -u/-g` **按用户/按群**屏蔽 matcher | 无内建；靠 matcher 优先级/短路 | 身份与插件解耦得最干净；但"不加载"冒充"已禁用"导致无法热开关——**这是本项目必须避免的路** |
| **Koishi** | 数值 `authority`（0–4 准则 + 登录插件 5 级管理员），只能操作低于自己等级者 | 控制台「插件配置」右上角启用/停用，**停用不删代码也不删配置** | `$filter`（platform/user/guild/channel/self，可 and/or 嵌套）可挂插件**或插件分组**；`Schema.computed()` 让单个配置项按会话取值 | **开关（状态）与参数（config）分离**、同卡呈现；作用域下沉到参数级是最高级的形态，可作为 P4 之后的目标 |
| **Yunzai / TRSS** | `other.yaml` 的 `masterQQ`（全局）＋ TRSS 额外的 `master: ["stdin:stdin"]`（**账号级 owner**）并存 | `group.yaml` 的 `default.enable/disable` 按功能名黑白名单 + **每群一个块覆盖默认值**；其余开关散在各插件 config | 群块覆盖默认值＝教科书式继承 | **`masterQQ` + 账号级 `master` 并存正是本项目"账号 owner + 名册"的翻版**：外部项目已经证明它会漂移。`group.yaml` 的默认值+群覆盖值得直接抄 |
| **NapCat / Lagrange** | 无权限概念 | 每账号一份 `onebot11_<QQ>.json`（数组项带 `enable`）；新版支持全局默认文件兜底 | 账号＝连接＝配置单元 | 账号层只该管"连接与凭据"；全局默认 + 账号覆盖的顺序可直接借鉴 |
| **Discord / Telegram**（对照） | discord.py 的 `owner_id`/`owner_ids` **互斥**，owner 是 **bot 级**、不随 guild；Telegram **没有 owner 概念** | 细粒度靠 checks（`has_permissions` 等）；slash 的 `default_permissions` 可被服务器覆盖 | guild/channel 级权限 | owner 天然属于"应用"而非"连接"；权限矩阵优于布尔 `isAdmin` |

### 3.2 提炼出的 7 条原则（本文的设计依据）

1. **身份名册单一真相源**：一张表 `(platform, platform_uid, level, scope)`，不要在 env / profile / 插件配置各存一份。
2. **复合键**：多账号下同一人可能对应不同平台 UID，裸 QQ 号会漂移（本项目只有 QQ，但保留 `platform` 字段的成本近乎为零）。
3. **开关与参数两列**：`enabled` 是状态，`config` 是参数；Koishi 与 AstrBot 的插件卡都是这个模型。
4. **分层 AND 必须可见**：AstrBot「插件总开关 × profile 插件集」两层，UI 若不显示"当前最终是否生效"，用户会把"改了不起作用"当成 bug（本项目已在 §1.1 吃过一次同样的亏）。
5. **作用域单向覆盖**：全局默认 → 账号 → 群/会话 → 用户，只在最上层定义默认值（Yunzai `group.yaml`、NapCat 全局默认、Koishi filter 都是这个方向）。
6. **权限矩阵化**：能力 × 角色 × 作用域落一张表，别用布尔 `isAdmin`（本项目已有 `ROLE_RANK`，缺的是"能力×作用域"那一维的贯通）。
7. **UI 两页收口**：「身份/成员」一页管名册；「功能/插件」一页用矩阵（行＝功能，列＝作用域）管开关，参数下钻详情——这正是 §4.4 的形态。

### 3.3 外部项目已经踩过的坑（本项目正在踩的）

- **同一语义两处配置**：Yunzai `masterQQ` + 账号级 `master`；AstrBot 插件总开关 + profile `plugin_set`。
  → 对应本项目 `bots.owner_id` + `agent_owner_list`、模块列表开关 + 模块页开关。
- **账号级 owner 与系统级 owner 并存且无优先级规则**：TRSS 就是这么做的，结果是"谁是主人取决于你问哪个文件"。
- **用"未加载"冒充"已禁用"**：NoneBot 不 `load_plugin` 即等于禁用，于是无法热开关、UI 无从呈现。
  → 本项目的 `bindings.enabled` 是运行期状态而非加载开关，方向是对的，别退回去。
- **配置位置由插件自由决定**：Yunzai 文档直接写"具体请查看插件 README"，面板无法统一渲染。
  → 本项目用 `module.json` + `config_schema` 强制声明，这是优势，控制台必须继续依赖声明而不是手写页面。

---

## 4. 目标设计

### 4.1 身份层：把人从账号里拿出来（Identity）

新增全局实体，独立于账号与 Agent 配置：

```
Identity(platform="qq", user_id, role ∈ {owner, manager},
         scope ∈ {global, account:<id>, channel:<gid>}, note, created_at)
```

- **默认全局名单**：`owner` = 系统主人（能改一切，含身份本身）；`manager` = 能调教 Bot、能用管理指令。
- **账号/群级授权是覆盖**：`scope=account:acc1` 表示"只在这一个账号上算管理者"；
  解析同样"最具体者胜"，并返回 `source` 供 UI 显示。
- **删除**：`bots.owner_id`（列保留只读一版后清理）、`agent_owner_list` / `agent_manager_list`。
  迁移：把旧 `bots.owner_id` 与**每个账号**的 Agent 名册**并集**写入全局名单（幂等，`kv.identity_migrated`）。
- **Agent 页的「权限管理」**只保留「响应权限档位」和「高危审批」；名册字段改为一行只读摘要 +
  「去身份页管理」链接。

为什么这样能解决 1.2 的三个后果：新增账号不再失权（身份不在账号里）；一个人只需登记一次；
"群主/群管"回到"平台事实"这一维，不再与自授身份共用一根排序尺——判定器输出
`{role, source: group|identity|default}` 两个正交字段，排序只在需要时组合。

### 4.2 能力层：能力 = 开关与配置的唯一对象

`bindings(capability_id, scope_type, scope_id)` 已经是正确地基（`app/domain/binding.py`，
`app/infrastructure/config/binding_store.py`）。要补的只有三点：

1. **把 Agent 也当作 capability**：`capability_id="agent"`（既有文档已这么写），
   这样"Agent 总闸 / 场景开关 / 档位 / 审批"和其它模块走**同一套读写路径**。
2. **类级声明降级为 default**：模块的 `permission = "owner"` 等是**默认值**；
   `bindings.permission.role` 允许覆盖。UI 显式标注「默认：仅拥有者（模块声明）/ 当前：管理者及以上（已覆盖）」。
3. **工具开关并入所属能力的 config**：`tools_disabled` 已在 agent config 里，不要再单独长一套 API。

### 4.3 判定器：一个函数，界面可预览（PermissionResolver）

```
decide(capability_id, event_facts, resolved_binding, identity) -> Decision{
    allowed, role, role_source, reason, matched_rule
}
```

- 调用点收口：`ModulePermissionNode` / `AgentNode` / `ToolSpec.allowed_for_event` / 指令 / `ResponderService`
  全部走它，**禁止**再各自 `getattr(event, "is_bot_owner")`。
- 同一个函数供 WebUI 调用：`GET /api/permission/preview?capability=&account=&group=&user=`
  → 「在此群，该功能由 `acc1` 响应；张三可用（拥有者，来源：全局名单）；
  李四不可用（原因：需要管理者，实际为群成员）」。
- 这一步是**把"权限靠猜"变成"权限可看"**的关键；没有它，再漂亮的面板也只是把开关挪了位置。

### 4.4 一个面板：能力控制台（Capability Console）

路由建议 `/console`（替换「功能模块」列表页；`/modules` 保留为安装/卸载的"插件仓库"）。

**首屏：能力 × 账号矩阵**

```
┌ 能力控制台 ─────────────────────────── 作用域: [全局] [账号 acc1 ▾] [群 ▾] ┐
│ 能力                       acc1(主号)   acc2        acc3       操作         │
│ ▸ Agent（框架级）           ● 开         ○ 继承      ○ 继承     [详情]      │
│ ▸ 长期记忆 llm_memory        ● 开         ● 关        ○ 继承     [详情]      │
│ ▸ 知识库 llm_knowledge       ○ 继承       ○ 继承      ○ 继承     [详情]      │
│ ▸ 主动消息 llm_proactive     ● 关         ○ 继承      ○ 继承     [详情]      │
│ ▸ OneBot 工具                ● 开(12/18)  …                     [详情]      │
└──────────────────────────────────────────────────────────────────────────┘
   ● 开 / ○ 关 / ◐ 继承自全局
```

**点开一格 → 右侧抽屉，固定四段 + 一个折叠的高级参数（顺序即用户的心智顺序）**：

1. **开关**：这个功能对这个账号 / 这个群，回不回。（写 `binding.enabled`，支持"恢复继承"）
2. **怎么回**：输出形态（流式/整条/语音）、发言通道、触发方式（@/关键词/主动）、抖动与打断。
   来自模块 `config_schema` 的"输出行为"分组 + Agent 的 behavior 键——组内顺序由能力声明决定。
3. **谁能用**：身份档位（默认/覆盖）＋ 白黑名单（群、人）＋ 高危工具审批。
   名单编辑器复用现有 `PermissionEditor.vue`，但要明确写「本层覆盖全局」。
4. **在哪生效 / 谁响应**：群/私聊场景开关、群名单、每群响应权（all/dedupe/delegate/leader）。
5. **高级参数**（折叠）：模块 schema 剩余字段。

**抽屉底部固定「生效预览」条**（调 4.3 的同一函数）：
「群 A：acc1 响应（primary）；张三可用；李四不可用（需管理者）」。
这一条把"用户必须在脑中 join 四个页面"变成"页面替你做 join"。

**侧边栏（目标形态，5 个一级项）**：

| 一级项 | 管什么 | 现状对应 |
|---|---|---|
| 账号 | 连接、凭据、反向监听 | `/bots`（**删掉 owner**） |
| 能力控制台 | 开关 / 怎么回 / 谁能用 / 在哪生效 | `/modules` + `/agent/*` + `/tools/*` 的开关部分 |
| 身份与权限 | 全局 owner/manager 名单 + 账号/群级授权 | 新增 |
| 插件仓库 | 安装 / 更新 / 卸载 / 软卸载 | `/modules` 的安装部分 |
| 数据与运行 | 会话、统计、日志、设置 | `/sessions` `/stats` `/logs` `/settings` |

Agent 的**参数页继续存在**（人格、模型池、上下文、压缩……），但它只回答"怎么回"，
不再承载"回不回/谁能用"——后者全部收到控制台抽屉里。

### 4.5 「开启和关闭的便携性」= 六条可验收的定义

| # | 定义 | 验收方式 |
|---|---|---|
| P1 | **一屏一开关**：任何能力的启停 ≤ 1 次点击、0 次跳页 | 从控制台首页点到任一能力的 off，页面不跳转 |
| P2 | **作用域显式**：任何开关都能回答"我在哪一层改的" | 每格显示 `开/关/继承`，抽屉显示「继承自：全局」并可"恢复继承" |
| P3 | **开关即契约**：off 后派发、定时、主动消息、工具暴露、`on_online` 全停 | 现有 `tests/plugin/test_lifecycle_gate.py` + 新增"关掉后无任何出站消息"用例 |
| P4 | **可逆**：每次开关独立保存、即时生效、可撤销覆盖（不需要"整体重置"） | 关闭 → 重开 → 行为与配置完全回到原状；无隐藏副作用 |
| P5 | **可解释**：抽屉里第一个问题永远是"这个群回不回"，其余选项都是它的从属 | 用户测试：问"怎么关掉记忆"，`#1 点击` 内完成 |
| P6 | **聊天内等价**：面板能做的开关，群聊指令也能做（`#llm off memory`），写同一 binding | 指令与面板互相同步（WS 广播） |

### 4.6 与现有实现的对接：不需要重造

| 已有 | 复用方式 |
|---|---|
| `bindings` 三档作用域（`app/domain/binding.py`） | 控制台直接读写；`channel` 档已在（预留给"某群单独配置"），本轮可先只读 |
| `channel_policies`（`app/application/policy_store.py`） | 抽屉第 4 段的"每群谁响应"，整值替换即可 |
| `responder_service` 决策结构 | 抽屉底部"生效预览"直接调它（已有 `reason` / `degraded`） |
| 模块 `config_schema` + `webui_schema` | 抽屉第 2/5 段由 schema 生成，不新增手写表单 |
| `sections.json`（由 `config_partition.py` 生成） | Agent 参数页维持；控制台只取 `group_enable/private_enable/permission/behavior_*` |
| `PermissionEditor.vue` / `ConfigForm.vue` | 原样复用，只是挪进抽屉 |

---

## 5. 数据与 API 变更

| 变更 | 内容 |
|---|---|
| 新增表 `identities` | `(platform, user_id, role, scope_type, scope_id, note, created_at)`，唯一键 `(platform,user_id,role,scope_type,scope_id)` |
| 迁移 | `kv.identity_migrated`：`bots.owner_id` ∪ 每账号 `agent_owner_list` → 全局 owner；`agent_manager_list` → 全局 manager；幂等 |
| 新增 API | `GET/POST/DELETE /api/identities`；`GET /api/capabilities?account_id=&channel_id=`（一次拉全 resolved 视图 + sources）；`POST /api/bindings`（支持 `null` 删覆盖）；`GET /api/permission/preview` |
| 废弃 API | `POST /api/agent/config` 里的 `enabled` / `permission` 字段、`/api/module/{name}/toggle`、`/api/module/{name}/permission`、`/api/webui/single-service` —— 保留一版并标 `deprecated` |
| 删除字段 | `bots.owner_id`（先只读、后删列）、`agent_owner_list`/`agent_manager_list`（v4 迁移进 identities） |
| 文档修正 | `MODULE_DEV.md:132`、`dashboard-frontend-architecture.md` §4.6、`AGENT_CONFIG.md` §权限 |

---

## 6. 分期落地（每步独立提交 + 可验收）

| 阶段 | 内容 | 规模 | 验收 |
|---|---|---|---|
| **P0 修谎言**（0.5d） | 删账号页 Owner ID 与 `bots.owner_id` 的前端读写；修 `MODULE_DEV.md` / dashboard 架构稿的分叉描述；`app.py` 状态接口删该键 | 小 | 界面无账号 owner；`grep -r owner_id` 只剩迁移与 DB 兼容层 |
| **P1 身份层**（1–2d） | `identities` 表 + 迁移 + `GET/POST /api/identities` + 「身份与权限」页；Agent 页名册字段改只读摘要；判定器改读 identity；`is_admin` 收口到同一判定器（修 §1.3 末段的私聊/口径问题） | 中 | 新账号加入后 owner 立即生效；旧数据迁移幂等；名册 owner 在**私聊**也能用管理指令；`tests/plugin/test_authority*.py` 全绿 |
| **P2 控制台只读+开关**（2–3d） | `/console` 矩阵（能力 × 账号，三态 + 来源）；开关写 `bindings`；删模块列表卡片开关（改指详情）；删模块页顶部重复开关 | 中 | 任一模块可在一个页面完成开关；"继承/覆盖"显示正确；前端 build 通过 |
| **P3 抽屉：谁能用 + 怎么回**（2–3d） | 抽屉四段 + 生效预览；删 Agent 页「名单范围控制」与模块页「响应范围控制」卡片（改由抽屉写同一 binding）；身份 picker 支持搜索 | 中大 | 关一个功能 ≤ 1 次点击 + 0 跳页；预览与实际响应一致（同一判定器，单测覆盖） |
| **P4 收口**（2d） | 响应权策略并入抽屉；`single_service` 兼容层下线；多群对话框删除（或只留群列表）；文档同步 | 中 | 旧配置迁移后行为不变；全量 pytest + `ruff` 无新增；`docs/` 无重复来源 |

**回滚**：identity 表与 bindings 都是新增存储，P1/P2 均可 `kv` 开关回退；
`owner_id` 列保留到 P4 之后一个版本再删。

---

## 7. 验收清单（可直接当 review checklist）

- [ ] 任意"回不回/谁能用/在哪生效"的事实，只有一个写入口、只存一处；
- [ ] 界面上任何开关都能回答"我改的是哪一层，继承自哪里"；
- [ ] 新增账号后不需要任何"登记"动作，owner 依旧生效；
- [ ] 关闭一个能力后，重开能**逐字节**回到关闭前的配置；
- [ ] 同一个人在不同账号下身份一致（除非显式做了账号级授权）；
- [ ] 权限结果可预览、原因可读，且预览与运行时调同一函数；
- [ ] 文档里不再出现"账号 owner"这种已废弃概念；
- [ ] 代码级 `permission` 与用户覆盖在界面上有明确区分（默认 / 已覆盖）。

---

## 8. 如果只做三件事

1. **删掉账号页的 Owner ID**（P0，半天）——立刻消除"填了不起作用"的困惑，也让文档停止分叉。
2. **把名册升为全局身份**（P1）——命中率最高的一条：它解释了用户当前遇到的
   "换了账号 owner 指令就不好使""新加账号没人能管"的全部症状。
3. **立"能力控制台"的第一屏（只读 + 开关）**（P2）——先让"开关在哪"这个问题只剩一个答案，
   再逐步把"怎么回/谁能用"搬进同一个抽屉。

前两件是**修错**，第三件是**立新**；修错不需要等新架构，可以马上做。

---

## 9. 参考来源

外部项目（均为官方文档或源码）：

- AstrBot：[配置文件（admins_id / profile）](https://docs.astrbot.app/dev/astrbot-config.html)、
  [WebUI 说明](https://docs.astrbot.app/use/webui.html)、
  [插件与开关](https://docs.astrbot.app/use/plugin.html)、
  [内置指令与权限](https://docs.astrbot.app/use/command.html)、
  [OneBot v11 平台实例](https://docs.astrbot.app/platform/aiocqhttp.html)
- NoneBot2：[权限控制（SUPERUSERS / Permission）](https://nb2.baka.icu/docs/appendices/permission)、
  [nonebot-plugin-manager](https://github.com/nonepkg/plugin-manager)
- Koishi：[安装和配置插件（启用/停用）](https://koishi.chat/zh-CN/manual/usage/market.html)、
  [过滤器 $filter](https://koishi.chat/zh-CN/guide/plugin/filter.html)、
  [深入定制（authority）](https://koishi.chat/zh-CN/manual/usage/customize.html)、
  [权限管理](https://koishi.chat/zh-CN/guide/database/permission.html)
- Yunzai / TRSS：[default_config/other.yaml（masterQQ / master）](https://github.com/TimeRainStarSky/Yunzai/blob/main/config/default_config/other.yaml)、
  [项目配置（group.yaml 默认值+群覆盖）](https://yunzai-bot.com/get-started/config.html)
- NapCat：[基础配置（每账号配置文件 / 全局默认）](https://napneko.github.io/config/basic)；
  Lagrange：[OneBot 配置](https://lagrangedev.github.io/Lagrange.Doc/v1/Lagrange.OneBot/Config/)
- 对照：discord.py [`ext/commands/bot.py`（owner_id / owner_ids）](https://github.com/Rapptz/discord.py/blob/master/discord/ext/commands/bot.py)、
  [Telegram Bot API（无 owner，只有 chat member）](https://core.telegram.org/bots/api#getchatadministrators)

未能核实的点（已在正文避开断言）：NoneBot2 是否有官方插件禁用配置项；Koishi 的
"5 级管理员"仅出现在登录插件语境，官方权限准则定义的是 0–4。

项目内相关文档：

- 作用域存储与跨账号仲裁：[`multi-account-design.md`](multi-account-design.md)
- 配置键归宿与页面布局：[`../app/agent/config_partition.py`](../app/agent/config_partition.py)
- 模块能力声明与权限档位：[`MODULE_DEV.md`](MODULE_DEV.md)（注意 :132 处 `owner_id` 已过期，见 §1.4）
- 界面文案规范：[`UI_COPY.md`](UI_COPY.md)

