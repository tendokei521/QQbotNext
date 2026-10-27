# 计划 · 一键部署 / 首启向导 / 套装工件

> 设计依据：[`../oneclick-bundle-and-onboarding-design.md`](../oneclick-bundle-and-onboarding-design.md)
> 状态图例与 DoD 见 [`README.md`](README.md)。
> 每行格式：**编号 · 提交信息 · 主要改动 · 验证 · 完成判据**。

---

## P0 止漏（本周）

> 目标：**把"没反应"变成"可诊断"**，并把发行链路的三个硬伤修掉。不新增功能。

| 编号 | 提交信息 | 主要改动 | 验证 | 完成判据 |
|---|---|---|---|---|
| **P0-1** | `fix(agent): log why group messages are skipped when group_enable is off` | `app/agent/loop/pipeline.py`（`group_enable`/`private_enable` 两个 return 前加日志）；新增 `tests/agent/test_pipeline_skip_log.py` | `pytest tests/agent -q -k "pipeline or skip"` | 群回复关闭时**每个账号 60s 最多一条** WARN，内容含「群号 / 账号 / 原因 / 去 Agent→基础配置 打开」 |
| **P0-2** | `fix(start): report python version mismatch distinctly from missing deps` | `start.py`（`check_dependencies` 保留原始 rc，`EXIT_PYTHON` 分支单独文案）；`tests/architecture/test_start.py` 补用例 | `pytest tests/architecture/test_start.py -q` | 退出码 3 输出「Python 版本不符（需要 3.10+）」而不再提示查镜像 |
| **P0-3** | `fix(webui): validate ws_url on account save` | `app/application/bot_service.py`（复用 `onebot/endpoint.py:parse_endpoint`）+ `app/webui/api/bots.py` 透传 400/原因；`tests/webui/test_webui_bots_config_save.py` | `pytest tests/webui -q -k bots` | 非法地址保存返回 400 + 原文原因；合法保持原行为 |
| **P0-4** | `fix(webui): reject malformed agent config payloads` | `app/webui/api/agent.py`（JSON 解析失败返回 400；`config` 非 dict 返回 400）；`tests/webui/test_agent_api_service_handles.py` 或新文件 | `pytest tests/webui -q -k agent` | 坏请求体不再"返回成功"、不再 500 |
| **P0-5** | `fix(build): correct dashboard dist path in release workflow` | `.github/workflows/release.yml:55`（`-C app/webui/dashboard dist`） | 本地 `tar -czf /tmp/x.tgz -C app/webui/dashboard dist` 成功 | release 的打包 step 能产出 tarball |
| **P0-6** | `build: package dashboard dist into the wheel` | `pyproject.toml`（`package-data` 加 `app/webui/dashboard/dist/**`，或显式声明不含并在启动时报错）；`MANIFEST.in`（如需） | `python -m build` 后在临时 venv `pip install dist/*.whl`，启动访问 `/` 不出现"前端未构建" | wheel 安装后 WebUI 正常 |
| **P0-7** | `build(webui): replace prune_dist with clean rebuild` | `app/webui/dashboard/vite.config.ts`（`emptyOutDir: true` 或固定 build 目录）；`scripts/prune_dist.py` 标废弃；`.github/workflows/ci.yml` / `release.yml` 接线 | 连续两次 build 后 `dist` 文件数不增长；`python scripts/prune_dist.py --check` 不再有可删项 | 不再需要手动 prune，且懒加载 chunk 不丢 |
| **P0-8** | `docs: add prerequisites, napcat reverse-ws steps and troubleshooting` | `README.md`（前置：Python 3.10–3.13 勾 PATH / NapCat 三步 / 故障排查链接）；`MAINTENANCE.md` §12；`app/core/env_template.py` 的「Bot 连接」节标题 | 纯文档，抽查命令可执行 | 新手按 README 能走完到"进程起来" |

**P0 收尾**：全量 `pytest -q` + `ruff check .` 无新增；`dist` 重建并提交（若动了前端）。

---

## P1 首启向导 + 就绪诊断（1 周）

