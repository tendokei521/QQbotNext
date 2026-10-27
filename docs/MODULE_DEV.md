# QQBot Next 模块开发文档

本文档面向模块开发者，说明如何编写模块、注册钩子，以及每个钩子收到的参数。

---

## 1. 模块目录结构

一个模块是一个目录：

- **内置模块**：`app/plugins/<name>/`（随 Python 包发布，只读语义；改它要改仓库）
- **外部安装模块**：`data/module/plugins/<name>/`（用户在 WebUI 上传 zip 安装，可写语义）

两种模块的**目录形态完全一致**——插件根下必须直接是 `main.py` + `module.json`：

```text
<插件根>/<name>/
  module.json         # 必须：清单（name/display_name/version/entry/api_version…）
  __init__.py         # 可选（有则按包加载，支持相对导入）
  main.py             # 必须：定义 Module(BaseModule)
  config_schema.py    # 可选：WebUI 配置表单
  service.py          # 可选：业务逻辑
  pages/index.html    # 可选：自定义配置页
  requirements.txt    # 可选：第三方依赖清单（安装时只提示，不自动 pip 安装）
```

### 1.1 `module.json`（v2 清单）

```jsonc
{
  "name": "bilibili_parser",     // 必填：全局唯一 id，须与目录名一致（[A-Za-z_][A-Za-z0-9_]*）
  "display_name": "B站视频解析",   // 必填：UI 标题
  "sign": "BilibiliParser",      // 可选：模块签名（命令/日志展示）
  "version": "1.1.0",            // 必填：semver，用于 UI 展示与更新比较
  "description": "……",           // 可选
  "author": "tendokei521",       // 可选
  "entry": "main.py",            // 可选，缺省 main.py
  "api_version": 1,              // 必填：插件 API 版本；不兼容的模块拒绝加载
  "requires": ["llm"],           // 可选：框架能力自检（缺失只 WARNING）
  "requirements": "requirements.txt" // 可选：第三方依赖清单
}
```

规则：

- 内置模块缺清单 → 加载时 WARNING + 用类属性兜底（`version=0.0.0`），不阻断；
- 外部模块缺清单 / `name` 非法 / `api_version` 不兼容 → **拒绝加载**，并在模块列表显示错误原因；
- 清单 `display_name` 优先于类属性 `name` 用于 UI，类属性 `name` 只给代码内部引用；
- 新增内置模块后跑 `python scripts/gen_module_manifests.py` 生成清单（`--check` 供 CI 校验）。

### 1.2 安装 / 更新 / 删除（外部模块）

- 安装：WebUI「功能模块 → 安装插件」上传 zip。服务端在 `data/module/.staging/` 解压并
  **归一插件根**（自动剥掉 `repo-main/`、`__MACOSX/` 这类包装层），校验清单与入口后
  原子换入 `data/module/plugins/<name>/`；失败不留半成品。
- 更新：对已安装模块上传新版本 zip（覆盖安装），旧版本备份到 `data/module/.backup/`，
  配置/权限/数据（`data/module/data/<name>/`）全部保留。
- 删除：只删源码目录；配置与数据默认保留（可选清理）。内置模块**不可删除/不可覆盖**。
- 历史嵌套坏装：`python scripts/fix_module_layout.py --yes` 一键上提（先备份，幂等）。

最简单的 `main.py`：(清单仍必须存在)

```python
from app.plugin import BaseModule

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

### 名册身份（`manager` / `owner` 档从哪来）

`permission_role` 取的是「名册身份 + 群角色」里的最高档，两条来源缺一不可：

| 来源 | 配置位置 | 作用 |
|---|---|---|
| 群角色 | QQ 群给的 `owner` / `admin` | 只在本群有效 |
| 拥有者名册 `agent_owner_list` | Agent 配置 | **跨群生效**，命中即 `owner` 档 |
| 管理者名册 `agent_manager_list` | Agent 配置 | **跨群生效**，命中即 `manager` 档 |

> 账号级 `owner_id`（连接配置）**已废弃且不参与判定**（v3 起 `event.is_bot_owner` 只由名册推出）；
> 名册按账号各存一份的现状与改进方向见 [`identity-and-capability-console.md`](identity-and-capability-console.md)。

所以：**`permission = "owner"/"manager"` 的模块要有人能触发，就必须让人出现在名册里**——
把人加进 Agent 配置的拥有者/管理者名册（当前拥有者身份的**唯一**来源）。

> 排查提示：这两档不通过时框架**不写日志**（权限过滤整体是静默的），表现为
> 「模块一声不响、LLM 反倒出来搭话」。`ModulePermissionNode` 与 `AgentNode` 用的是
> 同一套 `compute_event_permission`，两边名册必须同源，否则会出现
> 「Agent 认为你是 owner、模块认为你是 member」的错位（P2 修复：模块侧此前漏传名册）。

---

## 2.5.1 同群多账号：响应权策略（声明制能力）

同一群里配了多个 Bot 账号时，默认**每个账号各自响应**（账号级独立，互不干扰）。
需要「同一个群里只让一个账号响应某个模块」时，用 WebUI 的**响应权策略**
（设计见 [`multi-account-design.md`](multi-account-design.md) §6）：

```python
from app.plugin import BaseModule

