> 本文只负责：工具状态的判定模型（谁能用、为什么不能用），以及历史错位是怎么修的。

# 工具状态模型

## 一句话

**一个管理员、一张总表、一条判定链。** 所有工具来源（OneBot / 模块 / 系统 / MCP）
在"能不能用"这件事上只问 `resolve_tool_state`，不再各写一套 if。

---

## 1. 五个来源

| 来源 `source` | 谁提供 | 归属 |
|---|---|---|
| `onebot` | `llm_tools_extra` 模块的 `OneBotToolSource` | 模块 |
| `module` | 模块用 `@tool` 声明的方法 | 各自模块 |
| `system` | 框架的会话/历史/展开/取回类工具（`recall_tool_result`，见 §7） | framework |
| `mcp` | 外部 MCP server | `llm_tools_extra` |
| —— | Tavily 联网搜索 | 框架（配置在本体「高级功能」） |

---

## 2. 判定链（固定顺序，短路）

```
resolve_tool_state(runtime, spec | name+source, session_scope=..., event=...)
   │
   ├─ ① 所属模块在不在？  module_disabled
   │     onebot/mcp → runtime.tools_options 是否为 None
   │     module     → spec.module.authority.enabled
   │
   ├─ ② 模块自己的策略？   module_switch_off
   │     onebot → resolve_tool_policy（总开关 / 黑白名单）
   │     module  → 模块配置里的 tools_enabled
   │
   ├─ ③ 用户总表？        user_disabled
   │     agent 配置 tools_disabled（唯一的 per-tool 存储）
   │
   ├─ ④ 依赖就绪？        dependency_missing
   │     mcp → 连接是否可用；system → prereq_state
   │
   └─ ⑤ 作用域/权限？      out_of_scope / permission_denied
```

每个结论都带一个**人能看懂**的 `reason_text`，界面直接显示。

### 第 ④ 步的前置开关

`system` 来源查 `tools/policy.py::_SYSTEM_TOOL_PREREQUISITES`，表里第三项是**键缺失时**的默认值
（不能省：`context_expand_enable` 这类默认开启的功能，读不到当 False 会让工具静默消失）：

| 工具 | 前置 | 键缺失时 |
|---|---|---|
| `get_current_session` / `get_chat_history` | —— | 始终可用 |
| `schedule_task` | `schedule_enable` | 不可用 |
| `expand_recent` / `expand_message` / `expand_user` | `context_expand_enable`（能力声明优先） | 可用 |
| `tavily_search` | `tavily_enable` | 不可用 |
| `recall_tool_result` | `tool_spill_enable` | 不可用 |
| `memory_*` | `memory_enable`（有 `runtime.memory_service` 时问它） | 不可用 |
| `knowledge_*` | `knowledge_enable` | 不可用 |

`context_expand` 自 P6 起是**能力声明**：先问 `runtime.capabilities` 有没有人声明这项能力，
声明了就直接算可用（不再看配置键）；没人声明才回落到配置键 `context_expand_enable`
（默认 True，没装该模块的旧实例行为不变）。声明决定**有没有**，配置只表达**要不要用**；
配置键仍被别处当偏好读（如 `【未展开:...】` 标记）。

---

## 3. 存储：一个抽屉

| 配置 | 归谁 | 写入口 |
|---|---|---|
| `onebot_tools_enable` / `allowed` / `denied` / `overrides` | `llm_tools_extra` 模块 | `POST /api/module/llm_tools_extra/config` |
| 各模块的 `tools_enabled` | 各自模块 | 同上 |
| `tools_disabled`（用户关掉的工具名单） | agent | `POST /api/agent/tools/toggle` |
| MCP server 定义 | `llm_tools_extra` 模块 | 模块配置页 |

**规矩**：`POST /api/agent/config` 遇到模块拥有的键**直接 400**
（`app/agent/config.py::module_owned_keys()`），因为写进去没人读。

前端保存时按归属分流（`stores/agentConfig.ts::MODULE_OWNED_FIELDS`）：
模块键 → 模块配置接口，其余 → agent 配置接口。

### 回声 vs 真写入（踩过的坑）

前端保存是把**整份配置**原样提交的，而库里（`module_config.module_name='agent'`）
还躺着 108 个迁移残渣式的模块键，值一直没变。如果只看"提交里含模块键"就 400，
界面任何保存都会失败。

所以按**意图**判断：

