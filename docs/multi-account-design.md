# 多账号模块设计：作用域绑定 + 响应权仲裁

> **状态**：P0（语义闭环）+ P1-a/b（bindings 存储、channel_policies 迁移、WebUI API）已落地；
> P1-c（前端策略卡片）进行中。本文是多账号模块层的**准一来源**（target design），
> 与 `MODULE_DEV.md`（插件作者视角）、`ARCHITECTURE.md`（目录/分层）分工不同。
> **范围**：账号 × 模块的配置/开关/权限归属、跨账号的群响应权仲裁、生命周期门控。
> 不涉及：LLM Agent 内部、接入层协议、WebUI 视觉改版。
>
> **实现落点**（与下文对应）：纯解析 `app/domain/binding.py`；纯仲裁 `app/plugin/responder.py`；
> 存储/迁移 `app/infrastructure/config/binding_store.py`（`ConfigService` mixin）；
> 候选集合与决策 `app/application/responder_service.py`；策略适配 `app/application/policy_store.py`；
> HTTP `app/webui/api/responder.py`。

---

## 1. 问题：三个作用域被压进两个布尔值

现状（重构前）：

| 事实 | 现状存储 | 真实维度 |
|---|---|---|
| 模块启停 | `module_authority(module, bot_id)` | **账号 × 模块** |
| 模块配置 | `module_config(module, bot_id)` | **账号 × 模块** |
| 模块权限（黑白名单/档位） | `module_authority(module, bot_id)` | **账号 × 模块** |
| 「单一服务」开关 | `webui_config.single_service[module]` | **模块 × 群**（响应权） |
| 服务账号指定 | `webui_config.multi_group.groups[gid].service_bot_id` | **群 × 模块** |
| UI 偏好 | `webui_config`（与运行策略同表） | **全局** |

由此产生三类缺陷：

1. **语义错位**：`single_service` 在账号的模块页呈现（用户以为是账号级），存的却是全局值，
   判断依赖群级映射。要表达的其实是「同一群里只让一个账号响应」。
2. **判定不闭环**：`is_single_service_skipped()` 只查「在线连接数 ≥ 2 + 群级服务账号」，
   不校验服务账号该模块是否启用、是否在群、权限是否通过 → **全群静默**（黑洞）；
   未指定服务账号时 → **双回**。
3. **开关不完整**：`enabled` 只拦事件派发，不拦 `notify_online()` 的定时器武装与 `on_online()`，
   禁用模块在账号重连后仍会跑后台任务。

根因：**账号级开关、模块级配置、群级响应策略混在「一个全局字典 + 一张账号表」里**，
没有一个统一的「作用域解析」层，也没有一个可以看多个账号状态的仲裁者。

---

## 2. 设计公理

| # | 公理 | 落地约束 |
|---|---|---|
| A1 | 一个事实只有一个作用域 | 所有运行期配置必须能回答 `global / account / channel` 之一；解析结果带来源 |
| A2 | 开关即契约 | `enabled=false` 必须同时拦住：事件派发、定时任务、主动消息、工具暴露、生命周期武装 |
| A3 | 跨账号决策单点集中 | 需要读其它账号状态的判断只能发生在 `ResponderService`；per-account 节点不得扫全量连接 |
| A4 | 声明制 | 能力是否参与仲裁由 `BaseModule.supports_single_service`（兼容名）声明；未声明者 UI 不给策略 |
| A5 | 失败可见且安全 | 仲裁失败显式 fallback（默认 fail-open）+ 结构化 reason + 一次性告警；禁止静默 |

**「轻量」的定义**：概念正交、每层可独立推理、失败可回退——不是文件少。

---

## 3. 概念模型

```
Account（身份/凭据/连接）  ──1:1──  Transport（OneBot 正/反向 WS）
        │
        ▼
Binding（能力 × 作用域 → enabled/config/permission）
        │
        ▼
Capability（模块：无状态或按账号上下文；声明 config/events/hooks）
        ▲
        │ 参与仲裁的候选账号集合
Channel（群/会话：成员账号、响应权策略）──  ResponderService（唯一跨账号仲裁者）
```

