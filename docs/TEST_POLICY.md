# 测试政策：只测契约，不锁实现

`AGENTS.md`「硬性底线」第 2 条的操作细则。测试是**回归安全网**，不是把当前实现抄一遍：锁死实现细节时，重构成本会从「改代码」变成「改代码 + 改一堆用例」。

## 1. 测什么（KEEP）

| 类别 | 具体 |
|---|---|
| 对外契约 | HTTP 请求/响应形状、OneBot 事件解析、DB/文件持久化格式、事件总线 topic 与 payload、状态机合法迁移、配置 schema 的默认值与校验 |
| 行为与不变量 | 公开函数/组件的输入→输出、异常类型与错误路径、权限/鉴权拒绝、并发与超时、**曾经坏过的 bug**（注释写明回归原因） |

## 2. 不测什么（DELETE）

| 类型 | 反例 | 归谁管 |
|---|---|---|
| 私有实现 | 直接调 `_helper()`，断言中间变量 | 由公开路径的行为测试间接覆盖 |
| 文案全文 | `assert hint == "…完整长句…"` | `tests/architecture/test_ui_copy.py`、`test_dashboard_copy.py` |
| 结构快照 | `assert len(x) == 17`、目录/字段清单 | 能自动修复的 `scripts/*.py --check` |
| mock 内部交互 | `assert_called_once_with(...)`、调用次数/顺序 | 只断言产生了什么副作用/返回什么 |
| 当前分支细节 | 启动器内部键、依赖元数据列表、解析器中间状态 | 删掉，或降级为脚本检查 |
| 无断言冒烟 | 只有 `assert r is not None` 且无其他契约 | 合并进真正的行为用例 |

**不固定区域**（提示词文案、模型输出、UI 文案、配置项集合、插件清单）只测不变量、关键片段、必须包含/不得包含：`assert "三选一" in hint` 可以，全文全等不行。

## 3. 数量与写法

- 新功能默认 **≤3 个用例**：主路径 1 + 关键边界 1 + 回归 1；
- 修 bug 先写 **1 个复现用例**，修完保留；
- 单个测试文件 **≤30 个用例**，超了先拆文件或先判重；
- 不为覆盖率数字补测试，需求没写的行为不测。

## 4. 删 / 合并 / 重命名模块

`tests/<layer>/<module>/` 与源码一一对应。**同一个提交里删掉对应测试**——这不算「带失败提交」，后者指「功能坏了却提交」，不是「模块没了测试还在」。移除模块的提交必须同时：删 `app/**` 实现、删 `tests/**` 测试、改被牵连的共享 fixture（不留 `skip` 僵尸）、在 [CHANGELOG.md](../CHANGELOG.md) 记一条。

## 5. 架构闸门（`tests/architecture/`）

只保留两类：**能自动修复**（失败信息直接给修复命令）与**能明确报错的硬规则**。不再新增「清单快照型」闸门（断言文件数/字段数/分支数）——它们随重构必红，却没有回归价值。

| 文件 | 守什么 |
|---|---|
| `test_architecture_boundaries.py` | 分层依赖方向 |
| `test_no_silent_except.py` | 禁止 `except Exception: pass` |
| `test_dashboard_copy.py`、`test_ui_copy.py` | 界面文案（规则见 [UI_COPY.md](UI_COPY.md)） |
| `test_vue_sfc_structure.py`、`test_extensibility_contract.py`、`test_runtime_attr_contract.py` | SFC 结构、跨面一致性、`getattr(runtime, "X")` 必须真存在 |
| `test_tool_state_single_source.py`、`test_turn_path_parity.py`、`test_no_tool_loop_in_providers.py` | 工具状态单一来源、两条一轮对话路径的公共不变量、工具循环只许收敛 |
| `test_plugin_layout.py`、`test_architecture_layout.py`、`test_prompt_layers.py`、`test_prompt_prefix_stability.py` | 目录结构与 `main.py` 只做编排、五层装配不变量、system 前缀稳定性 |

## 6. 本地什么时候跑

| 档位 | 典型改动 | 本地动作 |
|---|---|---|
| 小改动（默认） | 文档 / 注释 / 文案 / 格式化 / 单文件局部改 | **不跑测试、不跑 ruff**，直接提交 |
| 局部逻辑改动 | 单模块 / 单函数行为变更、bug 修复 | 只跑相关文件：`venv\Scripts\python.exe -m pytest tests\<area>\test_x.py -q`（必要时 `-k`） |
| 大工程收尾 | 跨层重构、功能闭环、依赖/配置结构变更、发版前 | 全量 `venv\Scripts\python.exe -m pytest -q` 全绿 + `ruff check .` 无新增违规 |

判断口径：改动是否可能影响当前已知用例的行为。CI（`.github/workflows/ci.yml`）每次 push 跑全量，是安全网。前端改动另加截图与量测，见 [FRONTEND_VERIFY.md](FRONTEND_VERIFY.md)。

## 7. 机械找候选（不靠感觉）

```powershell
venv\Scripts\python.exe scripts\test_audit.py --report                     # 全量分类，只读
venv\Scripts\python.exe scripts\test_audit.py --report --path tests\agent  # 单目录
venv\Scripts\python.exe scripts\test_audit.py --json logs\review\test-audit.json
```

AST 启发式分类（最终人工判断）：`copy_literal`（长中文全等）、`count_snapshot`（`len(...) == N`）、`private_api`（引用 `_private`）、`mock_internal`（`assert_called*`）、`plugin_coupled`（`import app.plugins.*`）、`no_assert`（无断言）。
删改按「每批 ≤1 个目录」推进，提交用 `test(<area>): drop implementation-detail tests`，删完该目录 `pytest <目录> -q` 必须绿。
