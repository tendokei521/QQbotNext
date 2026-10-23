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
| `system` | 框架的会话/历史/展开类工具 | framework |
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