| 编号 | 提交信息 | 主要改动 | 验证 | 完成判据 |
|---|---|---|---|---|
| **P1-1** | `feat(readiness): add readiness service and api` | 新增 `app/application/readiness_service.py`；`app/webui/api/readiness.py`；`deps.py` 注入；`tests/webui/test_readiness_api.py` | `pytest tests/webui -q -k readiness` | `GET /api/readiness` 返回 §6.5 的 7 项检查，每项带 `action` |
| **P1-2** | `feat(startup): print readiness checklist after launch` | `app/bootstrap.py`（启动完成后打印同一份检查）+ `start.py`（转发/着色） | 手动启动观察输出 | 启动日志出现「无账号 / 无模型 / 群回复未开启 / 3001 未监听」中命中的项 |
| **P1-3** | `feat(webui): add readiness card to overview` | `stores/readiness.ts`、`components/readiness/*`、`views/OverviewPage.vue`、`dist` 重建 | 前端 typecheck + 手动：断开账号看 blocker 跳转 | 总览顶部卡片能实时反映就绪状态，每条 blocker 可点去修 |
| **P1-4** | `feat(setup): add wizard shell and connection step` | 路由 `/setup` + `views/SetupPage.vue` + `components/setup/SetupStepConnection.vue`；`GET /api/setup/state`、`POST /api/setup/test-connection` | 手动走 step1 | 未完成首启时进入即向导；可"稍后再配"；测试连接给明确结果 |
| **P1-5** | `feat(setup): add model step that fetches and pools in one screen` | `components/setup/SetupStepModel.vue`；`POST /api/setup/quick-model`（建预设+模型+入池，事务） | `pytest tests/webui -q -k setup` + 手动 | 一步完成"填 Key → 拉模型 → 勾选 → 入池"，不回 Agent 页 |
| **P1-6** | `feat(setup): add persona-or-bundle step and finish checklist` | `SetupStepPersona.vue`（套装 tab 先留空位，P2 接入）、`SetupStepDone.vue`（复用 ReadinessCheckList）、`POST /api/setup/complete` | 手动走完 4 步 | 全部检查通过才显示"完成"；`setup_completed_at` 落库后不再自动进入 |
| **P1-7** | `feat(webui): surface module load failures with per-module reload` | `views/ModulesPage.vue`（已有 status=error 列表 → 加"重载"按钮）；复用 `POST /api/modules/{name}/reload` | 手动：破坏一个模块的 main.py 观察 | 装坏模块在列表与向导第 4 步可见，可单模块重载不重启 |

**P1 收尾**：新机器验收清单（从零到群内回话 ≤5 步）写入 `docs/plan/acceptance/P1.md`（可选）。

---

## P2 套装工件 v1（只导出/导入/预检）

> 本阶段**不改运行时**、**不写账号配置**：只做"包进得来、看得懂、导得出"。

| 编号 | 提交信息 | 主要改动 | 验证 | 完成判据 |
|---|---|---|---|---|
| **P2-1** | `feat(bundle): add manifest schema and validator` | `app/application/bundle/manifest.py`（解析+归一）、`validator.py`（六层校验）、`tests/application/test_bundle_manifest.py` | `pytest tests/application -q -k bundle` | 20 条校验用例覆盖：缺字段/坏类型/版本/依赖/路径/大小/哈希/密钥泄漏 |
| **P2-2** | `feat(bundle): add exporter` | `app/application/bundle/exporter.py`（从账号收集 → 脱敏 → 打 zip + checksums）；`tests/application/test_bundle_export.py` | 同上 | 导出包能被 P2-1 校验器接受；**包内无任何明文密钥** |
| **P2-3** | `feat(bundle): add validate and import endpoints` | `app/webui/api/bundles.py`（`/validate`、`/import`，plan token）；`tests/webui/test_bundle_api.py` | `pytest tests/webui -q -k bundle` | validate 不落库；import 复用同一 plan；非法包全部 4xx 且带 code |
| **P2-4** | `feat(webui): add bundle center list and detail` | `views/BundlesPage.vue`、`BundlesDetailPage.vue`、`stores/bundles.ts`、侧栏入口、`dist` | typecheck + 手动 | 列表/详情/空态/下载均已可用 |
| **P2-5** | `feat(webui): add import dialog with preview report` | `components/bundles/BundleImportDialog.vue`（5 屏）+ `BundlePreviewReport.vue` + `BundleSecretForm.vue` | 手动：导入合法/非法/缺密钥三种包 | 预检四块信息（将创建/将修改/依赖/需手填）完整可读 |
| **P2-6** | `docs(bundle): add bundle format specification and sample` | `docs/bundle-format.md`（或并入设计稿附录）、`tests/fixtures/bundles/*`、`docs/plan/acceptance/P2.md` | `pytest` 读取 fixture | 第三方可按文档手工做出可导入的包 |

---

## P3 实例化 + 快照回滚

| 编号 | 提交信息 | 主要改动 | 验证 | 完成判据 |
|---|---|---|---|---|
| **P3-1** | `feat(config): add transactional apply_ops to config service` | `app/infrastructure/config/config_service.py`（`apply_ops`：单事务同步落盘）、`binding_store.py` 配合；`tests/infrastructure/test_apply_ops.py` | `pytest tests/infrastructure -q -k apply_ops` | 批量写入要么全成功要么全回滚（构造第二个 op 失败） |
| **P3-2** | `feat(bundle): add bundle tables and registry service` | `app/infrastructure/persistence/database.py`（4 张表）、`app/application/bundle/service.py`、启动恢复钩子；`tests/application/test_bundle_service.py` | `pytest tests/application -q -k bundle` | 表创建幂等；服务能登记/列出/按账号查激活 |
| **P3-3** | `feat(bundle): add planner and applier with snapshot` | `planner.py`（ID 重写/键 diff）、`applier.py`（7 步）、`tests/application/test_bundle_apply.py` | 同上 | 应用成功后值正确、`bundle_objects` 完整、快照可读 |
| **P3-4** | `feat(bundle): add startup recovery for interrupted applies` | `bootstrap.py` / `service.recover()`；`tests/application/test_bundle_recovery.py` | 同上 | 伪造 `status=applying` → 启动后自动回滚且标记 `rolled_back` |
| **P3-5** | `feat(webui): add apply and rollback flows` | `BundleDetailPage`（应用到账号）、`BundleApplyProgress.vue`、`BundleApplyResult.vue`、`dist` | 手动 + typecheck | 逐项结果（创建/更新/跳过）+ 一键回滚 + 日志可复制 |
| **P3-6** | `test(bundle): add failure injection and byte-equal rollback tests` | `tests/application/test_bundle_failures.py`（写库失败/磁盘满/中断/并发） | `pytest tests/application -q -k bundle` | 6 类失败全部回滚，回滚后与快照逐字节一致 |

