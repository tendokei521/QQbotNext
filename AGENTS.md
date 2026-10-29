# Agent 工作规则

本文件用于约束本仓库中的 AI Agent / 协作开发行为。

## 提交规则

1. **每完成一个可独立提交的步骤，必须立即执行 GitHub 提交**，不要等到最后一次性提交。
2. 提交前先 `git add` 相关文件，再 `git commit`。
3. 提交信息使用 Conventional Commits 格式，例如：
   - `feat(dashboard): add PageSectionNav component`
   - `fix(llm): remove schedule fallback`
   - `docs: add agent commit rules`
   - `refactor(provider): normalize api_base endpoints`
4. 每个提交应保持“小而完整”：一个提交只做一件事，便于回滚和审查。
5. 如果当前工作区有未提交的无关改动，不要混入当前提交；只提交与当前步骤相关的文件。
6. 本节为准一来源；`STYLE.md §9 / §10.3 / §11` 已按本规则同步修订。可用 `git config commit.template .gitmessage`
   启用 Conventional Commits 预填模板；`gitpush.py` 亦已改为校验本格式（不再自动生成时间戳提交信息）。

## 验证规则（分层：按改动规模决定跑多少）

**原则：CI 是安全网，本地不必每次全量。** `.github/workflows/ci.yml` 已在每次 push / PR
跑全量测试 + ruff + 依赖校验 + 前端构建，所以本地把全量测试压到「阶段收尾」即可，
日常小改动**直接提交**。

| 档位 | 典型改动 | 本地要做的验证 |
|---|---|---|
| **小改动（默认）** | 文档 / 注释 / 文案、重命名、格式化、单文件局部修改、配置默认值调整 | **不跑测试、不跑 ruff**，`git add <相关文件>` → `git commit` 即可 |
| **局部逻辑改动** | 单个模块 / 单个函数的行为变更、bug 修复 | 只跑**相关测试文件**：`venv\Scripts\python.exe -m pytest tests/test_x.py -q`（必要时 `-k <关键词>` 收窄）；缺对应用例则先补一个再跑 |
| **大工程收尾** | 跨层 / 框架级重构、新增功能闭环、多模块联动改动、依赖或配置结构变更、发版前 | 全量 `venv\Scripts\python.exe -m pytest -q` **全绿** + `ruff check .` 无**新增**违规，之后再打收尾提交 |

判断口径：**改动是否可能影响当前已知用例的行为**。

- 只动文字、名称、注释、文档 → 小改动档位；
- 动到可执行逻辑，但影响范围可枚举（知道该跑哪几个测试文件）→ 局部档位；
- 范围说不清、或跨层跨模块 → 按大工程收尾档位处理（此时才值得花全量测试的时间）。

### 前端改动：必须看真实渲染（截图 + 布局量测）

前端问题（中文被挤成竖排、按钮被挤出 390px 屏幕、选中态切换时文字跳动 1-2px、
长文案被容器裁掉半句）**类型检查、构建、单测全都发现不了**，只有截图和量 DOM 能发现。
所以只要改动 `app/webui/dashboard/**` 的 `.vue` / `.ts`、前端文案或后端注入页面，
除上面按规模跑的验证外，还必须完成：

1. **静态闸门**：`cd app/webui/dashboard; npm run typecheck`，改完跑 `npm run build`
   （`dist/` 是进仓库的产物，**截图前必须重新 build**，否则截的是旧界面）；
   改文案再跑 `venv\Scripts\python.exe -m pytest tests\architecture -q`。
2. **两个尺寸截图**：起独立测试库预览服务（端口 9311），用
   `node scripts\frontend_shots.mjs --routes <改过的路由> --widths 1440,390`
   出图 + `metrics.json`，**逐张人工看**：不竖排、不截断、不重叠、主操作可见。
3. **量而不是猜**：`metrics.json` 里所有路由 `overflow` 必须为 0（即
   `scrollWidth == innerWidth`，390px 尤其看）；改选中态/hover/展开收起时，必须量
   "点前 / 点后"的 `getBoundingClientRect`，位置与尺寸差 **≤1px**，只允许颜色/阴影变化。
4. **产物分两个提交**：先提交 `src/**`，再单独提交重建的 `dist/**`；截图放 gitignore 的
   `logs/review/`，不要进仓库。

**不许在没看过渲染结果时说"前端改好了"**——按硬性底线第 4 条算未验证。
完整步骤、命令、坐标系差值片段与常见坑见 `docs/FRONTEND_VERIFY.md`。

**硬性底线（任何档位都不豁免）：**

1. **不许带着已知失败提交**：若主动跑了某个测试且它失败，必须先修或先说明，不得把红的测试
   当作"稍后修"提交；小改动档位豁免的只是"跑测试"这个动作，不是"可以有失败"。
2. **新增 / 修改功能必须配套「契约测试」**（不是实现快照）：只测对外行为 / 契约 / 关键边界 /
   曾经坏过的回归，细则见 `docs/TEST_POLICY.md`。私有实现、UI 文案全文、结构计数、mock 调用
   顺序不在"必须补测试"范围内；**删除 / 合并 / 重命名模块时，同一提交里删掉对应测试**，
   不算"带失败提交"——模块没了，测试不该留下变红。
3. 小改动档位同样必须遵守 Conventional Commits、一步一提交、不混入无关改动等提交规则。
4. 结论必须基于**实际跑过的结果**：没跑就说"未验证"，不要凭印象说"应该没问题"；
   向用户汇报时要写清"跑了什么、没跑什么"。