class Module(BaseModule):
    name = "我的模块"
    # 声明支持响应权策略（历史属性名，语义 = "参与跨账号仲裁"）
    supports_single_service = True
```

四层门控（未声明模块完全不受影响）：

| 层 | 行为 |
|---|---|
| WebUI | `/api/modules` 返回 `single_service` 字段；为 false 时**不显示**策略入口 |
| 写入 | `POST /api/modules/{module}/responder` 只收声明模块，其余 403 |
| 运行期 | 候选集合只含「该账号该模块启用 ∧ 在线 ∧ 在群 ∧ 权限通过」的账号 |
| 迁移 | 旧 `single_service/multi_group` 自动迁成 `channel_policies`（`delegate/open`），旧行为不变 |

策略语义（存储：`channel_policies`，按「模块 × 群」）：

| policy | 行为 |
|---|---|
| `all`（默认） | 候选全部响应 |
| `dedupe` | 候选 ≥2 时只选一个（稳定顺序：账号配置顺序） |
| `delegate` | 固定主账号；主账号不可用时按 `fallback` 处理 |
| `leader` | 主账号优先，掉线自动由次选接管 |

`fallback=open`（默认）：主账号不可用时降级到其它候选并 WARNING 一次（功能不静默消失）；
`fallback=closed`：本轮不响应（显式配置者的选择，UI 预览可查 `reason/degraded`）。

**该不该声明**：只有真正「按群收事件并响应」的模块才该声明。纯 LLM 策略模块
（`subscribe = ()`，如组装/输出/记忆）走不到权限节点，声明了也永远不生效——
`tests/plugin/test_single_service_declaration.py` 里有反向约束会拦住这种误声明。

---

## 2.5.2 装配 ≠ 可跑：`on_online` / `on_offline`

**账号一创建就会被装配**（`account_id` 在添加账号时就有，配置不依赖连接），所以
`on_load()` 可能在**账号还没连上 QQ** 的时候执行。因此生命周期分成两段：

| 钩子 | 何时调用 | 该做什么 |
|---|---|---|
| `on_load()` | 账号装配时（**不要求连接**） | 建实例、读配置、挂工具/配置视图；**不要**启动计时器 |
| `on_online()` | 账号真的连上之后（可重复） | 武装定时器、启动轮询/订阅等需要真实连接的循环 |
| `on_offline()` | 账号断开/出错（可重复） | 停掉 `on_online` 启动的一切 |
| `on_unload()` | 模块卸载/热重载 | 释放资源（框架还会注销该模块的定时任务与后台任务）|

```python
class Module(BaseModule):
    async def on_load(self):
        self._timer = build_my_timer(self)      # 只装配，不启动

    async def on_online(self):
        self._timer.start()                     # 连上才开始跑

    async def on_offline(self):
        self._timer.stop()                      # 断开立刻停
```

规则（`tests/application/test_account_offline_assembly.py` 守门）：

- **离线不跑任务**：账号未连接时，框架不会武装任何计时器，也不会注册模块级定时任务；
  `register_daily_schedule(...)` 在离线时直接返回 `False`，上线后由 `on_online` 补注册。
- 判断"是否在线"用 `self._runtime()` 的 `online` 属性，不要用 `bot_id` 是否存在
  （账号一创建就有 id）；`bot` 值为 `None` 只说明还没连接。
- 需要 QQ 平台数据的模块（群列表、好友、昵称）在离线时必须能降级到空列表，不要抛异常。

---

## 2.6 全局能力注册表（FeatureRegistry）

框架提供统一的“能力接管”机制，插件可以声明自己接管/禁用某个框架内置能力，并在插件
卸载/禁用时自动恢复。

> 详细说明、API 与多租约规则见 [`docs/FEATURE_REGISTRY.md`](FEATURE_REGISTRY.md)。

### 2.6.1 插件声明接管

在 `Module` 上声明 `supersedes`：

```python
from app.plugin import BaseModule

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

## 2.7 统一命令：`@command` / `@command_group`

「输入某个词就干某件事」不必再自己写 `@module_hook` + 文本匹配 + `event.llm.stop()`。
框架有一套统一的命令解析与分发（`app/plugin/commands.py`），口径对齐 AstrBot 的
`filter.command` / `filter.command_group`：

```python
from app.plugin import BaseModule, command, command_group

class Module(BaseModule):
    @command("log", description="发送本轮次 user 日志")
    async def log(self, event):
        ...                                   # 默认已截断 LLM

    @command("poke", aliases=("戳",), skip_llm=False)
    async def poke(self, event):
        ...                                   # 回一条后，本轮照常交给 LLM

    @command_group("memory", description="长期记忆")
    async def memory(self, event):             # 只发 "/memory" 时走这里
        await event.reply(render_group_help(iter_module_commands([self]), "memory"))

    @memory.command("list", description="列出记忆")
    async def memory_list(self, event):
        args = event.command.args               # "/memory list a b" → ["a", "b"]
```

### 2.7.1 参数

