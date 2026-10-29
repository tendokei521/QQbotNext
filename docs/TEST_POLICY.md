# 测试政策：只测契约，不锁实现

> 准一来源：`AGENTS.md`「硬性底线」第 2 条；本文件是它的操作细则。
> 目的：测试是**回归安全网**，不是"把当前实现逐字抄一遍"。测试锁死实现细节时，
> 重构成本会从"改代码"变成"改代码 + 改 3000 个用例"，本仓库已经撞到这个墙（见 §1）。

## 1. 现状与目标

| 指标 | 现状 | 目标 |
|---|---|---|
| 用例数 | 3156 | **1000–1200** |
| 测试文件 | 317 | ~120 |
| 测试行数 | 45k | ~18k |
| 全量耗时 | 122s | <45s |

问题不在"测试多"，在**测错了对象**：252 处 `assert len(...) == N`、285 处
`== "中文全文"`、56 个文件 `import app.plugins.*`。删一个插件 → 13 个文件收集失败；
改一句文案 → 一片红。这些不是安全网，是负债。

## 2. 测什么 / 不测什么

**只测这两类（KEEP）：**

1. **对外契约**：HTTP API 的请求/响应形状、OneBot 事件解析、数据库/文件持久化格式、
   事件总线 topic 与 payload、状态机合法迁移、配置 schema 的默认值与校验规则。
2. **行为与不变量**：公开函数/组件的输入→输出、错误路径与异常类型、权限/鉴权拒绝、
   并发与超时行为、**曾经坏过的 bug（注释里写明 issue/回归原因）**。

**明确不测（DELETE）：**

| 类型 | 反例 | 归谁管 |
|---|---|---|
| 私有实现 | 直接调 `_helper()`，断言内部中间变量 | 由公开路径的行为测试间接覆盖 |
| 文案全文 | `assert title == "提示词风格 · 必选，三选一"` | `tests/architecture/test_dashboard_copy.py`、`test_ui_copy.py` 统一拦 |
| 结构快照 | `assert len(components) == 17`、目录/文件/字段清单 | 能自动修复的闸门（`scripts/*.py --check`） |
| mock 内部交互 | `mock.assert_called_once_with(...)`、调用次数/顺序 | 只断言"产生了什么副作用/返回什么" |
| 当前分支细节 | 启动器缓存内部键、依赖元数据列表、解析器中间状态 | 删除，或降级为 `scripts/*.py --check` |
| 无断言冒烟 | 只 `assert result is not None` 且无其他契约 | 合并进真正的行为用例 |

**不固定区域（提示词文案、模型输出、UI 文案、配置项集合、插件清单）**：只测
**invariant / 关键片段 / 必须包含与不得包含**，禁止全文全等。例：
`assert "三选一" in hint` 可以，`assert hint == "…完整长句…"` 不行。

## 3. 数量纪律

- 新功能默认 **≤3 个用例**：主路径 1 + 关键边界 1 + 回归 1；
- 修 bug 先写 **1 个复现用例**，修完保留；
- 单文件 **≤30 个用例**，超了先拆文件或先判重（同场景多处覆盖只留一处）；
- 不为"覆盖率数字"补测试；需求没写的行为不测。

## 4. 模块 lifecycle：删模块就删测试

`tests/<layer>/<module>/` 与源码一一对应。**删除 / 合并 / 重命名模块时，同一个提交里
删掉对应测试**，不适用"不许带失败提交"——那是针对"功能坏了却提交"，不是针对"模块没了
测试还在"。移除模块的提交必须同时：

1. 删 `app/**` 对应实现；
2. 删 `tests/**` 对应测试；
3. 若被其他测试共享，改共享 fixture，不要留 `skip` 僵尸；
4. 在 `CHANGELOG.md` 里记一条。

## 5. 架构闸门（`tests/architecture/`）

只保留两类闸门：

1. **能自动修复**：`scripts/plugin_layout.py --apply`、`scripts/gen_*.py` 之类，
   失败信息直接给出修复命令；
2. **能明确报错的硬规则**：依赖方向、静默吞异常、UI 文案禁用词、Vue SFC 结构。

**不再新增"清单快照型"闸门**（断言当前文件数/字段数/分支数）。已有这类闸门逐步
降级为脚本检查或删除——它们随重构必红，却不提供回归价值。

## 6. 怎么找可删的（机械化，不靠感觉）

```powershell
# 全量分类报告（只读，不删）
venv\Scripts\python.exe scripts\test_audit.py --report

# 只看某个目录
venv\Scripts\python.exe scripts\test_audit.py --report --path tests\agent

# 机器可读，供批量处理
venv\Scripts\python.exe scripts\test_audit.py --json logs\review\test-audit.json
```

分类口径（`test_audit.py` 用 AST 判定，纯启发式，最终以人工判断为准）：

- `copy_literal`：断言与长中文常量全等；
- `count_snapshot`：`assert len(...) == 数字`；
- `private_api`：直接引用 `_private` 名称；
- `mock_internal`：`assert_called*` / 调用次数断言；
- `plugin_coupled`：文件 `import app.plugins.*`（模块一旦移除即断）；
- `no_assert`：用例里没有任何断言或 `pytest.raises`。

## 7. 删改流程（每批 ≤1 个目录，单独提交）

1. 跑 `test_audit.py --report --path <目录>`，按上表筛候选；
2. **逐文件判断**：先找 KEEP（契约/回归/权限），再删 DELETE；整个文件都是快照才整文件删；
3. 删完跑该目录测试：`pytest <目录> -q`，**必须绿**（已知外部阻塞除外，需在报告里说明）；
4. 单独提交：`test(<area>): drop implementation-detail tests`（删/合并测试不算功能提交，
   用 `test:` 前缀；不要和源码改动混在一起）；
5. 汇报写清"删了多少、保留了什么、跑了什么"。
