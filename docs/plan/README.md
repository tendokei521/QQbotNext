# 执行计划（总览）

> 本目录把两份设计稿落成**可勾选、可提交、可验收**的工作清单。
> 规则：**清单是活的** —— 每完成一个提交就在对应行补 `commit hash + 日期`，并把状态改成 `[x]`。
> 设计与理由不在本目录展开，详见：
> - 一键部署 / 整合包 / 首启 → [`../oneclick-bundle-and-onboarding-design.md`](../oneclick-bundle-and-onboarding-design.md)
> - 权限与回复（简单/高级双模） → [`../permission-dialogue-policy-design.md`](../permission-dialogue-policy-design.md)
> - 身份与能力面板 → [`../identity-and-capability-console.md`](../identity-and-capability-console.md)

---

## 1. 图例与状态

| 标记 | 含义 |
|---|---|
| `[ ]` | 未开始 |
| `[~]` | 进行中（同一时间只允许一个） |
| `[x]` | 已完成（**必须**有 commit hash） |
| `[!]` | 被阻塞（在该行写阻塞原因与阻塞项编号） |

状态板（更新时同步本表）：

| 阶段 | 主题 | 状态 | 清单 |
|---|---|---|---|
| **P0** | 止漏：静默、退出码、校验、发行链路 | `[x]` | [`onboarding-bundle.md`](onboarding-bundle.md#p0-止漏本周) |
| **P1** | 首启向导 + 就绪诊断 | `[x]` | [`onboarding-bundle.md`](onboarding-bundle.md#p1-首启向导--就绪诊断1-周) |
| **P2** | 套装工件 v1（格式/校验/导出/预检） | `[x]` | [`onboarding-bundle.md`](onboarding-bundle.md#p2-套装工件-v1只导出导入预检) |
| **P3** | 实例化 + 快照回滚 | `[x]` | [`onboarding-bundle.md`](onboarding-bundle.md#p3-实例化--快照回滚) |
| **P4** | 更新/卸载/值校验器 | `[ ]` | [`onboarding-bundle.md`](onboarding-bundle.md#p4-更新卸载值校验器) |
| **P5** | 协议端托管 + 免安装发行包 | `[ ]` | [`onboarding-bundle.md`](onboarding-bundle.md#p5-协议端托管--发行包) |
| **R0** | 群列表（缓存 + 刷新） | `[x]` | [`permission-dialogue.md`](permission-dialogue.md#r0-群列表) |
| **R1** | 策略底座（群级覆盖 + 解析） | `[x]` | [`permission-dialogue.md`](permission-dialogue.md#r1-策略底座) |
| **R2** | 简单视图（群开关矩阵） | `[x]` | [`permission-dialogue.md`](permission-dialogue.md#r2-简单视图) |
| **R3** | 高级视图（规则覆盖） | `[x]` | [`permission-dialogue.md`](permission-dialogue.md#r3-高级视图) |
| **R4** | 迁移收口 | `[x]` | [`permission-dialogue.md`](permission-dialogue.md#r4-迁移收口) |

### 已完成进度（2026-10-27）

| 编号 | commit | 一句话 |
|---|---|---|
| P0-1 | `1a555dd` | 场景开关关闭时留限流日志（不再静默 return） |
| P0-2 | `eeb8cf0` | 退出码 4 = Python 版本不符，不再误导去查镜像 |
| P0-3 | `97c0841` | 保存账号校验 ws_url（反向必须带端口） |
| P0-4 | `32aac8f` | Agent 配置坏请求体 400，不再"假成功"/500 |
| P0-5 | `f7f5cd1` | release 打包路径修正 |
| P0-6 | `53b3360` | wheel 打包 dashboard dist + 修 package-data 通配（实测：124 个 dist 文件进包） |
| P0-7 | `e9f1553` / `bbd79b7` | 干净构建替代 prune（dist 1077→122 文件 / 15MB→5MB） |
| P1-1 | `0ee9ada` | 就绪检查服务 + `GET /api/readiness` + `POST /api/setup/complete` |
| R0-1 | `21bac9b` | 群列表落库 + `GET /api/groups` + `POST /api/groups/refresh` |
| R1 | `b38a907` | `channel_overrides` + 纯函数解析 + `/api/policy*` |
| R2-1 | `1a2f8c6` | 群级开关接入回复门控（日志带 source） |
| R2-2/2-3 | `7252d47` | 「权限与回复」页（群矩阵 + 批量 + 静音 + 恢复继承） |
| R3-1/2/5 | `323867f` | 群级触发方式 / 免打扰时段 / 用户例外接入流水线 |
| R3-4 | `8d75ad0` | 群内指令 `#llm off/on/mute/status` |
| R3-3 | `2e52585` | 高级规则对话框（继承可见、留空=继承） |
| R4-1 | `ec26b82` | 旧账号级权限 → `channel_overrides` 迁移（场景层 + 群层） |
| R4-2 | `24fae90` | Agent 权限页收敛为入口；`sections.json` 副标题改由 config_partition 生成 |
| P0-8 | `5b98d50` | README 前置条件 / NapCat 三步反连 / 故障排查表；`.env` 节标题改名 |
| P1-3~P1-6 | `c0b99bd` | 首启向导（4 步）+ 总览就绪卡 + 侧栏入口 |
| P1-5 后端 | `7e9e0b6` | `/api/setup/quick-model`：建预设+模型+入池一次完成（幂等） |
| P1-7 | 已有 | 模块加载失败列表 + 单模块重载（`ModulesPage.vue`），就绪卡共用 |
| P2-1/P2-2 | `8f6a237` | 清单解析 + 七层校验（结构/兼容/依赖/引用/未知键/密钥/校验和）+ 导出器（密钥剥离、确定性字节） |
| P2-3 | `4b7c669` | `/api/bundles/validate|import|export|download|list`；agent schema 走 application 门面（守住 webui→agent 边界） |
| P2-4/P2-5 | `75f6b4c` | 「套装中心」页 + 导入预检对话框（含需填密钥提示）+ 从账号导出 |
| P3-1 | `a5ca2e8` | `ConfigService.apply_ops`：多对象配置单事务写（失败回滚内存） |
| P3-2/3/4/6 | `ac31476` | 激活/对象/当前套装三表 + `BundleApplier`（计划→快照→事务→校验）+ 回滚 + 启动恢复 |
| P3-5 | `20488d5` | 套装中心「应用到账号 / 应用记录 / 回滚」UI |
| 修复 | `c93de4f` / `39b9e1e` | 架构守卫：静默异常补日志、skip_log 移出 agent/loop |

---

## 2. 依赖关系

```
P0 ──┬─→ P1 ──→ P2 ──→ P3 ──→ P4 ──→ P5
     │                ↗
     └─→ R0 ──→ R1 ──→ R2 ──→ R3 ──→ R4
```

- **P0 是所有事情的前置**：它把"静默失败"变成诊断，否则后面每个功能出问题都会重新变成"没反应"。
- **R0 与 P1 可并行**（互不触碰文件）；**R1 依赖 R0 的群列表**（否则不知道有哪些群可配）。
- **P3 的 `apply_ops`** 与 **R1 的 `channel_overrides`** 都要改 `ConfigService`/`config_service.py` 附近——若并行，先约定谁先合入（建议 P3 先，R1 复用事务接口）。
- **R3 的"身份"部分** 依赖 identity 文档的 P1（全局名册）；若名册未落地，R3 先只做触发/回复/频控，身份留到名册之后。

---

## 3. Definition of Done（每个提交都适用）

1. **一个提交只做一件事**，消息用 Conventional Commits（`feat|fix|refactor|docs|build|test` + scope）。
2. `git add` 只加本步骤相关文件；工作区若有无关改动，不混入。
3. **可执行逻辑改动必须带测试**（`tests/test_*.py`，目录按现有结构）；
   纯文档/文案改动按 AGENTS.md 小改动档位，不强制跑测试。
4. 验证口径（AGENTS.md 分层）：
   - 单模块行为：`venv\Scripts\python.exe -m pytest tests/<相关> -q`
   - 跨层/框架级：全量 `pytest -q` + `ruff check .` 无新增
   - 前端改动：`npm run typecheck`（或 `build`）通过；**改了 `src/` 必须重建 `dist`** 并一起提交（否则用户看不到）。
5. 清单行补：`commit hash · YYYY-MM-DD · 验证命令`。
6. **不新增"同一语义两处配置"**：任何新键先写进 `config_partition.py` / `SCOPE_OVERRIDABLE`，再写实现。
7. 失败要可回滚：新增表/接口默认"空=旧行为"，不允许升级后行为漂移。

---

## 4. 工作流（单人或多 Agent 都适用）

1. `team_task`/清单里认领一个编号 → 标 `[~]`；
2. 读设计稿对应章节（每行都写了 `§` 或文件行号）；
3. 实现 → 跑验证 → `git commit`；
4. 回填 hash 与日期 → 标 `[x]`；
5. 若发现设计与代码不符：**先改设计稿再改代码**（避免第二次"文档分叉"）。

---

## 5. 当前建议起手式

P0 里风险最低、收益最直接的三项（半小时级）：

| 编号 | 提交 | 收益 |
|---|---|---|
| P0-1 | `fix(agent): log why group messages are skipped when group_enable is off` | 把"群里 @ 没反应"变成可查日志 |
| P0-2 | `fix(start): report python version mismatch distinctly from missing deps` | 不再让用户去查网络 |
| P0-3 | `fix(webui): validate ws_url on account save` | 账号配错当场报错 |

随后 P0-5/6（发行链路）需要真实构建验证，建议单独安排一个时段。
| P4-1 | `0b094b2` 值校验器接入人工保存（agent/module）+ 模块权限模式枚举拒绝 |
| P4-2 | `3d07002` `provider_model_pool` schema 类型对齐运行时（string→string_list）+ 套装值校验 |
| P4-5 | `9c2cb49` 删除预设时清理模型池 / 人格引用（`provider_model_pool` 死 id） |
