# 权限与回复：简单/高级双模设计

> 面向：框架维护者 / 产品设计。目标只有一句：**让"哪个群能聊天"是小白的一次点击，让"怎么回、谁能触发"是高级用户的一张可覆盖表，而两者写的是同一份数据。**
>
> 基线：`main@ce0a6d8`。现状结论均带 `文件:行号`。
> 相关文档：工件与就绪 → [`oneclick-bundle-and-onboarding-design.md`](oneclick-bundle-and-onboarding-design.md)；
> 身份与能力面板 → [`identity-and-capability-console.md`](identity-and-capability-console.md)；
> 执行清单 → [`plan/permission-dialogue.md`](plan/permission-dialogue.md)。

---

## 0. 一页结论

| 问题 | 结论 |
|---|---|
| 今天的"权限管理"是什么？ | **三套东西挤在一个词里**：身份档位（谁能用）、场所名单（哪些群/人）、能力开关（回不回）。而且**全部是账号级全局，没有任何群级配置**。 |
| "拉取群、直接勾哪个群能聊"能做到吗？ | **做不到**。群列表只在连接时拉一次、没有缓存表、没有刷新接口（`gateway.py:430-434`），界面上也没有群列表可勾。 |
| 根因 | 缺**场所维度（per-group）**的存储与解析。现有 `bindings` 的 `channel` 档位是预留的、运行时没人读；`channel_policies` 只解决"哪个账号回"，不解决"这个群回不回、怎么回"。 |
| 方案 | 三层语义（身份/场所/规则）× 两种视图（简单/高级）× **一份数据**（群级覆盖表）。简单视图只写 `enabled`；高级视图按"可覆盖键白名单"写 `config`/`permission`；两者共享继承链与"恢复继承"。 |
| 小白的路径 | 打开页面 → 点「拉取群列表」→ 列表里逐群开关（或全部开/关）→ 完成。默认值可见（"新群默认允许/不允许"）。 |
| 高级的路径 | 同上矩阵 → 点开某群 → 覆盖触发方式/回复方式/频控/静默/身份 → 看到"继承自：全局 → 已覆盖"→ 一键恢复继承。 |

---

## 1. 现状审计

### 1.1 四套"权限"入口 + 零个群级配置

| # | 入口 | 存储 | 维度 | 证据 |
|---|---|---|---|---|
| 1 | Agent → 权限管理：响应权限档位 `permission` | agent 配置（bindings account） | **账号** | `config_schema.py:501-512` |
| 2 | Agent → 权限管理：拥有者/管理者名册 | agent 配置 | 账号（应全局，见 identity 文档） | `config_schema.py:513-522` |
| 3 | Agent → 权限管理：名单范围（群/用户黑白名单） | agent 配置 `permission` 对象 | **账号**（名单是群号/QQ号列表） | `AgentPermissionSection.vue:18-28`、`config.py:480-514` |
| 4 | 模块详情 → 响应范围控制 | `bindings.permission` | 账号 × 模块 | `ModuleConfigPage.vue:467-477`、`authority.py:203-224` |
| 5 | 模块详情 → 响应权策略（同群多账号谁回） | `channel_policies` | 模块 × 群 | `ModuleConfigPage.vue:395-458`、`policy_store.py` |
| 6 | 顶栏 → 多群管理（只读，指路） | 无 | 群 | `MultiGroupDialog.vue:100-102` |

**关键事实：`group_enable` / `private_enable` / 触发方式 / 频控全是账号级全局**：

| 行为 | 位置 | 现状 |
|---|---|---|
| 群聊场景总闸 | `config.py:69-70`、`pipeline.py:316-320` | 一个布尔决定**所有群** |
| 触发方式（@/关键词） | `config.py:105-106`、`pipeline.py:283-295`、`domain/trigger.py:31-47` | 账号级，**不能"群 A 用 @、群 B 用关键词"** |
| 冷却/抖动 | `config.py`（`reply_cooldown` / `message_debounce_*`）、`pipeline.py` | 账号级 |
| 权限档位与名单 | `nodes/inbound.py:120-153`（AgentGate）、`:269-350`（模块） | 账号级 |

