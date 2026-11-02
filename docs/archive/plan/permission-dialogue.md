# 计划 · 权限与回复（简单/高级双模）

> 设计依据：[`../permission-dialogue-policy-design.md`](../permission-dialogue-policy-design.md)
> 状态图例与 DoD 见 [`README.md`](README.md)。
> 一句话：**简单视图管"哪个群能聊"，高级视图管"怎么回、谁能触发"，两者写同一张 `channel_overrides` 表。**

**进度（2026-10-27）**：R0-1 `21bac9b` ✅ · R1 `b38a907` ✅ · R2-1 `1a2f8c6` ✅ ·
R2-2/2-3 `7252d47` ✅（页面 `/policy` + 批量 + 静音）· R3 `323867f` / `8d75ad0` / `2e52585` ✅
（触发/免打扰/用户例外 + 群内指令 + 高级规则对话框）· R4 `ec26b82` / `24fae90` ✅（迁移 + 旧卡片收敛）。
**本计划已全部完成**；模块级（`capability=模块`）的群级覆盖留作后续独立项（v2，见设计 §10.5）。

**R5 · 统一判定器（2026-10-30）** ✅：判定收敛为 `domain/response_access.py`（阶段/检查/决策）
+ `application/response_authority.py`（取值与仲裁），入站节点与 LLM 流水线消费同一份
`ResponseDecision`；`permission.role_min` 从"白名单死键"变为真正生效；跳过日志统一走
`log_skip`（含来源层与「去哪修」）；判定链预览 `GET /api/policy/decision`。
R5 不新增存储：prerequisite 仍是"一份数据"，只是把消费它的路径合并成一条。

---

## 前置约定（开工前先确认，避免返工）

| # | 约定 | 理由 |
|---|---|---|
| A | `known_groups` / `channel_overrides` 是新表，**旧表不动** | 可整体回退 |
| B | 解析函数 `resolve_policy()` 是**纯函数**（不读库），与 `domain/binding.py` 同风格 | 可单测、可给 UI 预览复用 |
| C | 可覆盖键必须进白名单 `SCOPE_OVERRIDABLE`，未声明 = 拒绝写入 | 防止"任意键可覆盖、没人知道生效的是谁" |
| D | 未覆盖时行为 = 现状（回落 `runtime.config`） | 升级零行为漂移 |
| E | 所有新增"跳过"分支必须有日志 + `source` | 与 P0-1 同一口径 |

---

## R0 群列表

> 目标：先让"能看到群"成立，不做任何行为改动。

| 编号 | 提交信息 | 主要改动 | 验证 | 完成判据 |
|---|---|---|---|---|
| **R0-1** | `feat(groups): persist known groups and expose refresh api` | `database.py` 加 `known_groups`；新增 `app/application/group_service.py`；`app/webui/api/groups.py`（`GET /api/groups`、`POST /api/groups/refresh`）；`gateway.py:430` 拉取成功后 upsert | `pytest tests/webui -q -k groups` | 在线刷新返回真实群名/人数；离线返回缓存 + `stale=true`；重复刷新幂等 |
| **R0-2** | `feat(webui): add group list panel with refresh and offline cache` | `stores/groups.ts`、`components/groups/GroupListPanel.vue`（先只读展示）、`dist` | typecheck + 手动：断开账号看缓存 | 能看到"42 个群 · 2 分钟前"；离线有横幅；拉取失败给原因与重试 |

**R0 验收**：不改变任何回复行为（跑一遍现有 `pytest tests/plugin tests/agent -q` 全绿）。

---

## R1 策略底座

> 目标：存储 + 解析 + 只读 API。**不接入运行时不改变行为。**