---

## P4 更新/卸载/值校验器

| 编号 | 提交信息 | 主要改动 | 验证 | 完成判据 |
|---|---|---|---|---|
| **P4-1** | `feat(config): add schema-driven value validator` | `app/plugin/value_validator.py`；接入 `modules.py` update_config、`agent.py` POST /config；`tests/plugin/test_value_validator.py` | `pytest tests/plugin -q -k validator` | 非法类型/范围/枚举当场 422；合法值行为不变 |
| **P4-2** | `feat(bundle): enforce version constraints and conflict reporting` | `validator.py`（semver 约束、模块版本比对、同名冲突策略）；`tests/application/test_bundle_versions.py` | 同上 | required 缺失=拒绝；optional 缺失=跳过并 warning；同名=新 id + 后缀 |
| **P4-3** | `feat(bundle): add upgrade diff preserving user edits` | `planner.py`（三方 diff：包值/上次应用值/当前值）；UI 冲突列表 | 同上 | 用户改过的键在升级时列为冲突并保留 |
| **P4-4** | `feat(bundle): add uninstall with scoped cleanup` | `service.py`（按 `bundle_objects` 清理 + 先回滚激活）、UI 双勾选；`tests/application/test_bundle_uninstall.py` | 同上 | 只删本包对象；用户后建对象保留并在结果中列出 |
| **P4-5** | `fix(preset): cover provider_model_pool in reference checks` | `provider_model_service.list_references:215-220`、`provider_preset_service.delete:256-289`、`role_preset_service.delete:151-155`；`tests/application/test_preset_references.py` | 同上 | 删预设/角色后池与配置无悬挂引用 |

---

## P5 协议端托管 + 发行包

> 内容最多、外部依赖最重。**先确认长期维护意愿再做**。

| 编号 | 提交信息 | 主要改动 | 验证 | 完成判据 |
|---|---|---|---|---|
| **P5-1** | `feat(protocol): add pinned napcat manifest and verified download` | `app/protocol/`（版本清单 JSON：version/url/sha256）、下载校验、`data/protocol/` 布局 | 单测 + 手动下载 | 固定版本、校验失败拒绝安装、可回滚上一版 |
| **P5-2** | `feat(protocol): add supervisor for start/stop/status/logs` | 进程管理（启动/停止/重启/状态/日志尾）、崩溃重启策略、与账号绑定的生命周期 | 手动：杀掉进程观察重启 | 面板可启停并看到状态；框架退出时不留孤儿进程 |
| **P5-3** | `feat(protocol): forward login qr and autofill reverse ws` | 二维码解析/转发（WS/HTTP）→ WebUI 展示；登录成功后自动写账号 `ws_url/token` | 手动：全新账号扫码 | 扫码 → 自动配置 → 账号连上，全程不打开 NapCat 面板 |
| **P5-4** | `build: add deploy kit with embedded python and wheelhouse` | `scripts/build_deploy_kit.py`（内嵌 Python + wheelhouse + app + dist + 默认套装 + `start.bat`）、CI 产物 | 干净机器实测 | 解压双击 → 首启向导，无 Python 环境也能跑 |
| **P5-5** | `feat(update): add version check and rollback-safe updater` | 版本比对（框架/模块/套装）、更新前备份、失败回滚 | 手动：模拟更新失败 | 更新失败自动回到上一版并给出原因 |

---

## 风险与回滚

| 阶段 | 风险 | 回滚 |
|---|---|---|
| P0 | 日志增多 | 限流 + 单行格式，可降级为 debug 开关 |
| P1 | 向导打断老用户 | `setup_completed_at` 已存在即不进入；可随时从侧栏重进 |
| P2 | 导出泄漏密钥 | 由 P2-2 的扫描测试兜底；导出前二次扫描 |
| P3 | 半成品配置 | 快照 + 启动恢复；`kv.bundle_api_version` 可整体停用 |
| P4 | 值校验误杀历史配置 | 校验失败仅对"新写入"生效；历史值读取不校验；提供 `--force` 过渡开关一版 |
| P5 | 协议端长期维护 | 托管功能默认关闭，走"外部 NapCat"路径仍可用 |
