# 四模块合并记录（9 个内置 LLM 模块 → 7 个）

> 2026-10-31 ｜ 纯搬迁合并，**不改行为、不改配置键名、不重设计文案**。
> 范围：只合并"能改的点"相同/相邻的模块；功能层面的调整（配置面收敛、接管契约、统一出口）
> 留给后续提交。
> 备份：git 标签 `backup/pre-4module-merge`、数据库 `backups/app.pre-4module-merge.*.db`。

## 1. 合并结果

| 新模块 | 吸收 | 键数 | 显示名 |
|---|---|---|---|
| `llm_prompt` | `llm_assembly`(7) + `llm_message_meta`(14) | 21 | 提示词组装 |
| `llm_trigger` | `llm_proactive`(17) + `llm_schedule`(3) | 20 | 触发与调度 |
| `llm_output` | —（不变） | 24 | 流式输出 |
| `llm_context` | —（不变） | 10 | 上下文增强 |
| `llm_memory` | —（不变） | 24 | 长期记忆 |
| `llm_tools_extra` | —（不变） | 10 | 工具扩展 |
| `llm_enhance` | —（不变，`debug_prompt` 归位） | 1 | LLM调试 |
| ~~`llm_knowledge`~~ | 目录早已删除 → 3 个键进 `DELETED_KEYS` | 0 | — |

模块数 **9 → 7**，键数 **109 → 110**（补上底账漏登的 `memory_inject_recency`）。

## 2. 为什么是这两组

- **组装 + 元信息**：两者是同一个概念的两半——`user_message_format`/`prompt_time_position`
  与 `meta_sender_style`/`include_time` 都在决定"本轮 user 消息长什么样"；元信息还写一个标志
  门控框架的 `message_meta` 段。不合并会一直存在两处真值。
- **主动 + 定时**：结构完全同构（建管理器 → `on_online` 武装计时器 → `on_offline` 停 → 同一套流式发送器），
  且 `llm_schedule` 原本直接 import `llm_proactive` 的 `entry`/`stream`。合并后两条跨插件 import 变成包内相对导入。

## 3. 行为等价证据

| 检查 | 结果 |
|---|---|
| `llm_prompt.OWNED_KEYS / default_config / config_schema` 与两个旧模块逐项比对 | 全等（键序、键值、描述原文） |
| 装配视图的 owned 子集 | 保持分离：装配 7 键、元信息 14 键（**没有**合并成一个 21 键视图） |
| `pre_request` 钩子 | `-100 collect` / `20 format` 顺序不变 |
| `llm_trigger` 两个配置视图 | 主动 17 键 / 定时 3 键，仍分开 |
| `on_online` / `on_offline` | 两个驱动都启停，顺序不变 |
| 数据文件位置 | `data/llm/<bot_id>/{proactive_data,tasks_data}.json` 不变 |
| 旧模块测试 | 断言全部保留，仅改 import/模块路径（prompt 12 用例、trigger 62 用例） |

**已知等价性缺口（刻意留到功能调整批次）**：

1. `llm_prompt` 保留了 `supersedes=("assembly",)` 但没有 `build()`，因此启动日志仍会出现
   "声明接管消息组装但缺少 build()，已拒绝"——与合并前完全一致，不是本次回归。
   真正的接管契约（实现 `build`/`respond` + 失败 fail-loud）是下一批的事。
2. `llm_trigger` 合并后只剩**一个** enabled 开关；旧的两个启用位由迁移脚本按 OR 合并。
3. 统计归属：`manager`/`scheduler` 用 `module_name` → `llm_trigger`；`stream.py` 的硬编码
   也同步改成 `llm_trigger`。历史行仍保留旧名字（`llm_proactive`/`llm_schedule`），统计页会同时看到两个名字。
4. `llm_schedule` 对 `llm_context.focus_only_block` 的 import 保持原样（模块间 import 仍在，
   留给"破 import"批次）。

## 4. 配置迁移

`app/application/core_modules_merge.py`（在 `bootstrap.py` 的 `config_service.init()` 之后、
`registry.discover()` 之前执行）：

- **映射**：`llm_assembly`+`llm_message_meta` → `llm_prompt`；`llm_proactive`+`llm_schedule` → `llm_trigger`；
  `llm_knowledge` 无归宿 → 值进 `dropped` 并 `warning` 落日志，行删除。
- **幂等**：`kv["four_modules_merged_v1"]` + "新行已有值优先"合并；重跑收敛。
- **启用位**：多个旧行取 OR；新行已存在时以新行为准。
- **回滚**：跑前整库复制到 `backups/`；旧行删除前内容已并入新行；另可用上面的 git 标签整体回退。
- **只给有旧行的作用域建行**：新装环境不写标志、不建空行（之后建号还能迁）。

契约测试：`tests/application/test_core_modules_merge.py`（并集合并 / OR / 孤儿不静默丢 / 幂等 / 新值优先 / 无旧行不写标志）。

## 5. 顺手修的两处

1. `app/agent/storage/__init__.py` 的 `create_knowledge_store()` 会 `import app.plugins.llm_knowledge.store`
   ——该目录早已删除，函数一调用就 ImportError。本次删除该工厂与导出（无调用方）。
2. `config_partition`：`debug_prompt` 从 `DELETED_KEYS` 移入 `llm_enhance`（模块一直在用它）；
   知识库 3 键移入 `DELETED_KEYS`（模块已不存在）。

## 6. 验证

- 全量 `pytest -q`：**2922 passed / 19 skipped**（最后一次改动前）；`ruff check` 对新增/改动文件无告警；
  `scripts/gen_module_manifests.py --check` → 7 个模块清单一致；
  `scripts/plugin_layout.py --check` 对 `app/plugins` 无新增违规（`llm_trigger/main.py` 已从 248 行降到 <220）。
- 迁移脚本在真实库上的执行留待下次启动（`bootstrap` 里已插桩）；届时会打印
  `[Migrate] 四模块合并：账号/作用域=… 行=…` 并备份数据库。

## 7. 下一步（功能调整批次，不在本次范围）

1. 合并后的配置面收敛（21+20 键 → 各 ≈12 键，合并时间/格式/记录请求等双真值）。
2. 真正接管 `assembly`/`output` 槽（`Module.build/respond` + 接管失败 fail-loud）。
3. 破掉 `llm_trigger → llm_context` 的 import（焦点行改走 provider 接口）。
4. 统一发送出口 + `turn.py` 发言通道判定。
