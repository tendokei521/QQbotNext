# QQBot Next 模块开发文档

本文档面向模块开发者，说明如何编写模块、注册钩子，以及每个钩子收到的参数。

---

## 1. 模块目录结构

一个模块是一个目录，放在 `app/plugins/<name>/` 下：

```text
app/plugins/<name>/
  __init__.py
  module.py           # 必须：定义 Module(BaseModule)
  config_schema.py    # 可选：WebUI 配置表单
  service.py          # 可选：业务逻辑
  pages/index.html    # 可选：自定义配置页
```

最简单的 `module.py`：

```python
from app.modules import BaseModule

class Module(BaseModule):
    name = "示例模块"
    sign = "Example"
    description = "示例"
    permission = "member"
```

---

## 2. 两种流水线

框架把消息处理分成两条流水线，另有一条出站发送后钩子：

```text
模块流水线（前）
  @module_hook 注册的处理函数
  → 可以 event.llm.stop() 跳过 LLM

LLM 流水线（后）
  @llm_hook 注册的处理函数
  → pre_request / post_response / pre_send / post_send / post_stream

消息发送成功后（出站）
  @send_hook 注册的处理函数
  → 收到 SendContext，其中 ctx.message_id 为发送成功响应的消息 ID
```

---

## 2.5 模块权限

模块通过 `permission` 声明允许谁触发：

```python
class Module(BaseModule):
    permission = "group_admin"
```

| 取值 | 含义 |
|---|---|
| `everyone` | 所有人 |
| `member` | 普通群成员及以上 |
| `group_admin` | 群管理/群主 |
| `group_owner` | 仅群主 |
| `owner` | 仅 Bot 拥有者 |

事件上会写入语义化字段：

```python
event.role              # member / group_admin / group_owner
event.permission_role   # member / group_admin / group_owner / owner
event.is_bot_owner
event.is_group_owner
event.is_admin
event.is_member
```

权限过滤由框架在 `ModulePermissionNode` 统一完成，**不要在业务代码里再判断 owner/admin**。

---

## 2.6 全局能力注册表（FeatureRegistry）

框架提供统一的“能力接管”机制，插件可以声明自己接管/禁用某个框架内置能力，并在插件
卸载/禁用时自动恢复。

> 详细说明、API 与多租约规则见 [`docs/FEATURE_REGISTRY.md`](FEATURE_REGISTRY.md)。

### 2.6.1 插件声明接管

在 `Module` 上声明 `supersedes`：

```python
from app.modules import BaseModule

class Module(BaseModule):
    name = "我的主动回复"
    provides = ("proactive",)     # 本插件提供的能力（可选，用于标识/WebUI）
    supersedes = ("proactive",)   # 启用时自动接管并禁用框架主动消息
```

- `provides`：本插件提供的能力 ID（信息性，供 WebUI 与后续能力路由使用）
- `supersedes`：启用/加载时自动接管的框架能力 ID；禁用/卸载时自动释放并恢复

### 2.6.2 手动控制

插件也可以直接在生命周期里手动接管/释放：

```python
class Module(BaseModule):
    async def on_load(self):
        await self.ctx.services.features.suppress("proactive", self, self.bot_id)

    async def on_unload(self):
        await self.ctx.services.features.release("proactive", self, self.bot_id)
```

查询状态：

```python
self.ctx.services.features.query("proactive", self.bot_id)
self.ctx.services.features.status(self.bot_id)
```

### 2.6.3 已注册框架能力

| feature_id | 说明 |
|---|---|
| `proactive` | 框架主动消息（私聊/群聊主动发言） |
| `schedule` | 框架定时任务 |
| `memory` | 长期记忆总开关 |
| `knowledge` | 知识库总开关 |
| `onebot_tools` | OneBot 工具总开关 |
| `agent` | 框架级 Agent 整体启停 |
| `assembly` | 消息组装器：本模块实现 `build(req) -> list[dict]` 即可整体替换本轮 messages 的组装 |
| `provider_invoker` | provider 调用策略：本模块实现 `chat()` / `stream()` 即可替换调用约定（回退链/重试/参数透传） |