- **Binding**：`(capability_id, scope_type, scope_id)` 唯一；`scope_type ∈ {global, account, channel}`。
  解析顺序 global → account → channel，**最具体者胜**，逐键合并。
- **ChannelPolicy**：`(capability_id, channel_id)` → `policy / primary_account_id / fallback`。
  与 Binding 分开存：Binding 回答「这个账号能不能用」，Policy 回答「这个群里谁响应」。
- **ResponderService**：唯一允许同时看 registry + gateway + binding 的地方；输出 `ResponderDecision`。

### 3.1 作用域矩阵（准一来源）

| 配置 | 维度 | 存储 |
|---|---|---|
| 能力启停 / 权限 / 业务配置 | 能力 × 账号（可被全局默认覆盖） | `bindings` |
| 响应权策略（all/dedupe/delegate/leader） | 能力 × 群 | `channel_policies` |
| 指定服务账号 / 主备顺序 | 能力 × 群 | `channel_policies.primary_account_id` |
| Agent 启停 / 人设 / 名册 | 账号 | `bindings(capability="agent", scope=account)` |
| Provider 预设 / 模型 / 角色预设 | 全局 | `provider_*` / `role_presets` |
| UI 偏好（日志、折叠、列宽） | 全局（前端约定） | `webui_config`（仅 UI） |

---

## 4. 数据模型

```sql
-- 能力绑定：取代 module_config + module_authority 的运行时读写
CREATE TABLE IF NOT EXISTS bindings (
    capability_id TEXT NOT NULL,              -- 模块名 / 虚拟能力名（"agent"）
    scope_type    TEXT NOT NULL,              -- 'global' | 'account' | 'channel'
    scope_id      TEXT NOT NULL DEFAULT '',   -- global='' ; account=account_id ; channel=group_id
    enabled       INTEGER,                    -- NULL = 不覆盖（沿用更宽作用域）
    config_json   TEXT NOT NULL DEFAULT '{}',
    permission_json TEXT NOT NULL DEFAULT '{}',
    priority      INTEGER NOT NULL DEFAULT 100,
    updated_at    INTEGER NOT NULL,
    PRIMARY KEY (capability_id, scope_type, scope_id)
);

-- 响应权策略：取代 webui.single_service + multi_group.groups[*].service_bot_id
CREATE TABLE IF NOT EXISTS channel_policies (
    capability_id     TEXT NOT NULL,
    channel_id        TEXT NOT NULL,          -- 群号
    policy            TEXT NOT NULL DEFAULT 'all',   -- all | dedupe | delegate | leader
    primary_account_id TEXT,
    fallback          TEXT NOT NULL DEFAULT 'open',  -- open | closed
    updated_at        INTEGER NOT NULL,
    PRIMARY KEY (capability_id, channel_id)
);
```

**保留** `module_config` / `module_authority` 作为**迁移源**与回滚快照，迁移后不再写入；
旧 JSON（`data/module/configs/*`）仍由 `_migrate_legacy` 读取，再转 bindings。

**迁移规则**（幂等，写入 `kv["bindings_migrated"] = "1"`）：

1. `module_authority(module, bot_id≠NULL)` → `bindings(scope=account, scope_id=bot_id)`；
   `bot_id=NULL` → `bindings(scope=global, scope_id='')`。
2. `module_config(module, bot_id)` → 同名 bindings 行的 `config_json`（与 1 合并）。
3. `webui.single_service[m] == true`：
   - 对每个 `multi_group.groups[gid]` 且存在 `service_bot_id` → `channel_policies(policy='delegate', primary=service_bot_id, fallback='open')`；
   - 无 `service_bot_id` 的群 → 不写（保持旧行为：不过滤 = `all`）。