### 1.2 群列表：只有"连接时拉一次"，且无缓存

- `gateway.py:430-434` 在连接成功后调 `get_group_list`，把结果塞进 `conn.all_group_list_info`（内存）。
- `bot_service.get_bots_groups():211-223` 只返回**在线连接**的群列表；离线 = 空。
- 没有 `known_groups` 表、没有 `POST /api/groups/refresh`、没有群名/人数缓存。
- 于是"拉取群直接设置"这个动作，在今天的代码里**没有任何入口**。

### 1.3 由此产生的用户问题

1. 只想让机器人在两个群里说话：只能去填"白名单"，而白名单是**账号级**的，多账号时要填多份；
2. 想"群 A 只回 @"：做不到；
3. 想"这个群先静音一天"：没有临时静音，只能改全局或退群；
4. 关掉一个群之后，日志里只有一句"被跳过"，**不知道是被哪条规则拦下**（`nodes/inbound.py` 多处 `debug`，用户看不到）；
5. 小白在"权限管理"页看到档位、名册、黑白名单三个概念，**不知道从哪开始**。

---

## 2. 目标设计：三层语义 × 两种视图 × 一份数据

```
┌ 身份 Identity（谁）   全局 owner/manager 名册 + 群角色 + 群内用户例外
├ 场所 Scope（在哪）     群列表开关 / 私聊开关 / 新群默认 / 临时静音
└ 规则 Rules（怎么回）   触发方式 / 回复方式 / 频控 / 静默时段 / 最低身份档
```

**两种视图不是两套系统**：

| 视图 | 面向 | 能改什么 | 写入 |
|---|---|---|---|
| **简单**（默认） | 小白 | 每个群"能不能聊天"、私聊开关、批量 | `channel_overrides.enabled` |
| **高级** | 老手 | 每个群可覆盖的规则键 + 用户例外 + 频控/静默 | `channel_overrides.config/permission` |

- 高级视图里每个键都显示 **继承来源**（`默认 → 全局 → 账号 → 群`）与 **`恢复继承`** 按钮。
- 简单视图里每个群也显示"当前值来自哪层"（默认/已覆盖）。
- 两种视图共享同一条解析函数（`resolve_policy`），所以"简单里关掉"与"高级里看"永远一致。

**设计原则（对应你的两个诉求）**：

1. **小白只碰一个概念**："允许聊天的群"。其余全部有安全默认，不需要理解。
2. **高级不引入新概念**：所有可覆盖项都是**已有配置键的子集**（触发、冷却、档位…），不发明第二套参数名。
3. **可回退**：任何覆盖都能一键"恢复继承"；批量操作前显示"将影响 N 个群"。
4. **可解释**：任何"不回"都能在日志与页面上看到 `来源=群覆盖/账号/默认` 与原因。

---

## 3. 数据模型

### 3.1 新表

```sql
-- 群列表缓存（离线可看、可搜索、可判断"机器人还在不在群里"）
CREATE TABLE IF NOT EXISTS known_groups (
    account_id   TEXT NOT NULL,
    group_id     TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT '',
    member_count INTEGER NOT NULL DEFAULT 0,
    in_group     INTEGER NOT NULL DEFAULT 1,
    updated_at   INTEGER NOT NULL,
    PRIMARY KEY (account_id, group_id)
);

-- 群级覆盖（account × capability × channel）
CREATE TABLE IF NOT EXISTS channel_overrides (
    account_id      TEXT NOT NULL,
    capability      TEXT NOT NULL DEFAULT 'agent',   -- agent / 模块名
    channel_id      TEXT NOT NULL,                    -- 群号；'' 表示该账号的默认层
    enabled         INTEGER,                          -- NULL = 不覆盖（继承）
    config_json     TEXT NOT NULL DEFAULT '{}',       -- 只存"覆盖项"（白名单键）
    permission_json TEXT NOT NULL DEFAULT '{}',       -- role_min / user_mode / user_list
    updated_at      INTEGER NOT NULL,
    PRIMARY KEY (account_id, capability, channel_id)
);
```