| 参数 | 含义 |
|---|---|
| `name` | 命令词；命令组自动拼成 `"<组> <子命令>"`（可多词） |
| `aliases` | 别名（同样受前缀约束；组里会自动带上组名） |
| `description` | 说明，供帮助清单渲染 |
| `group` | 所属命令组（一般由 `@xxx.command` 自动填） |
| `skip_llm` | **隐藏参数**，默认 `True`；见 2.7.4 |
| `order` | 同分命中时的优先级（越小越先） |
| `prefixes` | 唤醒前缀，默认 `("/", "／")`；传 `("", "/")` 可允许裸词 |

命令组可以级联：`@grp.group("sub")` 再 `.command(...)`，名字会拼成 `"grp sub xxx"`。

### 2.7.2 解析口径

- 前缀：默认 `/log`、`／log`（全角斜杠）都认；**不带前缀默认不算命令**（避免自然语言误触）；
- 空白归一（`\s+` → 单空格）、命令名大小写不敏感、按**词边界**匹配（`/opened` 不会命中 `open`）；
- 命令名**允许含空格**（命令组），剩余部分按空格切成 `event.command.args`；
- 同一条消息命中多条时只跑**最具体**的一条：先比命令词数（`memory list` 胜过 `memory`），
  再比字符长度，然后比 `order`、模块名。

### 2.7.3 命令参数：按签名自动绑定

处理器 `event` 之后的参数就是命令参数，框架**按签名自动转换**（对齐 AstrBot 的
`validate_and_convert_params`，加载期解析一次签名，运行期只做转换）：

```python
@command("ban")
async def ban(self, event, qq: int, minutes: int = 10, reason: GreedyStr = ""):
    ...                      # "/ban 12345 30 刷屏 广告" → qq=12345, minutes=30, reason="刷屏 广告"
```

| 写法 | 行为 |
|---|---|
| `n: int` / `x: float` | 转数字，转不动 → 类型错误提示 |
| `on: bool` | `true/yes/y/on/1/真/是` → True，`false/no/n/off/0/假/否` → False |
| `text: str` | 原样（单段） |
| `text: GreedyStr` | **吞掉全部剩余文本**（含空格），必须声明在最后一个参数 |
| `n: int = 10` | 没给就用默认值；默认值的类型优先于注解决定转换目标 |
| `v: int \| str \| None = None` | 默认值/注解为 None 时按 AstrBot 口径：纯数字转 `int`，否则原样字符串 |
| 无注解 | 纯数字转 `int`，否则原样字符串 |
| 多余参数 | 忽略（不报错） |

参数不合法（缺必填 / 类型不对 / `GreedyStr` 不在最后）时，框架会**回一条用法提示**并照常
截断 LLM，不会再让模型对着错误文本瞎编：

```
⚠️ 参数不足：缺少 n
用法：/scale <n: 小数> [times: 整数 = 2]
```

绑定后的实参同时写在 `event.command.params`（`{"n": 7}`），观察者/日志可直接读。

### 2.7.4 隐藏参数 `skip_llm`：命中即截断 LLM

`skip_llm` 默认 `True`：命令命中后框架替你 `event.llm.stop()`，本轮**不进 LLM**
（不写就等于自动截断，不会再出现"命令回完模型又搭一句"）。

确实想让模型接着说的场景，显式写 `skip_llm=False`；子命令缺省继承组上的设定。
处理器抛异常、或参数不合法时**同样截断** LLM，并记日志。

### 2.7.5 分发时机

入站节点链：`router(100) → permission(110) → command(115) → invoke(120) → agent(130)`。

- 在**权限之后**：`permission="owner"` 的模块，命令一样受权限约束；
- 在**模块通用钩子之前**：命令先落定，`@module_hook` 观察者（如 group_log）仍照常收到事件；
- 在 **LLM 之前**：所以命令能直接截断 LLM；
- 命令模块**不需要**写 `subscribe`：只声明命令时框架自动补 `message_group` / `message_private`；
- 解析结果挂在 `event.command` 上（`name` / `args` / `text` / `prefix` / `module` /
  `skip_llm` / `params`），后续模块与处理器都可读。

> 与 `#llm` 指令的关系：`#llm xxx` 是 Agent 层的内置指令表（`app/agent/commands.py`），
> 在 LLM 流水线**内部**分流；本节的命令是模块层、在 LLM **之前**分发。两者互不影响。

---

## 3. 模块流水线钩子：`@module_hook`

### 3.1 用法

```python
from app.plugin import BaseModule, module_hook

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
from app.plugin import BaseModule, llm_hook

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
from app.plugin import BaseModule, send_hook

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
from app.plugin import BaseModule, before_send_hook

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
from app.plugin import BaseModule, api_hook

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
from app.plugin import BaseModule, bot_lifecycle_hook

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
from app.plugin import BaseModule, event_completed_hook

class Module(BaseModule):
    @event_completed_hook(order=10)
    async def after_event(self, ctx):
        # ctx.event / ctx.duration_ms / ctx.state
        pass
```

在入站事件模块链处理完成后触发；LLM 流水线是后台异步任务，不等待其完成。

### 4.6.5 `@tool_call_hook`：LLM 工具调用后

```python
from app.plugin import BaseModule, tool_call_hook
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
from app.plugin import BaseModule, llm_hook

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

### 4.7.1 工具循环住在 `app/agent/loop/tool_step.py`（P1 起）

一轮「请求 → 工具批次 → 回填」的循环**只有这一处**实现：

```python
from app.agent.loop.tool_step import StepRequest, run_blocking_step, run_stream_step

