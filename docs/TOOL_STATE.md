# 工具状态机与执行契约

> 范围：一个工具从"进本轮清单"到"结果回传"的判定与契约，以 `app/agent/tools/*.py` 为准。
> 一句话：**一个判定点（`resolve_tool_state`）、一张总表（`tools_disabled`）、执行前再复核一次。**

## 1. 来源

| `source` | 谁提供 | 例子 |
|---|---|---|
| `system` | 框架与模块（模块产出也标 system） | `get_current_session`、`tavily_search`、`recall_tool_result`（框架）；`schedule_task`（llm_trigger）；`memory_*`（llm_memory） |
| `onebot` | **框架内置**（`app/agent/tools/onebot/`，2026-11-03 从「工具扩展」插件并入） | OneBot API 175 个 |
| `module` | 模块 `@tool` / `TOOLS` | 第三方工具 |
| `llm_context` | `llm_context` | `get_context` |
| `speech` | 框架发言通道（**仅群聊参与度模式**；私聊永远普通输出） | `send_message` / `finish` |

判定链只对 `onebot` / `module` / `system` 分来源处理；其余来源跳过 ①—④，只受总表与权限约束。
MCP（`source=mcp`）随「工具扩展」插件一起下线，不再有这一来源。

## 2. 判定链（`tools/state.py`，固定顺序、短路）

```text
resolve_tool_state(runtime, spec | name+source, session_scope=..., event=...)
 ① 能力在不在       → tools_disabled / module_disabled
      onebot: runtime.config.onebot_tools_enable（「高级选项」总开关）
      module: spec.module.authority.enabled
 ② 工具自己的策略   → module_switch_off / out_of_scope
      onebot: 「工具管理 → OneBot 工具」页的允许/禁止名单 + 逐工具权限覆盖 + 清单默认值
      module: 模块配置 tools_enabled[name] is False
 ③ 用户总表         → user_disabled（tools_disabled + 三套旧 map 只读兼容）
 ④ 依赖就绪         → dependency_missing
      system: tools/policy.prereq_state(name)
 ⑤ 作用域/权限      → out_of_scope / permission_denied（只在给了 event 时判）
```

每个结论都带 `reason_text`（`ToolState.reason_text`），界面直接显示。

## 3. 前置开关（`tools/policy.py::_SYSTEM_TOOL_PREREQUISITES`）

| 工具 | 前置键 | 键缺失时 | 当前有生产者 |
|---|---|---|---|
| `get_current_session` | —— | 始终可用 | ✓ 框架 |
| `schedule_task` | `schedule_enable` | 不可用 | ✓ llm_trigger |
| `memory_save/recall/delete/correct/deny` | `memory_enable` | 不可用 | ✓ llm_memory |
| `tavily_search` | `tavily_enable` | 不可用 | ✓ 框架（注入还要求 `tavily_api_key` 非空） |
| `recall_tool_result` | `tool_spill_enable` | 不可用 | ✓ 框架 |
| `expand_recent/message/user` | `context_expand_enable`（声明优先） | 可用 | ✗ 无（已由 `get_context` 取代） |
| `knowledge_add/search/delete` | `knowledge_enable` | 不可用 | ✗ 无（知识库模块已不存在） |

`memory_*` 特殊：`runtime.memory_service` 存在且有 `enabled()` 时问它，否则看配置。

## 4. 权限与作用域（`tools/spec.py`）

- `scopes` 默认 `("group", "private")`，`"*"` 通配；会话类型不匹配 → `out_of_scope`。
- `permission` 等级：`everyone`/`member`=0、`group_admin`=1、`group_owner`=2、`owner`=3；私聊下群管理/群主降为 0；`owner` 看 `event.is_bot_owner`。
- `event is None`（主动消息 / 定时任务）→ 直接放行：这类触发没有"普通群成员"主语。
- `allowed_for_event` 是唯一实现：判定链第 ⑤ 层与执行器共用。

### 4.1 敏感级与授权身份（2026-11-03 重写）

`sensitivity` 由清单派生（critical 优先），不再按"不可撤销"划分：

| 等级 | 判定 | 需要谁授权 |
|---|---|---|
| `critical` | 修改/退出**自身账号**或账号级数据：资料/头像/签名/在线状态、好友、自定义表情、收藏/空间、原始数据包、缓存/重启…（见 `manifest._CRITICAL_TOOLS`） | **bot 拥有者**（owner） |
| `high` | 清单默认权限为**群管理员及以上**（group_admin / group_owner / owner） | **bot 管理者或拥有者**（manager / owner） |
| `normal` | 其余（member / everyone） | 不额外要求账号侧身份 |

授权身份的映射在 `tools/onebot/security.SENSITIVITY_AUTHORIZER_ROLES`，供「消息凭证」
鉴权使用；人工审批（`tool_approval_mode=manual`）对 high/critical 额外生效（fail-closed）。

### 4.2 消息凭证鉴权（群聊）

群聊里调用 high/critical 的 OneBot 工具时，模型必须传 `auth_message_id` = 让它执行
该操作的那条群消息的 message_id：