> 为什么不用现有 `bindings` 的 `channel` 档？因为 bindings 的 channel 档**没有账号维度**（主键是 capability+scope），
> 而"账号 A 在群 X 允许、账号 B 在群 X 不允许"是真实需求。新表把 `account_id` 放进主键，语义不歧义，
> 也避免改动 `domain/binding.py`（它继续服务能力级配置）。**两者分工写进文档**：`bindings` = 能力配置，`channel_overrides` = 场所覆盖。

### 3.2 解析顺序（纯函数，可单测）

```
resolve_policy(account_id, capability, channel_id) -> ResolvedPolicy
  默认值   ← capability 自身配置（agent: runtime.config；模块: config + authority）
  ← 账号层 ← channel_overrides(account, capability, '')
  ← 群层   ← channel_overrides(account, capability, channel_id)
  最具体者胜；每个键记录 source
```

返回：

```python
@dataclass(frozen=True)
class ResolvedPolicy:
    capability: str
    account_id: str
    channel_id: str | None
    enabled: bool                 # 响应开关
    config: dict[str, Any]        # 触发/回复/频控/静默（可覆盖键）
    permission: dict[str, Any]    # role_min / user_mode / user_list
    sources: dict[str, str]       # key -> default|account|channel
```

### 3.3 可覆盖键白名单（`OVERRIDABLE_KEYS`）

**必须显式声明**（否则又会变成"任意键可覆盖、没人知道生效的是谁"）。v1 只放"按群不同很正常"的键：

| capability | 可覆盖键 | 说明 |
|---|---|---|
| `agent` | `trigger_at`、`trigger_keyword` | 触发方式（@ / 关键词 / 两者） |
| `agent` | `reply_cooldown`、`message_debounce_seconds`、`message_debounce_max_wait` | 频控与抖动 |
| `agent` | `interrupt_enable` | 是否允许打断 |
| `agent` | `outbound_directive_enable` | 是否允许引用/@ 输出（运行期确实在读：`delivery/directives.py:161`） |
| `agent` | `permission`（`role_min`）+ `user_mode`/`user_list` | 谁能触发/谁被排除 |
| `module:<name>` | 该模块 schema 中标注 `scope: "channel"` 的字段 | 由模块声明，未声明=只能账号级 |

**声明方式**：在 `config_partition.py` 旁新增 `SCOPE_OVERRIDABLE`（或模块 schema 字段加 `scope: "channel"`）。
未在名单里的键，写接口直接拒绝并给出"该键只能在账号级配置"。

> ⚠️ 开工前要顺手修一处文档/代码分叉：`outbound_directive_enable` 被 `config_partition.py:361-362` 列为**已删除**，
> 但运行期仍在读（`delivery/directives.py:161`、`assembly/prompt.py:246`、`persona/rules.py:118`）。
> 以代码为准把它算作"可覆盖键"，并在该提交里修正 `config_partition` 的归属（否则又一个"两处说法"）。

### 3.4 三个语义的具体字段

| 语义 | 字段 | 默认 | 小白能看到吗 |
|---|---|---|---|
| 场所 | `enabled`（群/私聊） | 继承 `group_enable` / `private_enable` | ✅（开关） |
| 触发 | `trigger_at`、`trigger_keyword` | 账号级配置 | 高级 |
| 回复 | `outbound_directive_enable`（引用/@）+ 模块输出键（v2） | 账号级配置 | 高级 |
| 频控 | `reply_cooldown`、`message_debounce_*`、`quiet_hours`（新增，见下） | 账号级配置 | 高级 |
| 身份 | `role_min`、`user_mode/user_list` + 全局名册 | `permission` 档位 | 高级（名册跳身份页） |

> `quiet_hours` 是本设计**唯一新增**的行为键（`"23:00-08:00"`，空=不启用）。它补的是一个真实缺口：
> "晚上别在群里说话"，今天只能关掉所有群。

### 3.5 临时静音（新手也会用的一条）

`channel_overrides` 增加两列（或写进 config）：

```sql
muted_until INTEGER   -- 0 = 未静音；时间戳 = 到点自动恢复
```