4. 迁移后 `single_service` / `multi_group` 键**保留但只读**（供回滚/兼容 API 查询），UI 不再写。

### 4.1 账号作用域数据的生命周期（改名 / 删号 / 孤儿）

账号 id 是 `bindings(account)` / `channel_overrides` / `known_groups` /
`channel_policies.primary_account_id` / `account_active_bundle` / `bot_accounts` 的主键。
**只改 `bots` 表是不够的**：

| 操作 | 必须做什么 | 实现 |
|---|---|---|
| 账号 id 改名 | 上表全部行从旧 id 搬到新 id；`channel_policies.primary_account_id` 跟着改；`modules_default_enabled` 保留 | `ConfigService.rename_account()` |
| 删号 | 上表行全删；`channel_policies.primary_account_id` 置空（群级策略保留，回落到 fallback） | `ConfigService.delete_bot()` |
| 历史孤儿 | 启动时清理「`channel_overrides` / `known_groups` / `account_active_bundle` 里存在、`bots` 里没有」的 id；清理前备份到 `data/orphan-account-data.json` | `purge_orphan_account_data()`（`init()` 末尾） |

> 孤儿清理**不动** `bindings` 与 `bot_accounts`：旧版模块配置以 QQ 号为 key，登录快照也可能
> 先于账号存在——宁可少删，不误删。

---

## 5. 解析算法

```python
# app/domain/binding.py（纯函数，无 IO）
resolve(capability_id, account_id, channel_id) -> ResolvedBinding:
    candidates = [b for b in bindings(capability_id) if b.scope 匹配 (account_id, channel_id)]
    candidates.sort(key=scope_order)              # global < account < channel
    cfg, perm, sources = {}, {}, {}
    enabled, enabled_source = default_enabled, "default"
    for b in candidates:
        if b.enabled is not None:
            enabled, enabled_source = b.enabled, b.scope_type
        for k, v in b.config.items():    cfg[k], sources[k] = v, b.scope_type
        for k, v in b.permission.items(): perm[k] = v
    return ResolvedBinding(...)
```

**禁止**在解析器里读数据库/网关；它只接收「该能力的全部 binding」，由 `BindingService`
从 `ConfigService` 取快照。这样解析逻辑可以 100% 单测，UI 也能复用同一份 `sources` 做继承链展示。

---

## 6. 响应权仲裁

### 6.1 候选集合（唯一事实来源）

对某能力 + 某事件，候选账号 = 满足全部条件的账号：

```
已装配该能力实例 ∧ resolve(account, group).enabled ∧ 连接在线 ∧ 在群 ∧ 权限通过（模块侧）
```

「两个账号都开启模块」这个用户预期**自然包含在候选集合里**：关掉模块的账号不在候选，
既不会响应，也不会被当成「服务账号不可用」的唯一选项。

### 6.2 策略

| policy | 行为 | 典型用途 |
|---|---|---|
| `all`（默认） | 候选全部响应，账号各管各的 | 日志、统计、只读能力 |
| `dedupe` | 候选 ≥2 时选一个（稳定顺序：配置顺序 → account_id） | 同群只回一次 |
| `delegate` | 固定 primary；primary 不可用按 fallback 处理 | 专人负责某能力 |
| `leader` | primary 优先，不可用自动由次选接管（含 fallback） | 主备多账号 |

### 6.3 fallback（失败必须可见）

| 情况 | fallback=open（默认） | fallback=closed |
|---|---|---|
| primary 未配置 | 降级为 `dedupe` 选第一个 + `degraded=True` | 不响应 + `degraded=True` |
| primary 不在候选 | 降级为 `dedupe` 选第一个 + 告警 | 不响应 + 告警 |
| 候选为空 | 不响应（无候选属正常态，不告警） | 同左 |