| 等级 | 消息发送者要求 |
|---|---|
| `high` | 在 `agent_owner_list` 或 `agent_manager_list` 里 |
| `critical` | 仅在 `agent_owner_list` 里 |

校验（`tools/onebot/message_auth.py`）：`get_msg(auth_message_id)` 取消息 → 必须属于
**当前群** → 取发送者 → 按名册判身份。消息不存在、跨群、缺参、发送者权限不足
一律返回 `error: PERMISSION_DENIED ...`，处理器不执行（fail-closed）。
私聊与无 `auth_message_id` 的普通工具维持原判定。

## 5. 注入与执行

| 阶段 | 位置 | 行为 |
|---|---|---|
| 组装清单 | `loop/tool_source._collect_llm_ext` | 各来源产 spec → 逐个 `resolve_tool_state` → 剔除不可用并 debug 记录 |
| 执行 | `tools/spec.make_executor` | ①权限（消息凭证 auth_check 或事件判定）→ ②`_execution_gate` 复核 → ③审批 → ④调用 |
| 鉴权 | `ToolSpec.auth_check` / `self_checked_permission` | 群聊 high/critical 工具先校验 `auth_message_id`（见 §4.2），通过后才进入审批/执行 |
| 未知工具 | 同上 | `error: 未知工具 <name>` |

执行前复核的价值：清单是"本轮开始时"的快照；期间「高级选项」关掉工具或模块被关同样拦住，提示 `error: 工具 X 当前不可用（原因）`。

## 6. 执行期契约（通过判定之后，同样 fail-closed）

| 契约 | 字段 / 配置 | 行为 |
|---|---|---|
| 并发 | `ToolSpec.concurrency`、`concurrency_safe(args)`、`tool_parallel_max` | 仅 `parallel` 且 `concurrency_safe(args) is True` 才并行，其余独占；独占是屏障；池默认 8（1..32）；回填按模型顺序 |
| 超时 | `ToolSpec.timeout_s`、`tool_timeout_default_s` | 工具声明优先，否则 20s；超时**不取消**协程，只换成 `error: TOOL_TIMEOUT` 并给孤儿任务留痕 |
| 审批 | `tool_approval_mode`（「对话行为」页）、`runtime.approval_channel` | manual + `source=onebot` + `sensitivity in {high,critical}`（high=群管理类 / critical=自身账号类，见 §4.1）；fail-closed，仅 `ALLOWED_ONCE` 本次有效 |
| 结果 | `ToolResult(blocks, is_error, code, meta, additional_contexts, concludes_turn)`、`ERROR_CODES` | 结构化出口；旧 `"error:"` 前缀双读；钩子能在 `ctx.extra["code"]` 读到错误码 |
| 落盘 | `tool_spill_enable`、`tool_spill_max_bytes` | 超阈值（默认 50KB）写 `data/llm/<bot>/spill/<session>/<sha16>.txt`，正文换成预览+路径；失败只 warn；跳过 `recall_tool_result` 与 `speech` |
| 回应要求 | 固定框架默认 `tool_loop.DEFAULT_REPLY_DIRECTIVE` | 拼在**结果末尾**（截断/落盘之后）；错误结果与 `speech` 不追加；原模块配置随「工具扩展」插件下线 |

`make_executor` 也支持 `sink`：把 `ToolResult` 交给调用方——`additional_contexts` 汇总成一条 user 消息，`concludes_turn` 停止继续请求（`loop/tool_step.py`）。

## 7. 写入归属

- per-tool 开关只有一处：`tools_disabled`（`POST /api/agent/tools/toggle` → `set_tool_disabled`）。
- OneBot 工具总开关：`onebot_tools_enable`（「高级选项 → 工具」页）。
- OneBot 工具策略：`onebot_tools_allowed` / `onebot_tools_denied` / `onebot_tool_overrides`
  （「工具管理 → OneBot 工具」页：允许/禁止名单、逐工具触发权限与可用范围）。
- 消息凭证：`auth_message_id` 是**模型输入参数**（不是配置键），由 `message_auth.py`
  校验消息发送者身份（见 §4.2）；校验参数不传给 OneBot action。
- 结果长度、调试日志、戳一戳冷却、MCP 配置随「工具扩展」插件删除（改为框架常量/下线）。
- 各模块 `tools_enabled` 归各自模块。
- `POST /api/agent/config` 遇到模块键且**值真的变了**才 400（等值视为前端回声丢弃）；`GET` 不回传模块键。

## 8. 闸门

```bash
venv\Scripts\python.exe -m pytest tests/architecture/test_tool_state_single_source.py -q   # 9 条
venv\Scripts\python.exe -m pytest tests/agent/test_tool_execution_gate.py -q               # 3 条
```

锁死：写读同源（模块键 400）、模块键与本体键不相交、OneBot 开关是本体键、注入无"按来源放行"、
工具关闭给 `tools_disabled` 而不是复活、`tools_disabled` 全来源生效、不可用项必有 `reason_text`、
per-tool 只写一处、清单发出后关工具执行被拦。