- 页面：群行右侧「静音 1 小时 / 24 小时 / 直到我恢复」；
- 聊天内：`#llm mute 2h`（群内发送，写同一行）；
- 解析：`enabled = enabled AND now >= muted_until`，并在 `sources["enabled"]="muted"`；
- 到点无需定时任务：读时比较时间即可（幂等、无后台状态）。

---

## 4. API 契约

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/groups?account_id=` | 缓存群列表 + `fetched_at` + `stale`（离线时 `stale=true`） |
| POST | `/api/groups/refresh?account_id=` | 调 OneBot `get_group_list` → upsert `known_groups`，返回 `{groups, fetched_at, error?}` |
| GET | `/api/policy?account_id=&capability=agent&channel_id=` | `{resolved, sources, overrides:{account, channel}}` |
| POST | `/api/policy` | 写一层覆盖：`{account_id, capability, channel_id?, patch:{enabled?, config?, permission?}, clear:[keys]}`；`channel_id` 省略 = 账号层 |
| POST | `/api/policy/bulk` | `{account_id, capability, channel_ids:[], enabled}`（全部开/关） |
| POST | `/api/policy/mute` | `{account_id, capability, channel_id, minutes}`（0=取消） |
| GET | `/api/policy/matrix?account_id=&capability=agent` | 列表页用：每群 `{group_id, name, member_count, enabled, muted_until, source, overridden_keys[]}` |
| GET | `/api/permission/preview?account_id=&channel_id=&user_id=` | 复用统一判定器：`{allowed, role, role_source, reason}` |

**错误语义**：写不可覆盖键 → 400 `POLICY_KEY_NOT_OVERRIDABLE`（附允许键列表）；写非法值 → 422 + 逐键原因；账号不在 → 404。

---

## 5. 后端接入（最小改动版）

### 5.1 事件入口解析一次

`AgentNode._route()` 与 `ModulePermissionNode.process()` 各解析一次 `ResolvedPolicy`，放进 `ctx.state["policy"]`：

```
event → account_id
      → channel_id（群聊=群号；私聊=None）
      → resolve_policy(account, "agent"|module, channel)
      → 后续所有 "回不回/怎么回/谁能触发" 都从 policy 读
```

### 5.2 替换点（6 处，全部有默认回落）

| 现状 | 改为 | 位置 |
|---|---|---|
| `config.get("group_enable"/"private_enable")` | `policy.enabled`（无覆盖时回落原值） | `pipeline.py:316-320` |
| `config.get("trigger_at"/"trigger_keyword")` | `policy.config[...]` | `pipeline.py:283-295`、`:184-191` |
| `reply_cooldown` / `message_debounce_*` | `policy.config[...]` | pipeline/会话相关 |
| `runtime.config.permission`（档位） | `policy.permission["role_min"]` | `nodes/inbound.py:120-153` |
| 名单（群/用户黑白） | `policy.permission` 的 `user_*`（群维度不再需要"群名单"，由 `enabled` 表达） | `authority.py:178-200` |
| 模块权限 | `resolve_policy(account, module, channel)` 的 permission | `authority.py:203-224` |

**日志必须升级**：所有跳过分支从 `debug` 改成"按账号+原因限流的 info/warn"，格式统一：

```
[skip] group=123456 account=acc1 reason=disabled_by_channel source=channel
[skip] reason=trigger_not_matched source=channel keys={trigger_at:true, keywords:[]}
```

这样"为什么不回"在日志页可过滤（配合就绪卡的"为什么不回"入口）。

### 5.3 群内指令（便携性）

| 指令 | 行为 |
|---|---|
| `#llm off` / `#llm on` | 写当前群的 `enabled`（需要 `manager` 及以上） |
| `#llm mute 2h` / `#llm unmute` | 写 `muted_until` |
| `#llm status` | 回复当前群生效的策略摘要（含来源） |

指令与页面写同一张表 → 不存在"指令改了但页面没变"。

---

## 6. 前端设计

### 6.1 页面与入口

- 路由：`/agent/permission` 改造成「权限与回复」，**两个 tab**：`群聊开关`（默认）/ `回复规则`。
- 侧栏与 Agent 概览卡片文案：`权限管理` → `权限与回复`；副标题「哪些群能聊、怎么回、谁能触发」。
- 账号级入口保留在 Agent 面板；模块的群级覆盖从「功能模块 → 模块 → 群范围」进入同一个组件（capability 切换）。