- 默认 **open**：功能不会因为一个账号掉线而静默消失；
- 首次降级/失效时 WARNING（同 `(capability, channel, reason)` 去重），并计入决策记录；
- `delegate + closed` 只有显式配置才允许全静默——这是配置者的选择，不是默认行为。

### 6.4 决策结构（可观测）

```python
@dataclass(frozen=True)
class ResponderDecision:
    capability_id: str
    channel_id: str | None
    policy: str
    candidates: tuple[str, ...]
    responders: tuple[str, ...]      # 最终允许响应的账号
    reason: str                      # no-candidates / all / dedupe / primary / primary-unavailable:fallback-open ...
    degraded: bool
```

- 决策写入事件上下文与 DEBUG 日志；`reason` 结构化，供 WebUI「本群由谁响应/为什么」预览；
- 决策**不做长期缓存**（依赖事件用户权限），但计算成本是 O(账号数) 的字典查询，
  且不再 `deepcopy` 全量 webui 配置；后续如有需要再加「按事件角色分键」的短 TTL 缓存。

### 6.5 与节点链的关系

```
router(100) → permission(110) → command(115) → invoke(120) → agent(130)
                 │
                 ├─ 1. binding.enabled（账号级，取代 authority.enabled 的直读）
                 ├─ 2. ResponderService.decide(capability, event)
                 │      候选集合里包含「其它账号是否启用/在线/在群/权限」
                 └─ 3. 权限档位 + 黑白名单（原有逻辑）
```

- `ModulePermissionNode` 与 `AgentNode` 共用同一个仲裁器；Agent 的候选来自
  `agent_manager.runtimes()`，模块的候选来自 `ModuleRegistry`；
- **统一判定链（2026-10-30）**：节点不再各自先判"能不能聊"再判"谁回"，而是由
  `ResponseAuthority.decide_agent/decide_module` 一次算出 `ResponseDecision`，
  仲裁结果作为其中的 `responder` 阶段（`policy/responders/candidates/degraded` 一并带出）。
  入站节点执行 `INBOUND_STAGES`、流水线执行 `PIPELINE_STAGES`，两处共用同一个决策对象；
  `GET /api/policy/decision` 返回同一条链，页面所见 == 运行时所见。
- 被仲裁拦下写限流 `log_skip`（`reason=responder_skipped`/`no_candidates` + 候选与策略），
  不再静默；降级仍由 `ResponderService` 按 `(能力, 群, 原因)` 限频 WARNING。
- **不要**把 `channel_policies` 合并进 `channel_overrides`：前者回答"这个群由谁响应"，
  后者回答"这个群能不能聊/怎么回"，合表会让多账号选举与场所开关互相约束。

---

## 7. 生命周期门控

| 触发 | 行为 |
|---|---|
| 账号上线 | 只对 `resolve(account).enabled` 为真的实例调用 `on_online()` 并注册定时任务 |
| 账号离线 | 对称地停止/注销；已禁用的实例不重复调用 |
| 运行期启用 | 注册定时任务 + `on_load` + （账号在线时）`on_online` |
| 运行期禁用 | `on_unload` + 取消后台任务 + 注销定时任务 |
| 动态每日任务 | `register_daily_schedule()` 前置检查 binding enabled |
| 调度器纵深防御 | `SchedulerService.register_module()` 再查一次 enabled |

原则：**框架负责不武装禁区，模块不重复实现 `enabled` 判断**。

---

## 8. API 设计

### 8.1 新接口（前端只写新接口）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/modules/{module}/responder` | 声明 + 每个群的策略 + 账号候选 + 当前预览 |
| POST | `/api/modules/{module}/responder` | 整值替换策略列表（只接受声明支持的能力） |
| GET | `/api/modules/{module}/binding?account_id=` | 有效配置 + 继承来源（P1） |
| POST | `/api/module/{module}/binding` | 写账号级 binding（P1，替代 authority/config 写入） |

响应示例：

