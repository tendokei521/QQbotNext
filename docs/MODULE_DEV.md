# 模块（插件）开发

> 读者：要给 QQBot Next 加一个功能（写插件）的人。
> 只写当前框架真实存在的接口；不要照抄归档文档里的旧模块名/旧装饰器。
> 目录与代码风格规范另见 [PLUGIN_STYLE.md](PLUGIN_STYLE.md)，能力清单见 [FEATURE_REGISTRY.md](FEATURE_REGISTRY.md)。

---

## 1. 先判断：该写插件吗

| 情况 | 结论 |
|---|---|
| 加一种"会说话/会做事"的能力（新工具、新数据源、新指令、新提示词段） | **写插件** |
| 只是调整框架已有行为（提示词顺序、工具暴露策略、投递方式） | 改框架，别用插件硬塞 |
| 想替换整个机制（消息组装 / provider 调用 / 输出策略 / 触发仲裁） | 声明 `supersedes` 接管槽位，属于高级用法 |

框架不认识"记忆""知识库""心情"这类业务概念；内置的 5 个模块（触发与调度 / 上下文增强 /
长期记忆 / 工具扩展 / LLM 调试）本身就是插件，可以直接拿来当范本读。

---

## 2. 目录形态

```text
<插件根>/<name>/
  module.json          # 必须：清单
  __init__.py          # 必须：包标记（相对导入依赖它）
  main.py              # 必须：定义 Module(BaseModule)
  config_schema.py     # 可选：配置表单（分组/字段/显隐）
  src/                 # 可选：实现细节（store/service/tool/…）
```

- **内置模块**：`app/plugins/<name>/`（随仓库发布）
- **第三方模块**：`data/module/plugins/<name>/`（WebUI 上传 zip 安装，升级不丢，不进仓库）
- 两种位置**目录形态完全一致**；模块内部一律用**相对导入**（`from .src.store import ...`）。

`module.json`：

```json
{
  "name": "my_module",            // 目录名，必须与模块名一致
  "display_name": "我的模块",      // 界面显示名
  "sign": "MyModule",             // 唯一标识（权限/日志用）
  "version": "1.0.0",
  "description": "一句话说明它做什么",
  "author": "you",
  "entry": "main.py",
  "api_version": 1
}
```

---

## 3. 最小可运行模块

```python
# main.py
from __future__ import annotations

from app.plugin import BaseModule


class Module(BaseModule):
    name = "我的模块"
    sign = "MyModule"
    description = "示例：收到 @ 时回一句话"
    permission = "everyone"
    subscribe = ("message_group",)
    category = "消息"
    order = 100
    default_config = {"reply_text": "我在"}

    async def on_load(self) -> None:
        # 装配阶段：拿运行时、注册工具源/上下文/提示词段/定时任务
        runtime = self._runtime()
        ...

    async def on_unload(self) -> None:
        # 卸载阶段：撤掉自己注册的一切（框架不会替你清理注册表条目）
        ...

    # 事件入口（subscribe 里列的事件会走到这里）
    async def handle(self, event) -> None:
        text = str(self.config.get("reply_text", "我在"))
        await self.send_to(event.session_id, text)
```

要点：

- `on_load` 里**不要**假设网络已通/账号已登录；需要"登录后"动作放 `on_online`。
- 模块被禁用时**不会执行 `on_load`**（也就不会挂定时器/工具）；重新启用会重新加载。
- `on_unload` 必须自己撤注册（`unregister_*`），否则热重载会留下幽灵注册。

---