### 6.2 简单视图（默认）

```
┌ 权限与回复 ────────────────────────────────────────────────┐
│ [群聊开关]  回复规则                                          │
│                                                              │
│ 账号：主号 123456789 ▾        群列表：42 个 · 2 分钟前 [刷新] │
│ 🔍 搜索群名/群号            [全部允许]  [全部关闭]            │
│ ☑ 允许私聊                                                    │
│                                                              │
│ 允许聊天（默认：允许 / 已关闭 3 个）                          │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ ● 前端交流群      123456789   128 人    [开 ▾]  ⋮         │ │
│ │ ○ 测试群          987654321    12 人    [关 ▾]  ⋮         │ │
│ │ ● 摸鱼群          112233445    56 人    [开 ▾]  ⋮         │ │
│ └──────────────────────────────────────────────────────────┘ │
│  ⋮ 菜单：静音 1 小时 / 静音 24 小时 / 高级规则 / 恢复继承      │
│                                                              │
│ 群列表来自该账号在线时的拉取；离线时显示上次结果（可能过期）。 │
└──────────────────────────────────────────────────────────────┘
```

- 行内开关三态：`开` / `关` / `继承（默认允许/不允许）`——用下拉而不是纯 switch，避免"看起来关不掉"。
- 顶部显示"默认：允许/不允许"，并提供「新群默认」设置（写账号层 `enabled`）。
- 批量操作前弹确认："将把 42 个群设为不允许，是否继续？"。
- 离线态：横幅「账号未连接，显示 10 分钟前的群列表 · [连接]」。

### 6.3 高级视图

```
┌ 权限与回复 ────────────────────────────────────────────────┐
│ 群聊开关  [回复规则]                                          │
│                                                              │
│ 默认规则（继承到所有群）        [编辑]                        │
│   触发：@我    关键词：—      回复：允许引用/@                │
│   频控：冷却 3s  抖动 2s      静默：23:00–08:00               │
│   谁能触发：所有人 ▾          用户例外：3 人                   │
│                                                              │
│ 群级覆盖（3 个群有改动）                                      │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ 前端交流群   123456789                                    │ │
│ │  触发  [继承 ▾]  @我                                      │ │
│ │  回复  [覆盖]    允许引用/@                [恢复继承]      │ │
│ │  频控  [继承 ▾]  冷却 3s                                  │ │
│ │  静音  [未静音 ▾]                                         │ │
│ │  例外  黑名单 2 人                                         │ │
│ └──────────────────────────────────────────────────────────┘ │
│ ...                                                          │
│ 说明：留空 = 继承上一层；任何覆盖都能一键恢复。               │
└──────────────────────────────────────────────────────────────┘
```

- 每个键三态：`继承` / `覆盖` / `禁用（例如关闭该键的行为）`；`恢复继承` = 删除该键。
- 继承来源用 chip 显示：`默认` / `账号` / `群`。
- 名册相关只显示摘要 + 「去身份页管理」（避免本页再出现第二份 owner 列表——见 identity 文档）。

### 6.4 与「能力控制台」的关系

| 页面 | 视角 | 数据 |
|---|---|---|
| 能力控制台（identity 文档 §4.4） | 按**能力**：行=能力，列=账号 | `bindings` + 群开关摘要 |
| 权限与回复（本文） | 按**群**：行=群，列=规则 | `channel_overrides` |

两页都写同一份群级数据，且都显示继承来源 → 不存在"改了一处另一处不变"。

### 6.5 错误与空态

| 状态 | 展示 |
|---|---|
| 账号未连接 | 缓存列表 + "10 分钟前"+ [连接账号] |
| 群列表为空 | "该账号还没加入任何群" + [去 QQ 确认] |
| 拉取失败 | 原因（超时/对端不支持 get_group_list）+ [重试] |
| 写失败 | "没有保存（原因）" + 保留草稿 + [重试] |
| 权限不足 | 指令侧提示"需要管理者权限"，页面侧禁用按钮 + tooltip |

---

## 7. 迁移（旧行为必须逐字节等价）