```json
{
  "module": "what_to_eat",
  "declared": true,
  "groups": [
    {"group_id": "123", "name": "测试群", "accounts": ["acc1", "acc2"],
     "policy": "delegate", "primary_account_id": "acc1", "fallback": "open",
     "preview": {"responders": ["acc1"], "reason": "primary", "degraded": false}}
  ]
}
```

### 8.2 兼容层

- `GET /api/modules` 的 `single_service` 字段保留（= 是否声明），前端不再用它存开关；
- `POST /api/webui/single-service` 保留一个版本：写 `channel_policies`（delegate/open），
  响应带 `deprecated: true`；`MODULE_DEV.md` 标注迁移；
- 旧 `multi_group.groups[*].service_bot_id` 只读迁移，不再写。

---

## 9. UI 信息架构

- **模块配置页**：删掉「单一服务模式」总开关卡片，改为「响应权策略」卡片：
  每群一行（群名/账号数/策略下拉/主账号下拉/fallback 开关/当前响应者预览）；
  未声明的模块不显示。
- **账号页**：模块开关仍是账号级（不变），但提示「该账号在哪些群受响应权策略约束」。
- **能力视图**（后续）：全局查看某能力在哪些账号/群启用、谁在响应。
- 所有影响跨账号的配置必须注明「对所有账号生效」；写操作后 WS 广播统一 `account_id`/`channel_id`。

---

## 10. 分阶段计划（每步独立提交 + 验证）

| 阶段 | 内容 | 验证 |
|---|---|---|
| **P0-a** | 生命周期门控（registry/scheduler/daily schedule） | `tests/plugin/test_lifecycle_gate.py` + 既有 registry 测试 |
| **P0-b** | 纯 `ResponderElection` + `ResponderService`，接入 PermissionNode/AgentNode；旧 `single_service` 配置即时映射（不迁表） | `tests/plugin/test_responder*.py`、`tests/plugin/test_dispatcher.py` 更新 |
| **P1-a** | `bindings` 表 + `BindingResolver` + `BindingService` + 迁移；ConfigService 读写切换 | `tests/application/test_binding_*.py`、迁移测试、全量 |
| **P1-b** | `channel_policies` 表 + `single_service/multi_group` 迁移 + 兼容 API | 迁移幂等测试、WebUI API 测试 |
| **P1-c** | WebUI responder API + 前端卡片 + store；旧接口标记 deprecated | `tests/webui/test_responder_api.py`、前端 build/typecheck |
| **收尾** | 文档同步（MODULE_DEV/api-contract/README）、全量 pytest + ruff | 全量 2500+ 测试全绿，ruff 无新增 |

**回滚**：P1 之后旧表就是快照；出问题可让 `kv.bindings_migrated` 回退并恢复旧读写路径
（代码保留 `LEGACY_CONFIG_WRITE=1` 开关，仅过渡期）。

---

## 11. 明确不做

- 不做「能力单例 + AccountContext」的实例模型重构（P2，独立项目；插件 API 影响面大）；
- 不做跨平台（非 OneBot）接入抽象；
- 不做响应权决策的持久化历史（只留运行期决策记录 + 日志）；
- 不把 UI 偏好从 `webui_config` 一次性搬空（只搬运行策略）。

---

## 12. 验收标准

1. 两个账号同群、都启用某能力、未配置策略 → 默认 `all`（各自响应，不静默）；
2. 配置 `dedupe` → 恰好一个账号响应，另一个 DEBUG 有 `reason=dedupe`；
3. 配置 `delegate(primary=已删/关模块/离线)` + fallback=open → 仍有一个账号响应，WARNING 一次，`degraded=true`；
4. `delegate + closed` → 全静默但 `GET responder` 预览明确显示 `degraded/reason`；
5. 禁用模块 → 账号重连后不执行 `on_online`、不注册定时任务；
6. `GET /api/modules` 仍返回账号级 `enabled`；旧配置自动迁移且行为不变；
7. 全量 pytest 全绿，ruff 无新增。