step = await run_blocking_step(StepRequest(
    runtime=runtime, chain=chain, messages=messages,
    params={"model": ..., "tools": ..., "tool_executor": executor},
    max_rounds=5, tool_ctx=tool_ctx, modalities=modalities,
))
# step.kind: assistant / max_rounds / error / empty
# step.text / step.tool_results / step.tool_calls / step.usage
```

**provider 适配器只发一次请求**：它把这些工具调用报在 `LLMResponse.tool_calls`
里，不执行、不再循环。因此：

- 直接 `await provider.chat(..., tool_executor=...)` 的代码**拿不到工具循环**了
  （这是 P1 的对外可见变化）。要么改用 `chat_with_fallback(..., tool_executor=...)`
  （签名不变、内部转调上面的 runner），要么直接用 runner。
- 接管 `runtime.invoker` 的插件要自己负责循环：`params` 里会带 `tool_executor`，
  但框架不会再往下传——循环归调用策略那一层。
- 流式侧同理：`chat.stream_response` 消费 `iter_stream_step` 的事件
  （`("text", 句子)` / `("round_end", RoundSummary)`），所以它能在句间被打断。

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

### 4.7.2 能力声明：`runtime.capabilities`（P6 起）

有些键其实是**能力**而不是偏好——`stream_output` / `speech_via_tool` 描述"这套运行时
能怎么产出"，`context_expand_enable` 其实是在问"有没有人提供上下文展开"。
框架不再靠读配置猜谁装了，改成让提供方**声明**：

```python
from app.agent.capabilities import (
    CAP_CONTEXT_EXPAND, OUTPUT_SPEECH, registry_of,
)

async def on_load(self) -> None:
    runtime = self.ctx.services.runtime(self.bot_id)
    registry_of(runtime).declare_output(OUTPUT_SPEECH, source="my_output_module")
    registry_of(runtime).declare_tool(CAP_CONTEXT_EXPAND, source="my_context_module")

async def on_unload(self) -> None:
    registry_of(runtime).revoke_output(source="my_output_module")
    registry_of(runtime).revoke_tool(CAP_CONTEXT_EXPAND, source="my_context_module")
```

规则：

- **声明优先于配置**：声明了流式，即使 `stream_output=false` 也按流式跑；
- **撤销只能撤自己声明的**（`source` 不匹配就不动），卸载即回到"没人提供"；
- 没人声明时**回落到配置键**，所以没装模块的旧实例行为不变；
- 输出能力三选一：`stream` / `blocking` / `speech`（发言通道也是边产边发）。

### 4.7.3 高危工具审批通道：`runtime.approval_channel`（P6 起）

`tool_approval_enable` 打开后，OneBot 里 `sensitivity` 为 `high`/`critical` 的工具在
执行前必须获批。通道由业务/插件提供（框架不假设媒介是私聊还是 WebUI）：

```python
class MyApprovalChannel:
    async def ask(self, runtime, *, tool, args, ctx):
        from app.agent.tools.approval import ApprovalOutcome
        ok = await my_ui_confirm(tool, args)      # 你自己的确认流程
        return ApprovalOutcome.ALLOWED_ONCE if ok else ApprovalOutcome.REJECTED

runtime.approval_channel = MyApprovalChannel()
```

**fail-closed**（这几个是硬规矩，不是建议）：

- 没有通道 → `UNAVAILABLE` → **拒绝执行**；
- 通道抛异常 / 返回不认识的值 → 同样按拒绝；
- 只有 `ALLOWED_ONCE` 才执行，且只对**这一次**调用有效（不缓存、不升级）；
- 拒绝话术按 `REJECTED` / `TIMEOUT` / `UNAVAILABLE` 区分，asked 与 decided 都留痕。

### 4.7.4 注册一段提示词：`runtime.prompt_sections`（P1 起）

默认提示词由 **12 个段**组成（人格 / 行为 / 表情 / 定时 / 主动性 / 元信息 / 技能 /
记忆 / 背景 / 群聊记录 / 历史 / 本轮），顺序由每段的 `order` 决定，定义在
`app/agent/assembly/builtin_sections.py`。模块只想**加一段**时不必接管整个装配器
（`supersedes=("assembly",)` 仍可用于整体替换，但那是另一回事）：

```python
from app.agent.assembly.sections import Section, register_section, unregister_section


def build_my_block(req):
    """(PromptRequest) -> list[dict]；不适用时返回 []，不要返回空消息。"""
    if not req.cfg("my_knowledge_enable", True):
        return []
    return [{"role": "system", "content": "### 我的知识\n…"}]


class Module(BaseModule):
    async def on_load(self) -> None:
        runtime = self.ctx.services.runtime(self.bot_id)
        register_section(runtime, Section(
            name="my_knowledge",        # 稳定标识：同名同 scope 覆盖（幂等）
            order=85,                   # 升序；取值见下表
            builder=build_my_block,     # 只读 req、无副作用
            source="my_module",         # 只用于日志/排查
        ))

    async def on_unload(self) -> None:
        runtime = self.ctx.services.runtime(self.bot_id)
        unregister_section(runtime, "my_knowledge")
