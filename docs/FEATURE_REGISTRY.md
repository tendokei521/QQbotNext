# 框架能力注册表（FeatureRegistry）

> 读者：要给框架"插一脚"的插件作者。只列**代码里真实存在**的能力点与契约；用法示例见 [MODULE_DEV.md](MODULE_DEV.md) §6，五层装配见 [ARCHITECTURE.md](ARCHITECTURE.md) §5。

## 0. 三类，先分清

| 类型 | 你要做的 | 生效方式 | 代码位置 |
|---|---|---|---|
| **接管型**（4 个 feature） | 声明 `supersedes=("assembly",)` 并实现方法 | 租约接管；卸载/禁用自动恢复 | `app/plugin/features/` |
| **声明型**（capabilities） | `runtime.capabilities.declare_*` | 框架查询时优先于配置 | `app/agent/capabilities.py` |
| **注册型**（hook / section / source / middleware / node） | `on_load` 注册，`on_unload` 撤销 | 立即生效，按 `order` 排序 | 见 §3 |

## 1. 接管型 feature（唯一登记表 `features/builtin.py`）

| feature_id | 槽位 | 接管者必须实现 | 释放时 |
|---|---|---|---|
| `agent` | `runtime.config.enabled` + 停/启调度器 | ——（开关型） | 还原启用态，`scheduler`/`proactive` resume |
| `assembly` | `runtime.assembler` | `build(req) -> list[dict]` | 交棒，否则默认 `PromptAssembler` |
| `provider_invoker` | `runtime.invoker` | `chat(...)` + `stream(...)` | 交棒，否则默认 `ProviderInvoker` |
| `output` | `runtime.output` | `respond(ctx, *, should_stop=None)` | 交棒，否则 `None`（按配置选） |

规则：

- 加载/启用时 `FeatureRegistry.acquire_module(module)`；卸载/禁用时 `release_module`。**被禁用的模块不接管**，`bot_id is None` 也不接管。
- 租约：同一能力先到先得，后来者只加租约不换生效者；释放时交棒给仍在场者，最后一个释放才还原**第一个接管前**的快照。
- 接管者缺方法或运行时未装配 → 控制器拒绝，**不建租约**（不会"显示已接管、实际没换"）。
- `provides` 只用于展示，不产生任何行为；真正生效的只有 `supersedes`。
- 插件不要直接 `FeatureRegistry.register()`；框架能力由框架注册。

手动控制与查询（`ctx.services.features`）：`suppress(feature_id, owner, bot_id)` / `release(...)` / `query(feature_id, bot_id)` / `status(bot_id)`；也可 `from app.plugin import get_features`（同一份数据）。

## 2. 声明型能力（`runtime.capabilities`）

| 方法 | 取值 | 说明 |
|---|---|---|
| `declare_output(kind, source=...)` / `revoke_output(source=...)` | `stream` / `blocking` / `speech` | 输出形态三选一；`source` 不匹配撤不掉别人的 |
| `declare_tool(name, source=...)` / `revoke_tool(name, source=...)` / `has_tool(name)` | 能力名 | 工具能力（如 `context_expand`） |
| 常量 | `CAP_CONTEXT_EXPAND` / `CAP_PROMPT_SECTIONS` | 后者的段注册走 §3 的 section 注册表，框架当前不查询它 |

消费点只有两处：

- `delivery/output.py::select_default_output` → `capabilities.output_capability`：**回复模式最优先**（`reply_mode=participation` ⇒ 发言通道）→ 声明 → 配置兜底；**流式与否只读 `stream_output`**，声明不改变传输。
- `tools/policy.py::prereq_state`：`context_expand` 先问 `has_tool_capability`，没人声明才回落 `context_expand_enable`（键缺失默认 True）。

> 现状：5 个内置模块**都没有声明**（`declare_output` / `declare_tool` 零调用），所以行为全由配置兜底。声明是给第三方替换用的接口，不是现状描述。

## 3. 注册型扩展点

| 接缝 | 注册 API | 契约 | 何时读 |
|---|---|---|---|
| 提示词段 | `register_section(runtime, Section(...))` | `builder(req) -> list[dict]` | 每次装配 |
| 上下文块 | `register_context_provider(runtime, p)` | `async collect(scope) -> str \| ContextBlock` | 每轮取上下文 |
| 工具源 | `register_tool_source(runtime, s)` | `async collect(scope) -> list[ToolSpec]` | 每轮组工具 |
| 中间件 | `register_middleware(runtime, Middleware(...))` | 3 个钩点，见下 | 对应步骤 |
| 审批通道 | `runtime.approval_channel = obj` | `async ask(runtime, *, tool, args, ctx) -> ApprovalOutcome` | 高危工具执行前 |
| 入站/出站节点 | `ctx.services.node_registry.register_inbound/outbound(node, owner=self)` | `async process(ctx, next_)` | 消息链（按 `order`） |
| LLM 阶段钩子 | `@llm_hook(stage, event_type="*", order=100)` | `async (ctx[, msg])` | 对应阶段 |
| 工具调用后钩子 | `@tool_call_hook(event_type="*", order=100)` | `async (ToolCallContext)` | 每次工具调用后 |

- 中间件钩点（`loop/middleware.py`）：`tool_call.before` `{name,args,call_id}`、`tool_result.after` `{name,call_id,result_text,is_error,code}`、`messages.before_send` `{messages}`。返回 `dict` 合并（只覆盖给出的键）、返回 `{"abort": "原因"}` 拦截、异常按 `fail_closed` 中断或跳过。
- 审批（`tools/approval.py`）：取值 `ALLOWED_ONCE` / `REJECTED` / `TIMEOUT` / `UNAVAILABLE`；没有通道、抛异常、返回值不认识 → 一律 `UNAVAILABLE` 拒绝（fail-closed），只有 `ALLOWED_ONCE` 且仅本次生效。触发条件：`tool_approval_mode=manual`（「对话行为」页，默认 auto）+ `source == "onebot"` + `sensitivity in {high, critical}`。
- 节点：框架启动时注册入站链 `router → permission → command → invoke → agent`；插件 `owner=self` 注册的节点在实例卸载时由框架清掉（`remove_owner`）。

