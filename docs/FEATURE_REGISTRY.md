# 全局能力注册表（FeatureRegistry）

> 适用版本：2.0（分层 + 插件架构）
> 本文档面向插件开发者，说明如何声明“我接管某个框架能力”，以及框架如何自动恢复。

---

## 1. 解决的问题

框架内置了一些“同类能力”，例如：

- 框架级 Agent 整体启停：`agent`（开关型）
- 消息组装：`assembly`（装槽型）
- provider 调用策略：`provider_invoker`（装槽型）
- 输出策略：`output`（装槽型）

> v3 起只剩这 4 个「高级接口」。主动消息、定时任务、记忆、知识库、OneBot 工具等
> 是**业务**，由各自模块用基础接口实现，不再走接管（见 §2.1）。

除这 4 个租约型能力外，框架还留了两处**不走租约**的扩展点：能力声明
`runtime.capabilities` 与高危工具审批通道 `runtime.approval_channel`（见 §5.1）。

当第三方插件也实现同类能力时（例如“新的输出策略插件”），需要有一个统一机制让插件：

1. 声明自己接管该能力；
2. 接管期间暂停框架内置实现；
3. 插件卸载/禁用后自动恢复；
4. 多个插件同时想接管时不会互相覆盖。

`FeatureRegistry` 就是为此提供的全局能力注册表。

---

## 2. 核心概念

| 概念 | 说明 |
|---|---|
| `FeatureController` | 框架能力的控制器，提供 `suppress()` / `restore()` / `status()` |
| `FeatureRegistry` | 全局注册表，维护控制器与插件“租约” |
| `provides` | `BaseModule` 类属性：本插件提供的能力 ID（信息性） |
| `supersedes` | `BaseModule` 类属性：本插件启用时自动接管的能力 ID |
| 租约（Lease） | 一个插件对某能力在某 Bot 实例上的接管记录 |
| `suppress_with_owner()` | （可选覆盖）带租约占有者的接管入口：需要知道「谁在接管」的能力用它装上接管者对象，默认转调 `suppress()` |
| `on_owner_changed()` | （可选覆盖）交棒钩子：租约集合变化后把生效者切给当前持有者，默认不处理 |
| 装槽型能力 | `assembly` / `provider_invoker` / `output`：接管 = 把运行时属性指向插件对象，交棒 = 指回仍在场者，释放 = 还原 |
| 策略型能力 | v3 起**已取消**：`memory` / `knowledge` / `context` / `message_meta` / `mcp` 都是业务，不再是 feature |

### 2.1 已注册能力清单（v3：**4 个**，全部是「高级接口」）

| feature_id | 类型 | 说明 | 内置提供者 |
|---|---|---|---|
| `agent` | 开关型 | 框架级 Agent 整体启停 | — |
| `assembly` | 装槽型 | 消息组装（`build(req)`） | `llm_assembly` |
| `provider_invoker` | 装槽型 | provider 调用约定（`chat`/`stream`） | — |
| `output` | 装槽型 | 一轮回复怎么产出（`respond(ctx)`） | `llm_output` |

> **只有"更换机制 / 接管行为"才算能力。** 记忆、知识库、主动消息、定时任务、
> OneBot 工具、MCP、元信息渲染、指代消解都是**业务** —— 它们由各自模块用
> 基础接口实现（`context_providers` / `tool_sources` / LLM 钩子 / `llm_turn` /
> `send_to` / `spawn`），**框架不知道它们存在**，因此不在本表里。

> 另有两条**不走租约**的扩展点（P6 起）：能力声明 `runtime.capabilities` 与
> 高危工具审批通道 `runtime.approval_channel`。它们不接管框架内置实现、不参与多租约，
> 所以不在上表，契约与接线方式见 §5.1。

### 2.2 三层边界

| 层 | 内容 | 归属 |
|---|---|---|
| **基础功能** | 收发消息、事件分发、会话历史、toolcall 注册与执行、**单次** LLM 请求、组装后发送、定时服务、后台任务、数据目录 | 框架（稳定 API，不可替换） |
| **高级接口** | 上表 4 个 | 框架开槽，**模块声明 `supersedes` 接管** |
| **业务** | 记忆/知识库/主动消息/定时任务/OneBot/MCP/元信息/指代 | **模块自己实现**，不声明任何 `supersedes` |

---

## 3. 插件声明式接管（推荐）

```python
# main.py
from app.plugin import BaseModule

class Module(BaseModule):
    name = "我的输出策略"
    sign = "MyOutput"
    description = "替代框架原生输出策略"

    provides = ("output",)         # 我提供输出策略
    supersedes = ("output",)       # 我启用时接管框架输出策略
```