> 后两个是「装槽型」能力：接管时框架把该账号运行时的 `assembler` / `invoker`
> 指向你的模块对象，交棒时指回仍在场的接管者，卸载/禁用时自动还原。
> 缺对应方法时忽略接管并留痕（不会把一个不合格的对象装进去，导致每轮请求都炸）。

### 2.6.4 多租约行为

- 同一能力允许被多个插件同时接管；
- 只要还有任一租约持有者，能力就保持禁用；
- 最后一个租约释放时，自动恢复“第一个租约接管前”的状态；
- 因此两个主动回复插件不会互相覆盖，后启用者退出后仍会回到前启用者/框架状态；
- 「装槽型」能力（`assembly` / `provider_invoker`）是**先到先得**：多个接管者时
  生效的是第一个，先到者释放后**交棒**给仍在场的下一个，最后一个释放才还原。

---

## 3. 模块流水线钩子：`@module_hook`

### 3.1 用法

```python
from app.modules import BaseModule, module_hook

class Module(BaseModule):
    @module_hook("message_group", order=10)
    async def on_group_message(self, event):
        ...
```

- `event_type`：订阅的事件类型，例如 `"message_group"`、`"message_private"`、`"notice_poke"`，也可以用 `"*"` 表示全部。
- `order`：同一模块内多个钩子的执行顺序，越小越先执行。
- 如果模块没有显式声明 `subscribe`，框架会从 `@module_hook` 自动推导。

### 3.2 参数

```python
async def handler(self, event: BaseEvent):
    ...
```

| 参数 | 类型 | 说明 |
|---|---|---|
| `self` | `BaseModule` | 当前模块实例，可访问 `self.config`、`self.authority`、`self.ctx.services` |
| `event` | `BaseEvent` | 领域事件对象 |

### 3.3 常用事件字段

消息事件 `MessageEvent`：

```python
event.event_type       # "message_group" / "message_private"
event.message_type     # "group" / "private"
event.text             # 所有 text 段拼接
event.message          # list[MessageSegment]
event.user_id
event.group.group_id
event.bot              # IBot，可发送消息
event.reply("...")     # 快捷回复
event.is_at_me()       # 是否 @ 本 bot
```

通知/申请事件：

```python
event.event_type       # "notice_poke" / "notice_group_recall" / ...
event.group_id
event.user_id
event.target_id
event.operator_id
```

### 3.4 控制 LLM 是否继续

```python
@module_hook("message_group", order=10)
async def on_message(self, event):
    await event.reply("已处理")
    event.llm.stop()   # 本模块已接管，跳过 LLM 流水线
```

- `event.llm.stop()`：跳过 LLM 回复。
- `event.stop()`：终止整条节点链，后续模块和 LLM 都不执行。

---

## 4. LLM 流水线钩子：`@llm_hook`

### 4.1 用法

```python
from app.modules import BaseModule, llm_hook

class Module(BaseModule):
    @llm_hook("pre_request", event_type="*", order=10)
    async def before_llm(self, ctx):
        ...
```

参数：

- `stage`：钩子阶段，见下表。
- `event_type`：只对指定事件类型生效，`"*"` 表示全部。
- `order`：同一阶段内多个钩子的执行顺序，越小越先执行。

### 4.2 钩子阶段总览

| 阶段 | 触发时机 | 参数 |
|---|---|---|
| `pre_request` | LLM 请求前（**组装之前**，`ctx.user_text` 已就绪） | `(self, ctx)` |
| `post_assemble` | 本轮 messages 组装完成、请求发出前 | `(self, ctx)` |
| `post_response` | 非流式完整回复生成后 | `(self, ctx)` |
| `pre_send` | 每条消息发送前 | `(self, ctx, msg)` |
| `post_send` | 每条消息发送后 | `(self, ctx, msg)` |
| `post_stream` | 流式回复整体结束后 | `(self, ctx)` |