## 4. 提示词段（`Section`）

| 字段 | 说明 |
|---|---|
| `name` | 唯一标识；同 `(scope, name)` 覆盖 |
| `order` | 层内升序；同 order 按 name（保证同输入同字节） |
| `builder(req)` | 返回该段 messages，不适用返回 `[]` |
| `scope` | `agent`（默认）/ `global`；同名时 agent 遮蔽 global |
| `complete` | 声明"本段自己产出整条 system"；一次解析最多一个，冲突抛 `ValueError`（装配器 error 后中止本轮） |
| `layer` | `system_prompt` / `persona` / `injected` / `history` / `user`；留空按内置映射，插件段默认 L3 |

- 层序 L1→L5 固定（`assembly/layers.py`），L1 的多段装配时合并成**一条** system。
- 内置段（`prompts/sections.default_sections()`）：`persona(10)` `behavior(20)` `reply_rules(54)` `message_meta(60)` `skills(70)` `memory(80)` `background(90)` `group_log(100)` `identity_context(106)` `history(110)` `user(120)`；`extra(105)` 是 `extra_blocks` 兼容段，每次渲染按原位置插入。`identity_context` = 每轮最后一个 system 块（你的账号 / 拥有者 / 管理员）。
- 内置模块另注册：`emoji_hint(62, L3)`、`schedule(40)` / `proactive(50)`（L1）、`context_convention(59, L1)`。
- 撤段：`unregister_section(runtime, name)`；撤掉的是对内置段的覆盖时自动恢复内置版本。

## 5. 钩子装饰器（8 个）

| 装饰器 | 参数 | 时机 / 回调 |
|---|---|---|
| `@module_hook` | `event_type="*"`, `order=100` | 模块流水线；`async (event)` |
| `@llm_hook` | `stage`, `event_type="*"`, `order` | LLM 阶段；`async (ctx[, msg])` |
| `@send_hook` | `message_type="*"`, `order` | 发送成功（`status=ok` 且带 `message_id`）；`async (SendContext)` |
| `@before_send_hook` | `message_type`, `order` | 发送前，可改 `ctx.params` 或 `ctx.skip=True` |
| `@api_hook` | `action="*"`（支持 `send_*` 通配）, `order` | 任意 OneBot API 调用后；`async (ApiContext)` |
| `@bot_lifecycle_hook` | `state="*"`（`login`/`connected`/`disconnected`/`error`）, `order` | `async (LifecycleContext)` |
| `@event_completed_hook` | `order` | 入站事件链处理完；`async (EventCompletedContext)` |
| `@tool_call_hook` | `event_type="*"`, `order` | 工具调用后；`async (ToolCallContext)` |

LLM 阶段（`run_stage` 的真实调用点）：`pre_request` / `post_assemble` / `post_response` / `pre_send` / `post_send` / `post_stream` / `turn_started` / `step_completed` / `turn_stopping` / `turn_completed` / `bot_sent` / `message_observed`。stage 是字符串、注册不校验（拼错 = 永不触发）；`pre_request` / `pre_send` 命中 `job.skip` / `job.superseded` 时返回 `False`，本轮中止。

## 6. 其它可声明入口

- 指令：`@command(name, aliases, description, skip_llm=True, order, prefixes)` / `@command_group`；默认前缀 `"/"`、`"／"`。
- 工具：`@tool(description, parameters, permission, scopes)` 或类属性 `TOOLS`（`source="module"`）。
- 依赖自检：`requires = ("llm", "assembly", ...)`，加载时缺项只 WARNING；`self.missing_requirements()` 可查。
- 框架有什么：`ctx.services.capabilities()` 返回 `{services, features, resource_types, schema_types, ...}`（`app/application/capabilities.py`）。

## 7. 当前使用方（内置 4 模块）

| 模块 | 用到的扩展点 |
|---|---|
| `llm_trigger` | Section `schedule`/`proactive`（L1）、工具源 `schedule_task`、LLM 钩子 `message_observed`/`bot_sent`、`runtime.scheduler` / `proactive_service` |
| `llm_context` | Section `context_convention`（L1）、工具源 `get_context`、`pre_request` 预取引用、`runtime.context_options` |
| `llm_memory` | 上下文块 + 工具源（`memory_*`）、`pre_request`/`turn_completed`、`runtime.memory_service` |
| `llm_enhance` | `pre_request`（调试 prompt 开关） |

> 2026-11-03：「工具扩展」插件并入框架——OneBot 工具（175 个）成为框架内置工具源
> （`app/agent/tools/onebot/`），提示段 `emoji_hint` 回到默认段表；**总开关**
> `onebot_tools_enable` 移到本体键（「高级选项」页），**逐工具策略**（允许/禁止名单、
> 权限覆盖）留在「工具管理 → OneBot 工具」页；不再有模块与 `runtime.tools_options`。

## 8. 卸载纪律

框架在 `_teardown_instance` 自动清：5 类广播钩子、`llm_hooks` / `llm_tool_call_hooks`、`@tool`、节点（按 owner）、`context_providers` / `tool_sources`（按 owner）、feature 租约。

**必须自己撤**：`unregister_section`、`unregister_middleware`、`capabilities.revoke_*`、`runtime.approval_channel = None`、`runtime.<kind>_options = None`。