```

要点：

- 注册表按 **runtime 实例隔离**（`runtime.prompt_sections`），不同 bot、不同模块互不串；
  12 个默认段在首次解析时自动注入，插件**不要**手动注册它们；
- `builder(req)` 只读请求、不产生副作用；返回 `[]` 表示"这段本轮不适用"（不产生空消息）；
  `describe()` 与装配各跑一次 builder，所以它必须幂等/无副作用（不要在里面发请求、写库）；
- `scope="agent"`（默认）只影响本 runtime；同名 `scope="global"` 段会被 agent 段**遮蔽**
  （适合"所有 bot 通用、个别 bot 想改"的注入）；
- `complete=True` 表示该段声称产出**整条 system**：一次解析最多允许一个，两个以上会让
  `resolve()` 抛 `ValueError`，装配器留 `error` 日志后中止本轮（绝不发畸形 prompt）；
- 单段 builder 抛异常时装配器会**跳过该段**并记 `error` 日志（其余段照常），
  不会因为记忆召回之类旁路故障让整轮没有回复；
- `PromptRequest.extra_blocks` 是旧兼容通道（位置固定在群聊记录之后、历史之前）：
  能改的用 section，只有"一次塞多块、且不挑位置"才用它。

**order 取值约定**（内置段占 10 的倍数；插件从 **200 起**，避免与以后新增的内置段撞号）：

| order | 段 | 含义 |
|---|---|---|
| 10 | `persona` | 人格 |
| 20 | `behavior` | 行为规则 |
| 30 | `emoji_hint` | 表情提示 |
| 40 | `schedule` | 定时任务协议 |
| 50 | `proactive` | 主动性协议 |
| 60 | `message_meta` | 消息元信息说明 |
| 70 | `skills` | 技能说明（可多块） |
| 80 | `memory` | 记忆召回 |
| 90 | `background` | 背景（指代 + 聊天环境） |
| 100 | `group_log` | 群聊环境记录 |
| 105 | `extra` | **兼容通道**：`req.extra_blocks`，插件不要占用这个段号 |
| 110 | `history` | 会话历史 |
| 120 | `user` | 本轮消息 |
| 200+ | 插件段 | 想插进内置序列中间就取同区间的小数（如 85）；想排最后取 ≥200 |

同 `order` 的段按 `name` 排序兜底（同一输入必然同一字节，便于快照/前缀缓存）。

**卸载时必须自己撤**：注册表不跟模块生命周期自动清理。`on_unload` / `on_disable`
里不调 `unregister_section`，同一 bot 热重载模块就会越积越多段（同名覆盖还能幂等，
换名字就成了重复注入）。若注册的是对默认段的**同名覆盖**，撤销后会自动恢复内置版本。

**排错**：`PromptAssembler().describe(req)` 返回 `[{block, role, chars, messages}]`，
`block` 就是 section 名（空段也留一行 `role="-"`）；`prompt_dump_enable` 落盘的
是最终发出的 messages，两者可对照定位"这段到底进没进 prompt"。

### 4.7.5 提示词缓存与前缀稳定（Q5）

上游的前缀缓存（OpenAI / DeepSeek 的自动缓存、Anthropic 的显式缓存）只认**逐字节
相同**的开头：从第 1 条 system 的第一个字节起，命中多少算多少。所以改 prompt 时有
三条硬约束：

1. **system 前缀必须逐字节稳定**。同一会话、同一配置下反复请求，system 的拼接顺序、
   分隔符、文案一个字都不能变；不要往 system 里塞时间戳、随机数、`set` 的遍历结果、
   未排序的 `json.dumps`。需要"本轮不同"的内容放进 user 消息或工具结果，别动 system。
2. **改 tool 定义会使缓存失效**。tools 数组在请求里排在 system 之前（Anthropic 把它
   和 system 一起算进前缀），增删工具、改描述、改参数 schema 都会让整段缓存作废。
   工具集变化本身是低频事件，但**不要每轮**根据会话状态动态裁剪工具。
3. **`prompt_cache_enable`**（Agent 页「高级功能 → 模型缓存」，默认开）：给支持显式
   缓存的 provider 在 system 末尾打缓存标记，命中后这部分输入按折扣计费、首字更快。
   不支持的网关可能直接拒收该标记，关掉即回到"不带标记"的旧请求形态。

组装层提供了三个纯函数用于回归/排错（`app/agent/assembly/prompt_cache.py`）：
`system_prefix_fingerprint(messages)`（只看 system 段的短指纹）、
`stable_messages_digest(messages)`（全量 messages 的稳定摘要）、
`is_prefix_stable(prev, cur)`（`cur` 是否只是在 `prev` 尾部追加）。测试里拿它们锁
"同一输入必然同一字节"，改组装顺序时会立刻变红。

---

### 4.7.6 工具结果的表达力与实现要点（Q6）

工具返回的不只是"一段文本"。`ToolResult`（`app/agent/tools/result.py`）现在可以表达：

| 字段 | 含义 | 谁消费 |
|---|---|---|
| `blocks` | 内容块：`text` / `image`(`sha`/`mime`/`alt`) / `ref`(`label`/`target`) | `plain()` 渲染成模型可读文本 |
| `is_error` + `code` | 结构化错误（`ERROR_CODES` 里的稳定码） | 遥测、`tool_call_hook` 的 `ctx.extra["code"]` |
| `meta` | 观测信息（如落盘路径 `spill`） | 事件日志（`tool/result`）、压缩 pruner |
| `additional_contexts` | 工具要**额外告诉模型**的上下文 | 循环体汇总成**一条** `[工具补充]` user 消息 |
| `concludes_turn` | 工具要求**结束本轮**（`finish` 类语义） | 循环体停止下一次请求，结局记 `completed` |

两类"副作用"的边界：

- `additional_contexts` 是**给模型看的**，不是给用户看的；一次工具批次里多条会合成一条消息
  （多条 user 消息会让"谁在说话"变模糊）。
- `concludes_turn` 不会替你生成正文：工具那轮模型没写话，回复就是空，但轮次结局是
  `completed`（工具明确表示办完），不是 `silent`。
- 要让调用过程"给人看"，用 `ToolSpec.present_call` / `present_result`（默认 `None` = 标准日志行）。
  呈现是日志/界面层，**不影响**执行与回给模型的文本。

### 4.7.7 在"具体一步"插一脚：中间件（Q7）

模块以前只能在固定钩子阶段（`pre_request` / `turn_completed` …）或整体接管（`supersedes`）
之间二选一；中间件（`app/agent/loop/middleware.py`）补上中间那一档：

```python
from app.agent.loop.middleware import Middleware, register_middleware, unregister_middleware