## 4. 声明字段（类属性）

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` / `sign` / `description` | str | 显示名 / 唯一标识 / 一句话说明 |
| `permission` | str | 谁能启用：`everyone` / `owner` / `member`（见 `app/plugin/authority.py`） |
| `subscribe` | tuple[str, ...] | 订阅的事件类型（`message_group` / `message_private` / 通知/请求等，见 `domain/events.py`） |
| `category` / `tags` | str / tuple | 界面分组与标签 |
| `order` / `hidden` / `pinned` | int / bool | 界面排序、是否隐藏、是否置顶 |
| `default_config` | dict | 默认配置（与 `config_schema` 一起决定配置页） |
| `config_schema` | dict | 配置表单 schema（见 §5） |
| `requires` | tuple[str, ...] | 依赖的框架能力（缺了只告警不阻断，可 `missing_requirements()` 查询） |
| `provides` / `supersedes` | tuple[str, ...] | 声明/接管机制槽位（高阶，见 §10） |
| `supports_single_service` / `single_service_key` | bool / str | 声明"本模块是该类能力的唯一实现者"（如定时服务） |

---

## 5. 配置表单（config_schema）

`default_config` 给默认值，`config_schema` 描述怎么渲染：

```python
# config_schema.py
SCHEMA = {
    "groups": {
        "group_basic": {"label": "基础", "collapsible": False},
        "group_adv": {"label": "高级", "collapsible": True},
    },
    "items": {
        "enable": {"type": "boolean", "label": "启用", "default": True, "group": "group_basic"},
        "mode": {"type": "select", "label": "模式", "default": "a", "group": "group_basic",
                 "options": {"a": "模式 A", "b": "模式 B"}},
        "extra": {"type": "number", "label": "参数", "default": 3, "min": 1, "max": 10,
                  "group": "group_adv", "showIf": {"mode": "b"}},
        "model_id": {"type": "resource", "resource": "provider_model", "label": "模型",
                     "filters": {"provider_type": "embedding"}, "default": "",
                     "on_missing": "clear"},
        "api_key": {"type": "password", "label": "Key", "default": ""},
    },
}
```

- 常用类型：`boolean` / `number` / `string` / `textarea` / `select` / `list`（字符串列表） /
  `dynamic`（任意 JSON）/ `resource`（引用别的资源，如模型、人格预设）/ `password`（界面脱敏）。
- `showIf`：按其它字段的值显隐；`group`：归属分组；`min`/`max`/`step`：数值约束。
- `resource` 字段的悬挂引用策略：`on_missing: "clear"` 表示保存时清掉解析不到的值，默认 `keep`。
- 读配置：`self.config.get(key, default)`；不要直接读 Agent 配置（那是别的抽屉）。

---

## 6. 扩展点：你能往哪些地方挂钩子

### 6.1 事件流水线 — `@module_hook`

```python
from app.plugin import module_hook

@module_hook("message_group", order=100)   # event_type 可用 "*"
async def on_group_message(self, ctx) -> None:   # 参数由框架按 hook 注入
    ...
```

`subscribe` 声明要收哪些事件，`@module_hook` 决定同一事件里多个钩子的执行顺序。

### 6.2 LLM 流水线 — `@llm_hook`

四个阶段：`pre_request`（请求前，可暂停/防抖/改输入）、`post_response`（返回后，可拆分/清洗/改写）、
`pre_send`（每条发送前）、`post_send`（每条发送后）。

```python
@llm_hook("pre_request")
async def before_llm(self, ctx): ...
```

### 6.3 其它钩子

| 装饰器 | 时机 |
|---|---|
| `@before_send_hook(message_type="group")` | 消息即将发出，可改写 |
| `@send_hook(...)` | 发送完成 |
| `@api_hook(action="send_*")` | 任意 OneBot API 调用后 |
| `@bot_lifecycle_hook(state="login")` | 账号登录/连接/断开/出错（`login`/`connected`/`disconnected`/`error`） |
| `@event_completed_hook()` | 一个入站事件处理完 |
| `@tool_call_hook(event_type="group")` | 模型调用工具之后 |

### 6.4 指令 — `@command` / `@command_group`

```python
from app.plugin import command, command_group

@command("mymod", aliases=("mm",), description="示例指令", skip_llm=True)
async def cmd_mymod(self, ctx, arg: str = ""): ...
```

默认前缀与框架指令一致（`/` 或全角 `／`，见 `app/plugin/commands.py` 的 `DEFAULT_PREFIXES`）；
`skip_llm=True` 表示这条指令不触发 LLM 回复。

### 6.5 给模型加工具 — `ToolSource`

```python
from app.agent.loop.providers import register_tool_source, unregister_owner
from app.agent.tools.spec import ToolSpec

class MyTools:
    name = "my_tools"
    order = 50
    owner = self

    async def collect(self, scope):
        return [ToolSpec(name="my_tool", description="...", parameters={...}, handler=self.do)]

async def on_load(self):
    register_tool_source(self._runtime(), MyTools())

async def on_unload(self):
    unregister_owner(self._runtime(), self)