| 旧配置 | 迁移为 | 备注 |
|---|---|---|
| `group_enable=false` | 账号层 `enabled=false` | 群聊全关 |
| `private_enable=false` | 账号层私聊开关 `enabled=false` | 私聊独立 |
| `group_mode=whitelist` + `group_list` | 账号层 `enabled=false` + 列出群 `enabled=true` | 等价 |
| `group_mode=blacklist` + `group_list` | 账号层 `enabled=true` + 列出群 `enabled=false` | 等价 |
| `user_mode/user_list` | 账号层 `permission.user_*` | 等价 |
| 模块 `permission` 名单 | v1 保留原逻辑；v2 迁 `channel_overrides(capability=module)` | 渐进 |
| `channel_policies`（谁响应） | **不动** | 与"回不回"正交 |

- 迁移幂等：`kv["channel_overrides_migrated"]="1"`；
- 旧字段保留只读一版，UI 不再写；写旧接口返回 `deprecated:true`；
- 迁移脚本先备份 `data/app.db`（现状**没有**配置/DB 备份脚本，需要新增一条 `scripts/backup_config.py`）。

---

## 8. 验收标准

**简单视图**

1. 在线账号点「刷新」→ 群列表出现真实群名/人数；
2. 关掉群 A → 在 A 里 @ 机器人不回复；日志出现 `[skip] reason=disabled_by_channel source=channel`；
3. 群 B 不受影响；
4. 「全部关闭」→ 所有群不回；「全部允许」→ 恢复；
5. 离线时页面显示上次列表并标注时间。

**高级视图**

6. 群 A 设为"仅关键词"→ 只有关键词消息进 LLM；群 B 仍按 @ 触发；
7. 给群 A 加用户黑名单 → 只有该群生效；
8. 任意覆盖点「恢复继承」→ 回到上一层值，来源变回 `默认/账号`；
9. 静音 1 小时 → 到点自动恢复（读时判断，无后台任务）；
10. 群内 `#llm off` 与页面开关双向同步（WS 广播）。

**兼容与稳定**

11. 旧 whitelist/blacklist 迁移后行为与迁移前一致（构造 4 组用例）；
12. 写入不可覆盖键被拒（400），非法值被拒（422）；
13. 迁移幂等（跑两次结果一致）；
14. 全量 `pytest -q` 全绿，`ruff check .` 无新增。

---

## 9. 分期（执行清单见 `plan/permission-dialogue.md`）

| 阶段 | 内容 | 规模 |
|---|---|---|
| **R0 群列表** | `known_groups` + 刷新 API + 页面「群列表」卡片（只读） | 1–2d |
| **R1 策略底座** | `channel_overrides` + `resolve_policy` + 只读 `/api/policy` | 2–3d |
| **R2 简单视图** | pipeline 接入 `enabled` + 矩阵开关 + 批量 + 私聊开关 + 日志升级 | 2–3d |
| **R3 高级视图** | 触发/回复/频控/静默/用户例外 + 继承 UI + 群内指令 | 3–4d |
| **R4 迁移收口** | 旧配置迁移 + 删除旧卡片 + 兼容读取一版 + 全量验收 | 2–3d |

**依赖**：R0 独立；R1→R2→R3 顺序；R4 依赖 R2/R3。
与其它计划的交点：R2 的"日志升级"与 P0 的静默修复是同一件事，**先做 P0 再做 R2**；
R3 的"环境/身份"与 identity 文档的 P1（全局名册）共用判定器，**名册先落地可少改一次**。

---

## 10. 明确不做

1. **不做"每群一套完整 Agent 配置"**（那就是被删的 UMO 路由；本文只覆盖白名单键）。
2. **不把 `channel_policies`（谁响应）合并进来**：它回答"多个账号谁回"，与本页"回不回/怎么回"正交，强行合并会让两件事互相污染。
3. **不在本页再放一份 owner/manager 名册**：身份只有一处（identity 文档）。
4. **不做云端同步的群配置**：v1 本地。
5. **不引入新的"规则 DSL"**：高级项就是已有配置键 + 继承，可覆盖键显式白名单，避免第二套参数体系。