async def on_load(self) -> None:
    runtime = self.ctx.services.runtime(self.bot_id)
    register_middleware(runtime, Middleware(
        point="tool_call.before", order=100, source="my_module", fail_closed=False,
        handler=my_audit_handler,      # async (payload, *, runtime, ctx) -> dict | None
    ))

async def on_unload(self) -> None:
    unregister_middleware(runtime, source="my_module")   # 不撤会一直生效
```

三个钩点与 payload：

| 钩点 | payload（可改写） | 典型用途 |
|---|---|---|
| `tool_call.before` | `{name, args, call_id}` | 审计、脱敏、按策略拦截 |
| `tool_result.after` | `{name, call_id, result_text, is_error, code}` | 统一裁剪/打码、给结果加提示 |
| `messages.before_send` | `{messages}` | 注入一段上下文、按需要求终止 |

语义要点：

- 按 `order` 升序串行；返回 `None` = 不改，返回 dict = 合并替换；handler 拿到的是浅拷贝，
  **原地改不生效**；
- 返回 `{"abort": "原因"}` → 本步中断（工具调用不派发 / 本轮不请求 LLM）；
- `fail_closed=True` 时 handler 抛异常也按中断处理；`False`（默认）只留痕并继续；
- 没有注册任何中间件时**零开销**（不构造 payload、行为与改造前一致）。


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

## 4.9 基础接口：模块写业务所需的全部原料

> 这一节是「**业务逻辑请自己写，但原料由框架给足**」的落地清单。
> 有了这五件，主动回复 / 定时播报 / 群总结这类模块**不需要抄框架**。

### 4.9.1 `self.send_to(session_id, message)` —— 按会话发消息

唯一发送出口：自动分流群聊/私聊，正文开头的 `[reply]` / `[@qq]` 会解析成真正的消息段，
并且一定经过出站节点链 + `before_send` / `send` / `api` 钩子。

```python
await self.send_to("group_123", "晚上好")
await self.send_to("private_456", "[reply]好的")     # 引用触发本轮的消息
resp = await self.send_to("group_123", "带图...")     # 返回 OneBot 响应（拿 message_id）
```

```python
# 前端 channel.py 的等价签名（也是唯一发送出口）
from app.agent.delivery.channel import send_item
await send_item(bot, "group_123", "hi", runtime=self.ctx.services.runtime(self.bot_id))
```

### 4.9.2 `self.spawn(coro)` —— 带归属的后台任务

**不要直接用** `ctx.services.task_manager.create_task`：它的 owner 默认 `"system"`，
模块卸载时不会被清理（热重载会留下孤儿任务）。`spawn` 自动带上
`module:<模块名>:<bot_id>`，卸载/重载级联取消。

```python
async def on_load(self):
    self.spawn(self._loop(), name="demo-loop")

async def _loop(self):
    while True:
        await asyncio.sleep(600)
        ...
```

### 4.9.3 `self.llm_turn(...)` —— 无事件的完整一轮对话

和 `self.llm_chat()` 的区别：那个只发**一次裸请求**（没有历史/组装/工具/记忆）；
`llm_turn` 吃框架的全部上下文能力，而且**不需要真实事件对象**。

```python
result = await self.llm_turn(
    "总结一下今天群里聊了什么",
    session_id="group_123",
    record_history=False,     # 默认 False：模块自己决定要不要污染会话
)
if result.text:
    await self.send_to("group_123", result.text)

# 碰流式：逐句回调，边产边发
await self.llm_turn("讲个故事", session_id="private_456", stream=True, send=True)