`post_assemble` 是**唯一能读到最终请求 messages** 的阶段：`ctx.messages` 可直接增删、
重排、整体赋值；把它置空（或标记 `ctx.job.skip`）表示本轮不请求 LLM。
四条组装路径统一在此收口——普通回复（非流式/流式）、主动消息、定时任务，
所以不必按路径分别打补丁。想**整体替换**组装（而不是事后微调）见
[§4.7](#47-接管组装与调用策略迁移复刻第三方组装格式)。

---

## 4.5 消息发送后钩子：`@send_hook`

### 用法

```python
from app.modules import BaseModule, send_hook

class Module(BaseModule):
    @send_hook(message_type="*", order=10)
    async def after_send(self, ctx):
        message_id = ctx.message_id
        self.ctx.services.cache.set(f"sent:{ctx.bot_id}:{message_id}", ctx.message_type, 300)
```

参数：

- `message_type`：只对指定发送类型生效，`"group"` / `"private"` / `"*"`。
- `order`：同一模块内多个发送钩子的执行顺序，越小越先执行。

### 回调参数 `SendContext`

| 字段 | 类型 | 说明 |
|---|---|---|
| `ctx.message_id` | `int` | **发送成功响应里的 `data.message_id`** |
| `ctx.bot` | `IBot` | 当前发送消息的 Bot 连接 |
| `ctx.bot_id` | `int \| None` | Bot QQ 号 |
| `ctx.action` | `str` | OneBot 动作，如 `send_group_msg` / `send_private_msg` |
| `ctx.params` | `dict` | 发送参数 |
| `ctx.response` | `dict` | OneBot 完整成功响应 |
| `ctx.message_type` | `str` | `"group"` / `"private"` |
| `ctx.group_id` | `int \| None` | 群号（群消息） |
| `ctx.user_id` | `int \| None` | 用户 QQ（私聊消息） |

说明：

- 只有发送 API 返回 `status == "ok"` 且响应 `data.message_id` 存在时才触发；
- 发送被出站节点拦截（未真正发出去）时不会触发；
- 钩子按模块所属 Bot 匹配，只对本 Bot 的发送生效；
- 模块热重载 / 卸载时会自动注销对应钩子。

---

## 4.6 更多钩子总览

### 4.6.1 `@before_send_hook`：发送前钩子

```python
from app.modules import BaseModule, before_send_hook

class Module(BaseModule):
    @before_send_hook(message_type="*", order=10)
    async def before_send(self, ctx):
        if "脏词" in ctx.params.get("message", ""):
            ctx.skip = True   # 拦截本次发送
        ctx.params["message"] = str(ctx.params.get("message", "")).strip()  # 改写
```

- 参数：`message_type`（`"group"` / `"private"` / `"*"`）、`order`
- `ctx.skip = True` 可拦截发送
- 修改 `ctx.params` 可改写发送内容

### 4.6.2 `@api_hook`：任意 OneBot API 调用后

```python
from app.modules import BaseModule, api_hook

class Module(BaseModule):
    @api_hook(action="delete_msg", order=10)
    async def after_delete(self, ctx):
        self.ctx.services.cache.set(f"deleted:{ctx.params.get('message_id')}", True, 60)

    @api_hook(action="send_*", order=20)
    async def after_any_send(self, ctx):
        pass
```

- `action` 支持精确匹配或通配：`"send_group_msg"`、`"send_*"`、`"*"`
- `ctx.success` 表示是否为 `status == "ok"`
- 发送类 API 可通过 `ctx.message_id` 拿消息 ID

### 4.6.3 `@bot_lifecycle_hook`：Bot 生命周期

```python
from app.modules import BaseModule, bot_lifecycle_hook

class Module(BaseModule):
    @bot_lifecycle_hook(state="login", order=10)
    async def on_login(self, ctx):
        pass

    @bot_lifecycle_hook(state="disconnected", order=20)
    async def on_disconnect(self, ctx):
        pass
```

- `state`：`"login"` / `"connected"` / `"disconnected"` / `"error"` / `"*"`

### 4.6.4 `@event_completed_hook`：事件处理完成

```python
from app.modules import BaseModule, event_completed_hook

class Module(BaseModule):
    @event_completed_hook(order=10)
    async def after_event(self, ctx):
        # ctx.event / ctx.duration_ms / ctx.state
        pass
```

在入站事件模块链处理完成后触发；LLM 流水线是后台异步任务，不等待其完成。

### 4.6.5 `@tool_call_hook`：LLM 工具调用后

```python
from app.modules import BaseModule, tool_call_hook
from app.agent import ToolCallContext

class Module(BaseModule):
    @tool_call_hook(event_type="*", order=10)
    async def after_tool_call(self, ctx: ToolCallContext):
        # ctx.name / ctx.args / ctx.result / ctx.success / ctx.duration_ms
        pass
```

- 成功和异常超时都会触发
- 工具调用后钩子挂在对应 Bot 的 AgentRuntime 上，模块卸载时自动注销

---

## 4.7 接管组装与调用策略（迁移/复刻第三方组装格式）

把另一个框架（例如 AstrBot）的组装与请求模式 1:1 搬过来时，事后钩子不够用——
需要**整体替换**两处：消息怎么组装、provider 怎么调用。框架为此开了两个「槽」，
都在**单账号粒度**上生效（模块实例本就按 `(模块, bot_id)` 一份）：

```python
from app.modules import BaseModule, llm_hook

class Module(BaseModule):
    name = "AstrBot 组装复刻"
    sign = "AstrBotAssembly"
    # 声明式接管：启用时装上，禁用/卸载自动还原（多个接管者时先到先得 + 交棒）
    supersedes = ("assembly", "provider_invoker")

    # 1) 替换组装：入参是一次请求的全部归一化输入，返回 OpenAI 风格 messages
    def build(self, req):
        # req.session_id / req.user_text / req.history / req.pre_history_text
        # req.referent_text / req.group_log_text / req.skill_blocks / req.memory_text
        # req.extra_user_content_parts / req.available_tools / req.modalities / req.user_images
        return my_assemble(req)

    # 2) 替换 provider 调用策略（第三方把工具循环放在 runner 层时用这个）
    async def chat(self, *, runtime, chain, messages, params):
        return await my_runner.chat(messages, params)

    async def stream(self, *, runtime, chain, messages, params):
        async for event in my_runner.stream(messages, params):
            yield event
```

「槽」的取值口径：

| 槽 | 默认实现 | 默认行为 | 接管者需要实现 |
|---|---|---|---|
| `runtime.assembler` | `PromptAssembler`（块表） | 人设/协议/技能/记忆/背景/群聊记录/历史/本轮 | `build(req) -> list[dict]` |
| `runtime.invoker` | `ProviderInvoker` | 多 provider 回退链 + 空回复重试 + 参数白名单透传 | `chat(*, runtime, chain, messages, params)` / `stream(...)` |

配套的三个开关（Agent 配置页「会话 / 压缩」组，**都默认关闭/保持原行为**）：

| 配置键 | 默认 | 用途 |
|---|---|---|
| `system_prompt_style` | `blocks` | `single` = 把所有 system 块折成一整条（对齐第三方「一整条 system prompt」形态） |
| `prompt_dump_enable` | `False` | 每轮最终 messages 落盘 `data/llm/<bot_id>/prompt_dump.jsonl`，供逐条 diff |
| `prompt_dump_max_entries` | `20` | 落盘滚动保留条数 |
| `context_token_budget` | `0` | >0 启用 token 预算裁剪（从最旧历史丢，system 与本轮提问必留） |
| `context_token_chars_per_token` | `2.0` | 估算参数（只用于防爆，不用于计费） |

**验证对账的做法**：打开 `prompt_dump_enable` → 跑一轮真实对话 → 取
`data/llm/<bot_id>/prompt_dump.jsonl` 最后一条（或
`GET /api/agent/prompt-dumps?bot_id=<qq>&session_id=group_xxx`）与第三方框架同轮的
`messages` 逐条 diff。落盘的是 **`post_assemble` 之后、token 裁剪之后**的最终形态，
也就是真正发出去的东西。

历史侧配套：`SessionManager.history_snapshot()` 给未投影的原始条目（自带渲染器组装用），
`import_history()` 把外部历史批量搬进来让框架既有组装器也能读到，二者详见
`app/agent/session/manager.py` 的模块 docstring（含存储契约字段表）。

---

## 4.8 模块 ↔ 框架交互面（资源引用 / 系统 LLM / 依赖自检）

三件事一句话：**选系统资源用资源字段，发模型请求用 `self.llm_chat`，用之前先声明 `requires`。**

### 4.8.1 配置里直接引用系统资源（`type: "resource"`）

不用手抄 ID、也不用自己写 `dynamic` 数据源 —— 框架把「可被引用的资源」注册成统一目录，
WebUI（新版与 /legacy）都会渲染成下拉框：

```python
"persona_preset_id": {
    "type": "resource",          # 多选写 "resources"（值变成 id 数组）
    "resource": "role_preset",   # 资源种类，见下表
    "label": "角色预设",
    "default": "",
    "filters": {},               # 可选：透传给候选接口的过滤条件
},
```

| 内置种类 | 说明 | 常用 filters |
|---|---|---|
| `role_preset` | 角色预设（提示词 + 预设对话 + 工具/技能） | — |
| `config_profile` | 配置档案（可经路由绑定会话的整套 Agent 配置） | — |
| `provider_preset` | Provider 连接预设（api_base / 密钥由框架保管） | — |
| `provider_model` | Provider 模型实例 | `{"provider_type": "embedding"}` |
| `bot` | Bot 账号 | — |
| `module` | 已加载的模块名 | — |
| `group` | 该账号当前所在的群（**需要 bot_id**，与 `list` 的 `groups` 数据源同源） | — |

- 运行时读法不变：`module.config.get("persona_preset_id")` 拿到资源 id；
- **诊断不静默**：种类名写错时字段带 `issues: ["unknown_resource:<kind>"]`，界面显示
  「资源种类未注册：<kind>」；存了但资源被删的悬挂引用显示「`<id>`（已删除或未找到）」，
  且**拉不到候选时保存会回填原值**（不会把已存配置抹掉）；
- 全量清单：`GET /api/resources`（种类）与 `ctx.services.resources.options(kind)`；
- 自定义种类：`ctx.services.resources.register(kind, handler, label=...)`（在 `on_load` 里加）。

### 4.8.2 用系统 provider 发模型请求：`self.llm_chat`

```python
resp = await self.llm_chat([{"role": "user", "content": "把这句话翻成英文：你好"}])
if resp.ok:
    text = resp.text

# 指定模型（model_id 通常来自上面 resource 字段的下拉）
resp = await self.llm_chat(messages, model_id=self.config.get("model_id"), system="你是翻译官", temperature=0.3)

# 流式；候选与自检
async for ev in self.llm_stream(messages):
    ...
models = self.ctx.services.llm.models(kind="chat")     # 或 "embedding" / "all"
info = self.ctx.services.llm.resolve(model_id="...")   # 脱敏摘要（不含密钥）
```

解析顺序：显式 `model_id` → 显式 `preset_id` → **本账号 Agent 配置的模型池**。
密钥、多 provider 回退、空回复重试、token 记账、遥测全留在框架；拿不到配置时返回空
`LLMResponse`（`resp.ok` 为假）并留痕，不抛异常。

> 顺带消除一个命名坑：`ctx.services.providers` 是**数据源注册表**（list/dynamic 的模块
> 数据源，群/好友等），与 LLM provider 无关；它现在有语义化别名 `data_sources`。

### 4.8.3 声明依赖并在加载时自检

```python
class Module(BaseModule):
    requires = ("llm", "role_presets")      # 服务名，或可接管能力的 feature_id（如 "assembly"）

    async def on_load(self):
        if "llm" in self.missing_requirements():   # 也可直接用返回列表做降级
            ...                                    # 例如关掉本模块的模型相关功能
```

- 加载时框架自动校验，缺失**只 WARNING 不阻断**加载（日志会点名缺了什么）；
- 名字解析：先看 `ctx.services.<name>`，再看 `FeatureRegistry` 是否有同名能力；
- 框架能力清单：`ctx.services.capabilities()` 或 `GET /api/framework/capabilities`
  （版本 / schema 版本与字段类型 / 资源种类 / 可用服务 / 可接管能力）。

---

## 4.3 模块给 LLM 提供工具（`@tool`）

模块可以把方法暴露给 LLM 作为 function calling 工具：

```python
from app.agent import tool, ToolContext

class Module(BaseModule):
    @tool(
        description="查询某城市天气",
        parameters={
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市"}},
            "required": ["city"],
        },
    )
    async def query_weather(self, ctx: ToolContext, args: dict) -> str:
        # ctx.bot / ctx.session_id / ctx.event / ctx.user_id 可用
        return "晴，25℃"
```

- 工具名默认 = 方法名；
- 处理器签名：`(self, ctx: ToolContext, args: dict) -> str`；
- 也兼容旧式 `TOOLS` 类属性声明；
- 工具执行带 20s 超时、异常兜底、结果截断；
- 模块未启用（authority.enabled=False）时工具自动不注入。

工具支持**权限与作用域**：

```python
@tool(
    description="仅管理员可使用的群管工具",
    parameters={...},
    permission="group_admin",   # everyone / member / group_admin / group_owner / owner
    scopes=["group"],           # group / private / ["*"]
)
async def admin_delete(self, ctx: ToolContext, args: dict) -> str:
    ...
```

- `permission` 默认 `everyone`；
- `scopes` 默认 `["group", "private"]`；
- 私聊场景下 `group_admin` / `group_owner` 自动降级为 `member`，与模块权限语义一致；
- 无权限时工具执行返回 `error: 无权限调用工具 <name>`，并触发 `tool_call_hook` 记录失败。

## 4.4 模块给 LLM 注入技能（`@skill` / `SKILLS`）

技能是写入 system prompt 的能力说明，让模型知道“何时用、怎么做”：

```python
from app.agent import skill

class Module(BaseModule):
    SKILLS = {
        "周报助手": {
            "description": "用户说'写周报'时使用",
            "instructions": "1. 调用 collect_events 收集\n2. 按三节输出",
            "tools": ["collect_events"],
            "examples": [{"input": "写周报", "output": "…"}],
        }
    }

    # 或简写
    SKILLS = {"欢迎": "打招呼时先说欢迎"}

    # 或装饰器形式
    @skill(name="周报助手", description="写周报时使用", instructions="…")
    async def weekly_report(self): ...
```

- 技能默认全部注入；
- 模块 config 里的 `skills_enabled`（`{"<技能名>": bool}`）可单独开关技能；
- 工具同理可用 `tools_enabled`（`{"<工具名>": bool}`）单独开关。

---

## 5. `LlmContext` 参数说明

所有 LLM 钩子都会收到 `ctx: LlmContext`。

```python
from app.agent import LlmContext
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `ctx.event` | `BaseEvent \| None` | 原始消息事件；**主动性/定时路径没有事件对象，此处为 `None`** |
| `ctx.runtime` | `AgentRuntime` | 当前 Bot 的 Agent 运行时 |
| `ctx.bot` | `IBot` | 当前 Bot 连接 |
| `ctx.session_id` | `str` | 会话 ID，如 `group_123` / `private_456` |
| `ctx.user_text` | `str` | 本次用户输入文本 |
| `ctx.messages` | `list[dict]` | **本轮组装后的请求 messages**（仅 `post_assemble` 阶段可读写） |
| `ctx.response_text` | `str` | LLM 完整回复文本 |
| `ctx.response_messages` | `list[Message]` | 待发送的消息列表 |
| `ctx.state` | `dict` | 钩子间共享的临时状态 |
| `ctx.job` | `LlmJob` | 当前 LLM 任务对象；主动性路径为 `None` |

`LlmJob` 常用字段：

```python
ctx.job.id           # 任务 ID
ctx.job.group_key    # 会话分组 key，用于请求池
ctx.job.skip         # True = 放弃本次 LLM
ctx.job.superseded   # True = 被同会话新消息取代
```

---

## 6. 各 LLM 钩子详细用法

### 6.1 `pre_request`：请求前

适合做防抖、合并、暂停、权限前置判断。

```python
@llm_hook("pre_request", event_type="*", order=10)
async def before_llm(self, ctx: LlmContext):
    # 暂停，直到 event.llm.resume() 被调用
    await ctx.event.llm.wait_continue()

    # 或直接跳过本次 LLM
    ctx.job.skip = True
```

防抖示例（同会话只放行最后一条）：

```python
@llm_hook("pre_request", event_type="*", order=0)
async def debounce(self, ctx: LlmContext):
    ok = await ctx.runtime.llm_pipeline.pool.wait_for_continue(ctx.job, debounce=1.5)
    if not ok:
        ctx.job.skip = True
```

### 6.2 `post_response`：非流式回复生成后

适合拆分、清洗、改写完整回复。

```python
@llm_hook("post_response", event_type="*", order=20)
async def after_llm(self, ctx: LlmContext):
    from app.domain.message import Message

    # 直接改文本
    ctx.response_text = ctx.response_text.strip()

    # 拆成多条消息
    parts = [ctx.response_text[i:i+50] for i in range(0, len(ctx.response_text), 50)]
    ctx.response_messages = [Message.from_text(p) for p in parts]
```

注意：`post_response` 只在非流式路径触发。流式路径使用 `pre_send` / `post_send` / `post_stream`。

### 6.3 `pre_send`：每条消息发送前

流式按句发送时，每个句子都会触发一次；非流式拆分多条时，每条也会触发。

```python
@llm_hook("pre_send", event_type="*", order=30)
async def before_send(self, ctx: LlmContext, msg):
    # msg 是 Message 对象
    if not msg.text.strip():
        msg.skip = True          # 跳过这条
    msg.data["text"] = msg.data.get("text", "").strip()
```

### 6.4 `post_send`：每条消息发送后

```python
@llm_hook("post_send", event_type="*", order=40)
async def after_send(self, ctx: LlmContext, msg):
    self.ctx.services.cache.set(f"last:{ctx.session_id}", msg.text, 300)
```

### 6.5 `post_stream`：流式整体结束后

```python
@llm_hook("post_stream", event_type="*", order=50)
async def after_stream(self, ctx: LlmContext):
    # ctx.response_text 此时已包含完整流式回复
    logger.info(f"流式回复完成: {len(ctx.response_text)} 字")
```

---

## 7. 装饰器风格与旧版 `handle()` 的兼容

- 如果模块没有 `@module_hook`，框架会回退调用 `async def handle(self, event)`。
- 如果模块使用了 `@module_hook`，则 `handle()` 不再自动调用。
- `@llm_hook` 与旧的 `LLM_HOOKS = [...]` 类属性声明兼容。

---

## 8. 完整示例

```python
from app.modules import BaseModule, module_hook, llm_hook, send_hook

class Module(BaseModule):
    permission = "member"

    @module_hook("message_group", order=10)
    async def on_group(self, event):
        if event.text == "ping":
            await event.reply("pong")
            event.llm.stop()

    @llm_hook("pre_request", event_type="*", order=0)
    async def debounce(self, ctx):
        ok = await ctx.runtime.llm_pipeline.pool.wait_for_continue(ctx.job, 1.5)
        if not ok:
            ctx.job.skip = True

    @llm_hook("post_response", event_type="*", order=20)
    async def split(self, ctx):
        from app.domain.message import Message
        parts = [ctx.response_text[i:i+50] for i in range(0, len(ctx.response_text), 50)]
        ctx.response_messages = [Message.from_text(p) for p in parts]

    @llm_hook("pre_send", event_type="*", order=30)
    async def before_send(self, ctx, msg):
        if not msg.text.strip():
            msg.skip = True

    @llm_hook("post_send", event_type="*", order=40)
    async def after_send(self, ctx, msg):
        pass

    @llm_hook("post_stream", event_type="*", order=50)
    async def after_stream(self, ctx):
        pass

    @send_hook(message_type="*", order=60)
    async def after_send(self, ctx):
        # ctx.message_id 为发送成功响应的消息 ID
        pass
```

---

## 9. 配置 Schema

模块配置表单定义在 `config_schema.py`，参考 `module/CONFIG_SCHEMA.md`。

常用类型：

```python
SCHEMA = {
    "enable": {"type": "boolean", "label": "启用", "default": True},
    "count": {"type": "number", "label": "数量", "default": 3},
    "name": {"type": "string", "label": "名称", "default": ""},
}
```

模块内通过 `self.config.get("enable")` 读取。