| 情况 | 处理 |
|---|---|
| 提交里的模块键 = 库里的值 | 视为回声，静默丢弃（不改任何东西） |
| 提交里的模块键 ≠ 库里的值 | 视为真想写模块键 → 400，指出是哪些键 |
| `GET /api/agent/config` | 干脆不回传模块键，从源头消除回声 |

回归测试：`tests/webui/test_agent_config_save_guard.py`（4 条）

---

## 4. 三条注入/执行路径都用它

| 位置 | 作用 |
|---|---|
| `tool_source._collect_llm_ext` | 组装清单时逐个判定，剔除不可用项并写 debug 日志 |
| `tools/state.resolve_all_tool_states` | `/api/agent/tools` 与「工具总览」页（显示即真相） |
| `spec._execution_gate` | **执行前复核**：清单发出后开关变了也拦得住 |

> 执行前复核是重要的一环：模型拿到的清单是"本轮开始时"的快照，此前执行只查
> 权限等级，所以"清单里有它 → 一定能执行"。

---

## 5. 修掉的历史错位

| 症状 | 原因 | 修法 |
|---|---|---|
| 界面关掉了，工具还能调 | Tools 页写 agent 配置，策略读模块配置（`ConfigView` 模块优先） | 写入分流 + agent 配置拒写模块键 |
| 关掉模块后工具"复活" | 模块卸载后 `tools_options=None`，`policy_config` 回退到 agent 配置里的旧值 | 判定链第 ① 步先看模块在不在，不再回退 |
| 界面显示"已关"但行为是"能用" | `/onebot/tools` 读 agent 配置，策略读模块配置 | 两处都改成 `resolve_tool_state` |
| OneBot 工具绕过统一过滤 | `if spec.source == "onebot": 直接放行` | 所有来源统一走判定 |
| 四个页面各读各的 | 没有单一真值 | `/tools/overview` 总览页 |

---

## 6. 闸门

```bash
pytest tests/architecture/test_tool_state_single_source.py -q   # 9 条
pytest tests/agent/test_tool_execution_gate.py -q               # 3 条
```

锁死：

1. `POST /api/agent/config` 必须调用模块键校验（写读同源）
2. 模块键与本体键不相交（一个键只有一个主人）
3. 注入路径不许出现"按来源直接放行"的分支
4. 模块卸载时给出 `module_disabled` 而不是复活
5. `tools_disabled` 对所有来源生效
6. 不可用项必须有 `reason_text`
7. per-tool 开关只写 `tools_disabled`
8. 清单发出后关掉工具 → 执行被拦（`error: 工具 X 当前不可用`）

---

## 7. 判定通过之后：执行期契约

判定链只管"能不能用"。下列契约发生在**通过之后**，同样 fail-closed。

| 契约 | 字段 / 配置 | 行为 |
|---|---|---|
| 并发 | `ToolSpec.concurrency`、`concurrency_safe(args)`、`tool_parallel_max` | 只有 `concurrency="parallel"` 且 `concurrency_safe(args) is True`（精确 True）才并行，其余一律独占；独占工具是**屏障**，池有界（默认 8，1..32）。回填顺序 = 模型顺序，与完成先后无关。`execute_tool_batch` 拿不到 spec 时按独占——串行只是慢，并发写错不可逆。 |
| 超时 | `ToolSpec.timeout_s`、`tool_timeout_default_s` | 工具声明优先，否则默认 20s。超时**不 cancel** 协程，只把结果换成 `error: TOOL_TIMEOUT ...`，协程继续跑完并记 `_orphan` 日志。 |
| 审批 | `ToolSpec.sensitivity`、`tool_approval_enable`、`runtime.approval_channel` | 开关开 + 来源 `onebot` + `sensitivity in {high, critical}` → 执行前必须问人。通道给出 `ApprovalOutcome`；没有通道 / 抛异常 / 返回未知值都按 `UNAVAILABLE` 拒绝，只有 `ALLOWED_ONCE` 生效且仅本次。 |
| 结果 | `ToolResult(blocks, is_error, code, meta)`、`ERROR_CODES` | 结构化出口；旧的 `"error:"` 前缀仍双读（`is_error_result`），生产者不用改。`tool_call_hook` 里能在 `ctx.extra["code"]` 读到错误码（成功时没有该键）。 |
| 落盘 | `tool_spill_enable`、`tool_spill_max_bytes`、`recall_tool_result` | 超过阈值的全文落到 `data/llm/<bot>/spill/<session>/<sha16>.txt`（内容寻址、原子写、0600），消息里换成预览 + 路径；落盘失败只 warn、保留原文；跳过取回工具自身与 `source="speech"`。 |