# 失败不抛：result.error 非空 / result.blocked 表示被钩子拦下
```

`TurnResult` 字段：`text` / `messages`（本轮最终请求体，对账用）/ `tool_results` /
`usage` / `blocked` / `error` / `session_id`，以及 P2 起新增的
`reason`（`TurnReason`，见下表）/ `rounds`（本轮实际跑了几轮）/ `interrupted`（是否被取消）。

`reason` 取值：`completed` / `silent` / `blocked` / `max_rounds` / `max_tokens` /
`error` / `aborted`。它由事实推导（轮数用尽、`finish_reason`、异常、取消），
**不要**再从回复文本猜"这轮算不算成功"——兜底话术不会把空回答变成 `completed`。
流式主路径（`chat.stream_response`）把同一结论写进 `ctx.state["turn_reason"]`。

服务侧等价入口：`ctx.services.llm.turn(...)`。

### 4.9.4 `self.sessions` —— 会话历史读写

```python
mgr = self.sessions                       # → SessionManager（按 bot_id 的进程级单例）
mgr.add_message("group_123", "assistant", "我说的话")
history = mgr.get_history("group_123", limit=20)
raw = mgr.history_snapshot("group_123")   # 未投影的原始条目（自带渲染器时用）
```

> 单例 key 已归一成字符串，所以传 `self.bot_id`（int）或 `"123"` 拿到的是同一个实例。

**P4 起：历史是"追加式事件日志"，`messages` 是派生缓存**。`add_message` /
`upsert_streaming_assistant` / `mark_last_assistant_message_id` 的签名与行为不变
（内部多写一条事件），`get_history` / `history_snapshot` 读到的仍是同一个视图，
所以模块侧无需改动。想看"这一天到底发生了什么"（含工具调用、轮次结局）时，
用 `SessionLog`：

```python
from app.agent.session.session_log import SessionLog

log = SessionLog(mgr.history, mgr.get_session("group_123").task_id)
events = log.events()              # 只追加的全量事件（turn/end 里带 reason）
messages = log.derive_messages()   # 与 get_history 同形的投影
```

> 投影只取 `user/message` 与 `assistant/message`；工具调用/结果只入日志、**不进模型可见历史**
> （工具结果要不要跨轮持久化是另一个产品决策）。存量库需要先跑
> `python scripts/migrate_session_events.py` 才会有事件。

### 4.9.5 `self.framework_tools(session_id)` —— 框架工具集只读视图

```python
specs, tool_ctx = await self.framework_tools("group_123")
from app.agent import build_tools, make_executor          # 与框架同一套执行器
resp = await self.llm_chat(
    messages,
    tools=build_tools(specs),
    tool_executor=make_executor(specs, tool_ctx),
)
```

拿到的就是框架这一轮会给模型的那批工具（会话查询 / 上下文展开 / OneBot 等），
所以模块自建的 agent loop 与主链路能力一致。

### 4.9.6 定时任务：`SCHEDULES` + 运行时增删改查

```python
class Module(BaseModule):
    SCHEDULES = {
        "0 9 * * 1-5": "weekday_job",   # cron 写法
        "23:30:00": "nightly",          # 或 HH:MM[:SS]
    }

    async def weekday_job(self):
        ...
```

配置驱动（时间来自模块配置）时用 `register_daily_schedule` 或直接操作调度服务：

```python
scheduler = self.ctx.services.scheduler
await scheduler.register("demo:12345:daily", "08:00", self.daily_push)   # 注册/改期
scheduler.list_jobs(prefix=f"demo:{self.bot_id}")                        # 查（含下次触发时间）
await scheduler.update_job(key, "09:30")                                 # 改期（保留 handler）
await scheduler.run_job_now(key)                                         # 立即跑一次
await scheduler.unload_module("demo", self.bot_id)                        # 卸载时统一清理
```

> `SCHEDULES` 是**编译期声明**（时间固定）；时间要由配置决定时用动态注册。
> 两者**二选一**，同时用会双注册。

### 4.9.7 贡献上下文块与工具（注册制，可组合）

不想整体接管装配器、只想"加一块"时，注册贡献者即可：

```python
from app.agent.loop.providers import (
    SimpleContextProvider, SimpleToolSource,
    register_context_provider, register_tool_source,
)

async def on_load(self):
    rt = self.ctx.services.runtime(self.bot_id)
    register_context_provider(rt, SimpleContextProvider(
        "my_memory", lambda scope: "这是我要注入的一段背景", order=50, owner=self,
    ))
    register_tool_source(rt, SimpleToolSource(
        "my_tools", lambda scope: [my_tool_spec], order=50, owner=self,
    ))

async def on_unload(self):
    from app.agent.loop.providers import unregister_owner
    unregister_owner(self.ctx.services.runtime(self.bot_id), self)
```

- 上下文块渲染在 `group_log` 之后、`history` 之前（块名 `extra`，`describe(req)` 可见）；
- 工具源产出的工具与框架工具走同一套四类开关过滤；
- **按 order 收集、互相不排他**（与 `assembly` 那种整体接管不同）。

### 4.9.8 接管"一轮回复怎么产出"（`output` 槽）

```python
from app.domain.message import Message

class Module(BaseModule):
    supersedes = ("output",)          # 启用即接管，禁用/卸载自动还原

    async def respond(self, ctx, *, should_stop=None):
        # 自己决定怎么产：一次拿全 / 逐句流式 / 走工具 / 别的形态
        yield Message.from_text("你好")