```

工具默认按账号/会话可用，权限与作用域见 [TOOL_STATE.md](TOOL_STATE.md)。

### 6.6 往提示词里加内容 — `ContextProvider` / `register_section`

- **动态数据块**（记忆、群记录、外部查询结果）：实现 `collect(scope)` 并 `register_context_provider`，
  `name` + `order` 决定位置；返回 `ContextBlock(role="user")` 表示"**这是用户提供的数据，不是指令**"
  （会并进本轮 user 消息，降低注入风险、保住 system 前缀稳定）。
- **固定提示词段**（一段规则/说明）：`register_section(runtime, Section(name, order, builder, source=..., layer=...))`。
  默认落在 **L3 钩子注入**；只有确实属于"规则"才显式 `layer="system_prompt"`（会并进 L1 那条 system）。
  五层模型见 [ARCHITECTURE.md §5](ARCHITECTURE.md)。

### 6.7 定时任务

```python
from app.plugin import register_daily_schedule
register_daily_schedule(self, "09:00", self.morning_report)
```

需要更细的调度时用框架的 `TaskScheduler`（`self.ctx.services.scheduler`），并在 `on_unload` 注销。

---

## 7. 调用框架能力

| 方法 | 用途 |
|---|---|
| `self._runtime()` | 取本账号的运行时（`AgentRuntime`），访问 `config`、`scheduler`、`capabilities` 等 |
| `self.config.get(key, default)` | 读本模块配置 |
| `self.llm_chat(messages)` / `self.llm_stream(...)` | 用账号的系统 provider 发一次 LLM 请求（Key/模型/重试由框架管） |
| `self.llm_turn(user_text, session_id=...)` | 完整跑一轮对话（取值+装配+工具循环+投递），可选 `send=True` 发出去、`record_history=True` 写历史 |
| `self.send_to(session_id, message)` | 唯一发送出口（限速、站内钩子、发送池都在这条路上） |
| `self.spawn(coro)` | 起后台任务，模块卸载时自动取消 |
| `self.framework_tools(session_id)` | 拿框架工具清单 `(specs, ToolContext)`（给"自己驱动一轮"的场景） |

---

## 8. 存储与数据目录

```python
from app.plugin import get_config_path, get_data_path
cfg_dir = get_config_path(self.module_name)     # data/module/configs/<name>/
data_dir = get_data_path(self.module_name)      # data/module/data/<name>/（自动创建）
```

- 配置本身由框架存在 `data/app.db`，模块不要自己再存一份配置；
- 业务数据（缓存、索引、日志）放 `data_dir`，自己管 schema 与清理；
- 目录必须可丢：删掉后模块要能自愈（重建空库）。

---

## 9. 权限与"谁该回复"

- `permission` 决定**谁能启用**这个模块（拥有者 / 管理者 / 所有人）；
- 多个模块同时想回复同一条消息时，由「插件响应策略」页面仲裁（`app/plugin/responder.py`），
  不是你抢到就算赢；模块要能接受"这一轮不是我"；
- 高危工具（如 OneBot 全量 API）走框架的工具审批，不归模块管。

---

## 10. 接管槽位（高级，少用）

只有 4 个可接管的槽位（清单与租约语义见 [FEATURE_REGISTRY.md](FEATURE_REGISTRY.md)）：
`supersedes=("assembly",)` / `("provider_invoker",)` / `("output",)` / `("agent",)`。
声明表示你要**替换**该机制；真正实现对应协议（如 `build(req) -> list[dict]`）后，框架会把槽位指向你。
只有"要复刻另一套组装/调用形态"时才这么做；普通功能一律用 §6 的扩展点。

---

## 11. 依赖自检

```python
requires = ("assembly", "provider_models")   # 需要的框架能力
```

加载时框架会跑一次自检：缺什么只记 warning，不阻断加载；模块可用
`self.missing_requirements()` 判断并降级（例如缺 provider_models 时不暴露 embedding 工具）。

---

## 12. 安装、升级、卸载

| 动作 | 做法 |
|---|---|
| 安装第三方模块 | 「功能插件」页上传 zip，或把目录放到 `data/module/plugins/<name>/` 后重载 |
| 升级 | 覆盖目录后热重载（`registry.reload_all()` / 页面上的"重新加载"） |
| 卸载 | 页面卸载（会执行 `on_unload` 并移走目录）；不要手删正在运行的模块目录 |
| 内置模块 | 随仓库发布，改它要改仓库并跑测试 |

不要依赖"模块目录一定可写"：内置模块是只读语义，写数据一律走 §8 的目录。

---

## 13. 测试与闸门

写模块至少给三类测试：

1. **契约测试**：钩子是否真被调用、工具是否真的产生副作用、配置缺省时的行为；
2. **回归测试**：曾经坏过的地方（例如"禁用后仍注册了定时任务"）；
3. **目录闸门**：`tests/architecture/test_plugin_layout.py`（结构）、`scripts/plugin_layout.py --check`。

```bash
venv\Scripts\python.exe -m pytest tests/plugin tests/plugins -q
venv\Scripts\python.exe -m pytest tests/architecture -q
venv\Scripts\python.exe -m ruff check app tests
```

测试策略（什么该测、删除模块时怎么处理测试）见 [TEST_POLICY.md](TEST_POLICY.md)。

---

## 14. 常见错误

| 症状 | 原因 / 修法 |
|---|---|
| 模块加载了但收不到事件 | `subscribe` 没列该事件类型；或账号上模块是禁用的（禁用不跑 `on_load`） |
| 改了模块代码不生效 | 内置模块需要重启进程；第三方模块用"重新加载"（会跑 `on_unload` + `on_load`） |
| 热重载后工具/定时器重复 | `on_unload` 没撤注册：`unregister_owner` / `unregister_section` / 定时任务注销 |
| 提示词里出现了重复内容 | 同一信息既 `ContextProvider` 又写进 `Section`；两者选其一 |
| 配置页字段不显示 | `config_schema` 的 `group` 没在 `groups` 里声明，或 `showIf` 条件不满足 |
| 保存配置报"资源不存在" | `resource` 字段引用了已删除的模型/人格，且 `on_missing` 未声明 `keep` |