「装槽型」能力（`assembly` / `provider_invoker` / `output`）除了 `supersedes` 还要实现对应方法——
接管时框架会把该账号运行时的对应属性指向**你的模块对象**：

```python
class Module(BaseModule):
    supersedes = ("assembly", "provider_invoker", "output")

    def build(self, req):                      # 接管 assembly：返回本轮 messages
        return my_assemble(req)

    async def chat(self, *, runtime, chain, messages, params):   # 接管 provider_invoker
        return await my_runner.chat(messages, params)

    async def stream(self, *, runtime, chain, messages, params):
        async for event in my_runner.stream(messages, params):
            yield event

    async def respond(self, ctx, *, should_stop=None):          # 接管 output
        async for msg in my_output(ctx, should_stop=should_stop):
            yield msg
```

缺方法时**忽略接管并留痕**，不会把不合格对象装进运行时（否则每轮请求都会炸）。
详见 [`MODULE_DEV.md` §4.7](MODULE_DEV.md#47-接管组装与调用策略迁移复刻第三方组装格式)。

声明写好之后，框架会自动：

- 插件加载/启用时：调用 `FeatureRegistry.acquire_module(self)`
- 插件卸载/禁用时：调用 `FeatureRegistry.release_module(self)`
- 接管期间：`runtime.output` 指向你的模块，框架默认输出策略不再生效
- 插件离开后：如果没有其他接管者，`runtime.output` 回到接管前的值（`None` = 按配置自动选择）

---

## 4. 手动控制

如果不想用声明式，也可以在 `on_load` / `on_unload` 里手动操作：

```python
from app.plugin import BaseModule

class Module(BaseModule):
    async def on_load(self):
        await self.ctx.services.features.suppress("output", self, self.bot_id)

    async def on_unload(self):
        await self.ctx.services.features.release("output", self, self.bot_id)
```

查询状态：

```python
# 单个能力
self.ctx.services.features.query("output", self.bot_id)

# 当前 Bot 的全部能力状态
self.ctx.services.features.status(self.bot_id)

# 或使用插件 API
from app.plugin import get_features
print(get_features(bot_id=self.bot_id))
```

---

## 5. 已注册框架能力

| feature_id | 接管时行为 | 恢复时行为 |
|---|---|---|
| `agent` | 关闭 `AgentRuntime.config.enabled`，停止主动消息/定时任务计时器 | 恢复启用状态，重新武装计时器 |
| `assembly` | `runtime.assembler = 接管者`（需实现 `build(req)`） | 还原接管前的装配器（交棒 / 框架默认 `PromptAssembler`） |
| `provider_invoker` | `runtime.invoker = 接管者`（需实现 `chat()` + `stream()`） | 还原接管前的调用策略（交棒 / 框架默认 `ProviderInvoker`） |
| `output` | `runtime.output = 接管者`（需实现 `respond(ctx, *, should_stop=None)`） | 还原接管前的策略（交棒 / `None` = 按配置自动选择） |

这些控制器由框架在启动时注册（唯一登记表 `app/plugin/features/builtin.py`），插件无需手动注册。

> provider 适配器现在**只发单次请求**：模型要调的工具报在 `LLMResponse.tool_calls` 里，
> 由调用方交给唯一一份工具循环 `app/agent/loop/tool_step.py` 执行。所以接管
> `runtime.invoker` 的模块要自己负责循环；`chat_with_fallback(tool_executor=...)` 只是
> 兼容壳（内部走 `run_blocking_step`）。详见 [`MODULE_DEV.md`](MODULE_DEV.md) §4.7.1。

### 5.1 不走租约的扩展点（P6 起）

这两处不是「接管框架内置实现」，而是框架留的**挂钩点**：模块写上去/声明上去就生效，
不需要 `supersedes`，也不参与多租约与交棒；卸载时要自己撤掉，否则会一直生效。

| 接缝 | 实现文件 | 契约 | 默认行为 | 如何接上 / 撤掉 |
|---|---|---|---|---|
| 能力声明 `runtime.capabilities` | `app/agent/capabilities.py` | `CapabilityRegistry`：`declare_output(kind, source=...)` / `revoke_output(source=...)` / `declare_tool(name, source=...)` / `revoke_tool(name, source=...)` / `has_tool(name)`；`OutputCapability(kind=stream\|blocking\|speech, source)`、`ToolCapability(name, source)` | 空表；没人声明时回落到配置键（`stream_output` / `speech_via_tool` / `context_expand_enable`），行为与改造前一致 | `on_load` 里 `registry_of(runtime).declare_*`，`on_unload` 里 `revoke_*`——**只能撤自己 `source` 的声明** |
| 审批通道 `runtime.approval_channel` | `app/agent/tools/approval.py` | `async ask(runtime, *, tool, args, ctx) -> ApprovalOutcome`，取值 `ALLOWED_ONCE` / `REJECTED` / `TIMEOUT` / `UNAVAILABLE` | `None` = 没有通道 → 需要审批的调用**一律拒绝**（fail-closed） | 把实现对象赋给 `runtime.approval_channel`；卸载时置回 `None` |

**能力声明**的规则：

- **声明优先于配置**：有人声明了流式，即使 `stream_output=false` 也按流式跑；
- 撤销只能撤自己声明的（`source` 不匹配就不动），卸载即回到「没人提供」，再由配置兜底；
- 输出能力三选一（`stream` / `blocking` / `speech`），`speech` 也是边产边发；
- 现有使用方：`llm_output` 声明输出能力、`llm_context` 声明 `context_expand`；框架侧
  `select_default_output`（`app/agent/delivery/output.py`）与 `tools/policy.prereq_state`
  会先问声明，再回落到配置。

**审批通道**的规则（fail-closed 是硬规矩，不是建议）：

- 触发条件三者同时满足：`tool_approval_enable`（默认 `False`）+ 工具 `source == "onebot"`
  + `sensitivity in {high, critical}`，执行入口是 `app/agent/tools/spec.py` 的 `make_executor`；
- 没有通道 / 通道抛异常 / 通道返回不认识的值 → 一律按 `UNAVAILABLE` 拒绝；
- 只有 `ALLOWED_ONCE` 才执行，且只对**这一次**调用有效（不缓存、不升级）；
- 拒绝话术按 `REJECTED` / `TIMEOUT` / `UNAVAILABLE` 区分，asked/decided 成对留痕。

用法与完整示例见 [`MODULE_DEV.md`](MODULE_DEV.md) §4.7.2 / §4.7.3。

---

## 6. 多租约行为

同一能力可以被多个插件同时声明接管：

```text
插件 A 接管 agent  → Agent 暂停
插件 B 接管 agent  → Agent 继续暂停（B 只是加租约）
插件 A 卸载        → Agent 仍暂停（B 还在）
插件 B 卸载        → Agent 恢复
```

规则：

- 只要还有任一租约持有者，能力就保持禁用；
- 最后一个租约释放时，恢复“第一个租约接管前”的状态；
- 因此两个同类插件不会互相覆盖；
- 后启用者退出后，仍会回到前启用者或框架原始状态。

「装槽型」能力（`assembly` / `provider_invoker` / `output`）额外有**交棒**语义：

```text
插件 A 接管 assembly  → runtime.assembler = A
插件 B 接管 assembly  → 仍是 A（先到先得，B 只加租约）
插件 A 卸载           → runtime.assembler = B（交棒给仍在场者）
插件 B 卸载           → runtime.assembler = 框架默认装配器
```

这一条很关键：否则 A 被卸载/热重载后，运行时还指着 A 的旧实例在跑。

---

## 7. 使用建议

1. 优先使用 `supersedes` 声明式接管，少写生命周期代码；
2. 不要直接硬编码别的模块名/内部配置 key；
3. 如果只是“临时暂停”，请用 `features.suppress()` / `features.release()`；
4. 插件作者不应直接调用 `FeatureRegistry.register()`，框架能力由框架注册；
5. WebUI 模块卡片会显示“接管: xxx”，用于识别插件声明的能力冲突关系；
6. 自定义控制器时：只在需要「谁在接管」时才覆盖 `suppress_with_owner()`，
   只在接管是「装上某个对象」时才覆盖 `on_owner_changed()`——两者默认实现
   已保证既有控制器（含第三方）行为不变；
7. 装槽型能力的接管者必须实现约定方法（`assembly` → `build(req)`；
   `provider_invoker` → `chat()` + `stream()`；`output` → `respond(ctx, *, should_stop=None)`），
   否则接管会被忽略。

---

## 8. 配置与权限

- 接管只影响运行时行为，不修改插件的 `authority.enabled`；
- 接管会修改框架能力对应的配置值（如 `agent` 的启用状态），但会保存快照并在释放时恢复；
- 已禁用插件不会在启动时误接管框架能力。

---

## 9. 其他参考

- 完整开发文档：[docs/MODULE_DEV.md](MODULE_DEV.md)
- 架构说明：[README.md](../README.md)