```

- 契约：`respond(ctx, *, should_stop=None)` 异步产出 `Message`，**不发送**；
  发送与 `pre_send`/`post_send` 由流水线统一做（模块不必操心）。
- 卸载/释放后回到 `None`（框架按 `stream_output` 自动选择，行为不变）。
- 现有的 `BlockingOutput` / `StreamOutput` 就是它的两个默认实现，
  可参考 `app/agent/delivery/output.py`。

---

## 4.10 v3 内置的 9 个功能模块（配置归属的既定事实）

v3 把「高级功能」从 agent 配置页整体搬进了模块。**这 9 个模块随仓库发布、随账号自动装配**，
每个模块自己持有配置（`config_schema`），框架侧机制只认 `policy_view`。

| 模块 | 目录 | 键数 | 性质 | 用哪些基础接口 |
|---|---|---|---|---|
| 输出策略 | `llm_output` | 25 | **高级接口**（`supersedes=("output",)`） | `output` 槽（`mode=text/stream/speech`） |
| 组装策略 | `llm_assembly` | 10 | **高级接口**（`supersedes=("assembly",)`） | `assembly` 槽（`build(req)`） |
| 消息元信息 | `llm_message_meta` | 14 | 业务 | `@llm_hook("pre_request")` + `raw_user_text` |
| 上下文增强 | `llm_context` | 10 | 业务 | `context_providers` + `tool_sources` + `pre_request` |
| 长期记忆 | `llm_memory` | 17 | 业务 | `context_providers` + `tool_sources` + `pre_request`/`turn_completed` + `@command` |
| 知识库 | `llm_knowledge` | 3 | 业务 | `tool_sources` |
| 工具扩展 | `llm_tools_extra` | 9 | 业务 | `tool_sources`（OneBot + MCP） |
| 主动消息 | `llm_proactive` | 17 | 业务 | `message_observed`/`bot_sent` 订阅 + `spawn` + `llm_turn` + `send_to` |
| 定时任务 | `llm_schedule` | 3 | 业务 | 自建 `TaskScheduler` + `tool_sources` + `llm_turn` + `send_to` |

> **业务模块一律不声明 `supersedes`。** 只有"更换机制 / 接管行为"的模块才声明。

### 4.10.1 配置取用口径（写框架代码时唯一允许的读法）

```python
from app.agent.policy import policy_config, policy_get, policy_view

policy_get(runtime, "memory", "memory_enable", False)   # 只关心一个模块的键
policy_view(runtime).get("assembly_profile")            # 机制跨模块读键时用这条链
```

### 4.10.1b 广播型钩子（业务订阅"事实"）

流水线不认识业务，只广播事实；业务自己订阅：

| 阶段 | 时机 | 典型用途 |
|---|---|---|
| `message_observed` | 用户说话了（进入 LLM 前） | 主动消息：重置沉默、安排下次 |
| `bot_sent` | Bot 发出一条消息后 | 主动消息：重置计时 |
| `turn_started` | 一轮真正开始请求模型前（已完成组装） | 观测：记录本轮 model/tools 指纹 |
| `step_completed` | 每跑完一个工具轮（一次请求） | 观测：逐轮耗时/token/工具数（`ctx.state["step"]` 里是 `RoundSummary`） |
| `turn_stopping` | 一轮即将收尾（写历史/遥测之前） | 观测：拿到 `ctx.state["turn_reason"]` 做统计 |
| `turn_completed` | 一轮处理结束 | 记忆蒸馏、统计、审计 |
| `session_reset` | `#llm exit` 会话重置 | 记忆：清理会话态 |

> **不要**在新代码里写 `runtime.config.get("memory_enable")` —— 那个键已经不在 agent 配置里了。
> `tests/plugins/test_module_config_integration.py` 会一次装齐 9 个模块，
> 断言 108 个迁走的键都从模块值读到。

### 4.10.2 你自己的模块怎么接管同款能力

```python
class Module(BaseModule):
    # 排他槽（谁接管谁独占）：output / assembly / provider_invoker
    supersedes = ("output",)
    async def respond(self, ctx, *, should_stop=None): ...

    # 或策略型（只有一组配置键）：memory / knowledge / context / message_meta / mcp …
    # 接管 = 把 runtime.<kind>_options 换成你的配置视图，框架自然读你的值
    async def on_load(self):
        from app.agent.delivery.output import ConfigView
        runtime = self.ctx.services.runtime(self.bot_id)
        runtime.memory_options = ConfigView(runtime.config, self.config, MY_KEYS)
```

### 4.10.3 前端分区与升级

- `app/webui/dashboard/src/agent/sections.json` **由代码生成**，不要手改：
  ```bash
  python scripts/gen_sections.py          # 改完 config_partition.py 后重新生成
  python scripts/gen_sections.py --check  # CI 校验
  ```
- 本体界面只剩 5 页 / 43 键；模块能力在「LLM 扩展」分类下按模块渲染。
- 从 v2 升级务必跑两个迁移（都支持 `--dry-run`、自动备份 DB）：
  ```bash
  python scripts/migrate_v3_owner_to_roster.py   # 账号级 owner_id → 拥有者名册
  python scripts/migrate_v3_split_config.py      # agent 配置 108 键 → 9 个模块行
  ```

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
from app.plugin import BaseModule, module_hook, llm_hook, send_hook

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