| 编号 | 提交信息 | 主要改动 | 验证 | 完成判据 |
|---|---|---|---|---|
| **R1-1** | `feat(policy): add channel overrides table and resolver` | `database.py` 加 `channel_overrides`；新增 `app/domain/dialogue_policy.py`（`ResolvedPolicy` + `resolve_policy` 纯函数）；`app/application/policy_service.py`（CRUD + 缓存失效）；`tests/domain/test_dialogue_policy.py` | `pytest tests/domain -q -k policy` | 覆盖顺序（默认→账号→群）正确；`sources` 每键可追溯；未覆盖时等于默认 |
| **R1-2** | `feat(policy): add readonly policy and matrix api` | `app/webui/api/policy.py`（`GET /api/policy`、`/api/policy/matrix`）；`tests/webui/test_policy_api.py` | `pytest tests/webui -q -k policy` | 返回 `resolved + sources + overrides`；矩阵含 `enabled/muted_until/overridden_keys` |
| **R1-3** | `feat(policy): add overridable key whitelist` | `app/agent/config_partition.py` 旁新增 `SCOPE_OVERRIDABLE`（或独立模块）；写接口拒收名单外键；`tests/webui/test_policy_api.py` 补用例 | 同上 | 写 `max_tokens` 之类返回 400 `POLICY_KEY_NOT_OVERRIDABLE` + 允许键列表 |

**R1 验收**：无行为变化；`resolve_policy` 有 ≥10 条单测（含多账号同群）。

---

## R2 简单视图

> 目标：小白的核心路径可用。**这是本计划的最小可用交付。**

| 编号 | 提交信息 | 主要改动 | 验证 | 完成判据 |
|---|---|---|---|---|
| **R2-1** | `feat(agent): honor channel enabled in the reply gate` | `app/agent/loop/pipeline.py:316-320` 改为读 `policy.enabled`（无覆盖回落原键）；`app/plugin/nodes/inbound.py` Agent 门控同步；日志带 `source`；`tests/agent/test_channel_gate.py` | `pytest tests/agent -q -k "channel or gate"` | 关掉群 A → A 不回、B 正常；日志 `[skip] reason=disabled_by_channel source=channel` |
| **R2-2** | `feat(webui): add group switch matrix and bulk endpoint` | `POST /api/policy/bulk`；`components/groups/GroupSwitchMatrix.vue`（三态下拉 + 搜索 + 批量确认）；私聊开关；`dist` | 手动 + `pytest tests/webui -q -k policy` | 逐群开关、全部开/关、私聊开关均即时生效；批量前确认影响数量 |
| **R2-3** | `feat(agent): add per-group mute` | `channel_overrides.muted_until`；`POST /api/policy/mute`；群行 ⋮ 菜单（1 小时/24 小时/取消）；解析时 `enabled = enabled and now >= muted_until` | 同上 | 静音到点自动恢复（无后台任务）；`sources["enabled"]="muted"` |

**R2 验收（写进 `docs/plan/acceptance/R2.md`）**：
1. 拉群 → 关群 A → A 不回、B 正常；2. 全部关/开可用；3. 离线看缓存；4. 静音自动恢复。

---

## R3 高级视图

> 目标：高级用户可按群覆盖规则，且**继承可见、可回退**。

| 编号 | 提交信息 | 主要改动 | 验证 | 完成判据 |
|---|---|---|---|---|
| **R3-1** | `feat(policy): enforce trigger and reply overrides` | `pipeline.py:283-295`（触发）、`:184-191`（打断判定）、`outbound_directive_enable` 读取改为 `policy.config`；`tests/agent/test_policy_overrides.py` | `pytest tests/agent -q -k policy` | 群 A 仅关键词、群 B 仅 @；互不影响；未覆盖群行为不变 |
| **R3-2** | `feat(policy): add frequency limits and quiet hours` | 新增 `quiet_hours`（`"23:00-08:00"`，空=不启用）+ 冷却读 `policy.config`；`tests/agent/test_quiet_hours.py` | 同上 | 静默时段内不主动回复；跨零点正确；非法格式拒绝保存 |
| **R3-3** | `feat(webui): add advanced rules view with inheritance` | `components/policy/PolicyRulesView.vue`（每键三态 + 继承 chip + 恢复继承）；`stores/policy.ts`；`dist` | 手动 + typecheck | 任意覆盖可"恢复继承"；来源显示 默认/账号/群 |
| **R3-4** | `feat(agent): add in-chat toggles` | `#llm off/on`、`#llm mute 2h`、`#llm status`（`command_dispatch.py` + `commands.py` 帮助文案）；权限 `manager` 起；`tests/agent/test_llm_help.py` 或新文件 | 同上 | 指令与页面双向同步（WS 广播 `policy_updated`） |
| **R3-5** | `feat(policy): add per-group user exceptions` | `permission.user_mode/user_list` 按群生效（`authority.py` 判定链读 `policy.permission`）；`tests/plugin/test_authority.py` 补用例 | 同上 | 群 A 黑名单里的人只在 A 被拦；群 B 正常 |

