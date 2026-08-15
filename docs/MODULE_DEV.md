# QQBot Next 模块开发文档

本文档面向模块开发者，说明如何编写模块、注册钩子，以及每个钩子收到的参数。

---

## 1. 模块目录结构

一个模块是一个目录，放在 `module/modules/<name>/` 下：

```text
module/modules/<name>/
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
    authority_type = "all"
```

---

## 2. 两种流水线

框架把消息处理分成两条流水线：

```text
模块流水线（前）
  @module_hook 注册的处理函数
  → 可以 event.llm.stop() 跳过 LLM

LLM 流水线（后）
  @llm_hook 注册的处理函数
  → pre_request / post_response / pre_send / post_send / post_stream
```

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
| `pre_request` | LLM 请求前 | `(self, ctx)` |
| `post_response` | 非流式完整回复生成后 | `(self, ctx)` |
| `pre_send` | 每条消息发送前 | `(self, ctx, msg)` |
| `post_send` | 每条消息发送后 | `(self, ctx, msg)` |
| `post_stream` | 流式回复整体结束后 | `(self, ctx)` |

---

## 5. `LlmContext` 参数说明

所有 LLM 钩子都会收到 `ctx: LlmContext`。

```python
from app.llm import LlmContext
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `ctx.event` | `BaseEvent` | 原始消息事件 |
| `ctx.runtime` | `AgentRuntime` | 当前 Bot 的 Agent 运行时 |
| `ctx.bot` | `IBot` | 当前 Bot 连接 |
| `ctx.session_id` | `str` | 会话 ID，如 `group_123` / `private_456` |
| `ctx.user_text` | `str` | 本次用户输入文本 |
| `ctx.response_text` | `str` | LLM 完整回复文本 |
| `ctx.response_messages` | `list[Message]` | 待发送的消息列表 |
| `ctx.state` | `dict` | 钩子间共享的临时状态 |
| `ctx.job` | `LlmJob` | 当前 LLM 任务对象 |

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
from app.modules import BaseModule, module_hook, llm_hook

class Module(BaseModule):
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
