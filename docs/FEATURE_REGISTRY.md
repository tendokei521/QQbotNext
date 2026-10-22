# 全局能力注册表（FeatureRegistry）

> 适用版本：2.0（分层 + 插件架构）
> 本文档面向插件开发者，说明如何声明“我接管某个框架能力”，以及框架如何自动恢复。

---

## 1. 解决的问题

框架内置了一些“同类能力”，例如：

- 框架主动消息：`proactive`
- 框架定时任务：`schedule`（+ 驱动 `schedule_driver`）
- 长期记忆：`memory`
- 知识库：`knowledge`
- OneBot Tools：`onebot_tools`
- 框架级 Agent 整体启停：`agent`
- 消息组装：`assembly`（装槽型）
- provider 调用策略：`provider_invoker`（装槽型）
- 输出策略：`output`（装槽型，v3 新增）


当第三方插件也实现同类能力时（例如“新的主动回复插件”），需要有一个统一机制让插件：

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
| 策略型能力 | `memory` / `knowledge` / `context` / `message_meta` / `mcp`：只有一组配置键，接管 = 让对方的配置视图失效（框架回退默认） |

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

### 2.2 三层边界

| 层 | 内容 | 归属 |
|---|---|---|
| **基础功能** | 收发消息、事件分发、会话历史、toolcall 注册与执行、**单次** LLM 请求、组装后发送、定时服务、后台任务、数据目录 | 框架（稳定 API，不可替换） |
| **高级接口** | 上表 4 个 | 框架开槽，**模块声明 `supersedes` 接管** |
| **业务** | 记忆/知识库/主动消息/定时任务/OneBot/MCP/元信息/指代 | **模块自己实现**，不声明任何 `supersedes` |

---

## 3. 插件声明式接管（推荐）

```python
# module.py
from app.plugin import BaseModule

class Module(BaseModule):
    name = "我的主动回复"
    sign = "MyProactive"
    description = "替代框架原生主动消息"

    provides = ("proactive",)      # 我提供主动消息能力
    supersedes = ("proactive",)    # 我启用时接管框架主动消息
```

「装槽型」能力（`assembly` / `provider_invoker`）除了 `supersedes` 还要实现对应方法——
接管时框架会把该账号运行时的对应属性指向**你的模块对象**：

```python
class Module(BaseModule):
    supersedes = ("assembly", "provider_invoker")

    def build(self, req):                      # 接管 assembly：返回本轮 messages
        return my_assemble(req)

    async def chat(self, *, runtime, chain, messages, params):   # 接管 provider_invoker
        return await my_runner.chat(messages, params)

    async def stream(self, *, runtime, chain, messages, params):
        async for event in my_runner.stream(messages, params):
            yield event
```

缺方法时**忽略接管并留痕**，不会把不合格对象装进运行时（否则每轮请求都会炸）。
详见 [`MODULE_DEV.md` §4.7](MODULE_DEV.md#47-接管组装与调用策略迁移复刻第三方组装格式)。

只需要这两行，框架会自动：

- 插件加载/启用时：调用 `FeatureRegistry.acquire_module(self)`
- 插件卸载/禁用时：调用 `FeatureRegistry.release_module(self)`
- 接管期间：框架内置 `proactive` 停止发主动消息
- 插件离开后：如果没有其他接管者，恢复 `proactive` 原本配置与运行状态

---

## 4. 手动控制

如果不想用声明式，也可以在 `on_load` / `on_unload` 里手动操作：

```python
from app.plugin import BaseModule

class Module(BaseModule):
    async def on_load(self):
        await self.ctx.services.features.suppress("proactive", self, self.bot_id)

    async def on_unload(self):
        await self.ctx.services.features.release("proactive", self, self.bot_id)
```

查询状态：

```python
# 单个能力
self.ctx.services.features.query("proactive", self.bot_id)

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
| `proactive` | 关闭 `proactive_friend_enable` / `proactive_group_enable`，停止主动消息计时器 | 还原配置，重新武装计时器 |
| `schedule` | 关闭 `schedule_enable`，停止定时任务计时器 | 还原配置，重新武装任务 |
| `memory` | 关闭 `memory_enable` | 还原配置 |
| `knowledge` | 关闭 `knowledge_enable` | 还原配置 |
| `onebot_tools` | 关闭 `onebot_tools_enable` | 还原配置 |
| `agent` | 关闭 `AgentRuntime.config.enabled`，停止主动消息/定时任务计时器 | 恢复启用状态，重新武装计时器 |
| `assembly` | `runtime.assembler = 接管者`（需实现 `build(req)`） | 还原接管前的装配器（交棒 / 框架默认） |
| `provider_invoker` | `runtime.invoker = 接管者`（需实现 `chat()` + `stream()`） | 还原接管前的调用策略（交棒 / 框架默认） |

这些控制器由框架在启动时注册，插件无需手动注册。

---

## 6. 多租约行为

同一能力可以被多个插件同时声明接管：

```text
插件 A 接管 proactive  → proactive 暂停
插件 B 接管 proactive  → proactive 继续暂停（B 只是加租约）
插件 A 卸载             → proactive 仍暂停（B 还在）
插件 B 卸载             → proactive 恢复
```

规则：

- 只要还有任一租约持有者，能力就保持禁用；
- 最后一个租约释放时，恢复“第一个租约接管前”的状态；
- 因此两个主动回复插件不会互相覆盖；
- 后启用者退出后，仍会回到前启用者或框架原始状态。

「装槽型」能力（`assembly` / `provider_invoker`）额外有**交棒**语义：

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
   `provider_invoker` → `chat()` + `stream()`），否则接管会被忽略。

---

## 8. 配置与权限

- 接管只影响运行时行为，不修改插件的 `authority.enabled`；
- 接管会修改框架能力对应的配置值（如 `proactive_friend_enable`），但会保存快照并在释放时恢复；
- 已禁用插件不会在启动时误接管框架能力。

---

## 9. 其他参考

- 完整开发文档：[docs/MODULE_DEV.md](MODULE_DEV.md)
- 架构说明：[README.md](../README.md)