---

## R4 迁移收口

| 编号 | 提交信息 | 主要改动 | 验证 | 完成判据 |
|---|---|---|---|---|
| **R4-1** | `feat(policy): migrate legacy scene switches and group lists` | 迁移脚本（`scripts/migrate_dialogue_policy.py`）：`group_enable`/`private_enable`/`group_mode+group_list`/`user_*` → `channel_overrides`；`kv["channel_overrides_migrated"]="1"`；先备份 `data/app.db`（新增 `scripts/backup_config.py`） | `pytest tests/application -q -k migrate` | 迁移幂等；4 组用例行为与迁移前等价 |
| **R4-2** | `refactor(webui): replace legacy permission cards with links` | 删除 `AgentPermissionSection.vue` 的名单卡片、`ModuleConfigPage` 的"响应范围控制"卡片（改为链接到新页，capability 预选）；`sections.json` 文案改「权限与回复」；`dist` | 手动 + 契约测试（`tests/agent/test_agent_section_contract.py`） | 旧卡片不再出现；新页覆盖同等能力 |
| **R4-3** | `docs: document permission and reply rules` | 更新 `docs/MODULE_DEV.md`（模块如何声明 `scope: "channel"` 字段）、`docs/UI_COPY.md` 新增文案、`docs/permission-dialogue-policy-design.md` 状态 | 纯文档 | 文档无"两处说法" |
| **R4-4** | `test(policy): add equivalence and regression suite` | `tests/policy/`（新建目录）：等价性 10 例、边界（跨零点/多账号同群/群退群）、并发写 | `pytest tests/policy -q` | 全绿；全量 `pytest -q` + `ruff` 无新增 |

---

## 风险与回滚

| 风险 | 触发 | 回滚 |
|---|---|---|
| 解析层改动导致"全部不回" | R2-1 接入错误 | `kv.channel_overrides_disabled=1` 一键回落到旧读取（代码保留开关一版） |
| 迁移把黑白名单迁反 | R4-1 | 迁移前自动备份 DB；迁移写入前 dry-run 打印 diff；保留旧字段只读一版 |
| 日志过多 | R2-1/R3-1 | 统一限流器（同账号+同原因 60s） |
| 前端旧卡片删除后用户找不到入口 | R4-2 | 旧路由/旧组件保留一个版本的重定向；发布说明写清 |
| 队列顺序与 P 系列冲突 | P3 `apply_ops` 与 R1 同改 `config_service.py` | 先合 P3，R1 复用其事务接口；否则改文件级先后 |

---

## 与其它计划的交点

- **P0-1**（静默日志）和 **R2-1** 是同一套日志口径 → P0 先做，R2 复用限流器。
- **identity 文档 P1**（全局名册）与 **R3-5**（群内用户例外）共用判定器 → 名册先落地可少改一次。
- **P1-5**（向导模型步）与 **P2** 无关，但与 **R0** 可并行（不同文件）。

---

## 补记（2026-10-27）：账号默认入口 + 账号数据生命周期

R0–R4 上线后发现两个缺口，已补（均为额外提交）：

| 缺口 | 表现 | 补法 |
|---|---|---|
| 「继承」值没有改的入口 | 群行选「继承」后，用户找不到"账号默认允许/不允许"在哪改；实际值来自 Agent 配置的 `group_enable`（迁移过的账号则被固化成场景层） | 简单视图顶部加「账号默认（群聊）」三态：允许 / **不允许** / 跟随 Agent 配置。前两者写场景层 `__group__`，后者删掉覆盖回落到 Agent 配置（`POST /api/policy/scene`） |
| 改 id / 删号只动了 `bots` 表 | `channel_overrides` / `known_groups` / `channel_policies` / 预设激活 / 登录快照留在旧 id 下：新 id 页面全变「继承」，旧 id 数据成为孤儿 | `ConfigService.rename_account()` 一次搬全；`delete_bot()` 清干净（响应权只置空）；启动时清理历史孤儿并备份到 `data/orphan-account-data.json`（见 `docs/multi-account-design.md` §4.1） |
