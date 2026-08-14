# 模块配置 Schema 指南

本文件说明业务插件如何通过 `config_schema.py` 声明 WebUI 配置表单，
以及 WebUI 支持的字段类型（含后端动态数据源 `list` / `dynamic`）。

---

## 〇、模块结构约定（所有模块统一）

```
module/modules/<name>/
├── __init__.py        # 一句话说明
├── module.py          # Module(BaseModule)：声明元数据 + handle 薄入口（只路由，不写业务）
├── config_schema.py   # SCHEMA 字典
├── service.py         # async handle(module, event)（唯一业务入口，顶部做启用检查）
├── xxx_api.py         # 可选：模块专属 API 封装类（网络请求，见下）
└── xxx_db.py          # 可选：模块私有持久化库（如 notice_recall_back/recall_db.py）
```

- **入口链统一**：`module.py → service.handle(module, event)`，禁止更深子包层（如 `src/`、`intro`）；
  业务较复杂时可拆 `service/` 目录，但入口仍须是 `service/__init__.py` 导出的 `handle`。
- **平级辅助文件**：除 `service/` 目录外，可用**平级模块级文件**承载独立职责，只被 `service.py` import、不直接暴露入口：
  - `xxx_api.py`：模块专属 API 封装类（网络请求，继承共享客户端，见下）；
  - `xxx_db.py`：模块私有持久化（JSON/SQLite）。
- **网络请求统一走共享客户端**：`app/infrastructure/curl_cffi.py::CurlCffiClient`
  （curl_cffi 浏览器指纹模拟 + Cookie 自动管理）。模式：API 封装类继承共享客户端，业务用 `async with` 调用：

  ```python
  # xxx_api.py —— API 封装类
  from app.infrastructure.curl_cffi import CurlCffiClient

  class BilibiliAPI(CurlCffiClient):
      async def get_video_info(self, vid, timeout=10, cookie=""): ...

  # service.py —— 调用
  async with BilibiliAPI() as api:
      info = await api.get_video_info(vid)
  ```

  禁止在模块里散落 `aiohttp` / `requests` 直连；纯逻辑（正则、格式化、去重）保留为模块级函数。
- **启用开关检查只一处**：在 `service.handle` 顶部，不要与 `module.py` 重复。
- **日志统一**：`logger = module_logger.add_info(f"#{module.bot_id}").add_info(module.name)`
  （用 `module.bot_id`，定时任务无 event 也能用）。
- **定时任务**：`SCHEDULES = {"HH:MM[:SS]": "方法名"}`，方法在 Module 类上，内部调
  `service.xxx(module, self.ctx.bot)`；时间需可配置时改用动态注册（见 §六）。
- **数据源**：`LIST_PROVIDERS` / `DYNAMIC_PROVIDERS`（见 §三）。
- **共享逻辑**：跨模块通用处理（如关键词匹配 `app/modules/keyword.py::match_keywords`、
  群模式判断）提取为共享库，模块各自调用，不复制。

### 子模块（父模块可拥有子模块）

父模块目录下的子目录（**含 `module.py`**）即为子模块，子模块名为 `parent.child`：

```
module/modules/<parent>/
├── module.py              # 父模块（1级，handle 只做路由）
├── child_a/module.py      # 子模块（2级）
└── child_b/module.py
```

- **调度**：子模块由父模块通过 `self.children["child_a"]` 调用，**不参与全局事件分发**（`registry.loaded()` 排除）；
- **配置命名空间**：子模块配置存于 `parent.child` 键（如 `<parent>.<child>.timeout`）；
- **生命周期级联**：父 on_load → 子 on_load；父 on_unload → 子 on_unload；热重载整体重建；
- 父模块的 `service/` 等普通目录（无 `module.py`）不会被误认为子模块。

### 自定义配置页（pages/）

模块可自带 `pages/index.html`，WebUI 中该模块卡片**只保留黑白名单（权限）部分**，配置表单区域改为 iframe 渲染自定义页：

```
module/modules/<name>/
├── module.py / config_schema.py / service.py
└── pages/
    └── index.html          # 自定义配置页（可附带 app.js / style.css）
```

- 页面经 `GET /api/module/<name>/page` 提供，自动注入 `window.PLUGIN_MODULE` 与 `window.PLUGIN_BOT_ID`（当前选中账号）；
- **读写配置**：页面用
  `fetch('/api/module/<name>/config' + '?bot_id=<id>')` GET 读取、POST 保存（body 为配置 JSON，与 schema 表单同一契约）；
- 页面也可用 `/api/module/<name>/list/*`、`/dynamic/*` 数据源端点；
- 参考：`module/modules/example/pages/index.html`（完整读写示例）。

---

## 〇A、节点架构（消息分发「一切皆节点」）

框架的消息处理基于 `MessageNode`（`app/nodes/base.py`），入站与出站都由同一种节点构成：

```
入站链（0级基础设施 → 1级业务 → LLM 兜底）：
  [ModuleRouterNode] → [ModulePermissionNode] → [ModuleInvokeNode] → [AgentNode]
     (订阅/bot归属)      (启停/单一服务/权限)     (调用业务模块 handle)   (LLM 兜底 order=130)

出站链（Bot 发送前）：
  [任意拦截节点…] → [SendNode]   ← 不调用 next_ 即吞掉发送；可改写 params
```

- **插入**：`NodeRegistry`（容器中）注册新节点到任意 order，无需改 dispatcher；
- **替换**：`node_registry.replace("permission", 自定义权限节点)`；
- 业务模块仍是 `handle(module, event)`（InvokeNode 的叶子），现有模块零改动。

### LLM 兜底与模块接管（模块 ↔ Agent 协作规则）

框架级 LLM Agent（`AgentNode`，order=130）在**模块链之后**兜底响应——模块有最终决定权：

| 方法 | 作用 | 适用场景 |
|---|---|---|
| `event.llm.stop()` | 仅跳过 LLM 兜底，模块链照常 | 模块已回复（如指令命中），但后续模块仍可处理 |
| `event.stop()` | 终止整条链：后续模块 + LLM 全部不执行（对齐 astrbot stop_event） | 模块声明「这个话题我全权处理」 |

- **默认不调用**：模块未接管 → LLM 按自身触发规则（私聊全触发 / 群聊 @或关键词）决定是否回复；
- **接管判定建议**：指令/关键词类模块（`#今日密码`、`#打卡`）命中即 `event.llm.stop()`；
  解析类模块（B站链接）解析回复后默认 stop，但**群聊中用户 @bot 时留给 LLM 对话**（`if not event.is_at_me(): event.llm.stop()`）；
- 模块可查询 `event.stopped` 判断事件是否已被前面模块终止。

---

## 一、基本结构

每个插件目录下的 `config_schema.py` 导出一个 `SCHEMA` 字典，
`module.py` 中的 `Module(BaseModule)` 将其赋值给 `config_schema`：

```python
# config_schema.py
SCHEMA = {
    "greeting": {"type": "string", "label": "问候语", "default": "你好"},
    "enable":   {"type": "boolean", "label": "启用", "default": True},
}
```

```python
# module.py
from modules.<name>.config_schema import SCHEMA

class Module(BaseModule):
    ...
    config_schema = SCHEMA
```

每个配置项的通用属性：

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | ✓ | 字段类型（见下表） |
| `label` | string | ✓ | 显示标签 |
| `description` | string | | 说明文字（支持换行） |
| `default` | any | | 默认值 |
| `placeholder` | string | | 输入占位提示 |
| `group` | string | | 归入某个分组（`type:"group"` 声明的分组 id） |
| `showIf` | object | | 条件显示 `{key: "其他字段", value: true}` |

---

## 二、字段类型一览

### 基础类型

| type | 渲染 | 存储值 |
|------|------|--------|
| `boolean` | 开关 | `true`/`false` |
| `string` | 单行输入 | 字符串 |
| `password` | 密码框 | 字符串 |
| `number` / `float` | 数字输入（支持 `min/max/step`） | 数字 |
| `select` | 下拉框（`options` 为 `{value: label}` 字典） | 字符串 |
| `textarea` | 多行输入（`rows`） | 字符串 |
| `time` | 时间选择器 | `"HH:MM"` |

### string_list —— 可增删的字符串列表

```python
"keywords": {
    "type": "string_list", "label": "触发关键词",
    "default": [], "placeholder": "如：AI、助手",
}
```

渲染为「一行一个 + 添加/删除按钮」，存储为 `string[]`。
（旧版 `type: "list"` 即此语义，已改名 `string_list`。）

### list —— 后端数据列表（勾选/排序/模式）

数据由**后端动态获取**（内置 `groups` / `friends`，或模块自注册数据源）。

```python
"target_groups": {
    "type": "list", "label": "目标群",
    "endpoint": "groups",          # 数据源端点
    "id_field": "group_id",        # 唯一标识字段
    "name_field": "group_name",    # 显示名称字段
    "meta_fields": ["member_count", "max_member_count"],  # 行内附加信息
    "sortable": True,              # 拖拽排序
    "checkboxes": True,            # 每行勾选
    "mode_select": True,           # 全开/部分/全关 下拉
    "default": {},
}
```

**存储格式**（保存时自动序列化）：

```json
{
  "target_groups": { "123456": { "enabled": true, "index": 0 }, "789012": { "enabled": false, "index": 1 } },
  "target_groups_mode": "partial"       // all | partial | none（mode_select 时才有）
}
```

**运行时读取**：

```python
cfg = module.config.get("target_groups", {})        # {id: {enabled, index}}
mode = module.config.get("target_groups_mode", "all")
if mode == "all" or (mode == "partial" and cfg.get(str(group_id), {}).get("enabled")):
    ...  # 该群参与
```

### dynamic —— 后端动态选项（下拉框 + 每选项独立表单）

```python
"provider_cfg": {
    "type": "dynamic", "label": "按提供商配置", "endpoint": "providers", "default": {},
}
```

前端先拉 `GET <endpoint>` 得到下拉选项，再为选中的选项拉取其字段定义并渲染子表单。

**存储格式**：

```json
{
  "provider_cfg": { "deepseek": { "api_key": "...", "model": "deepseek-chat" } },
  "provider_cfg_selected": "deepseek"
}
```

**运行时读取**：

```python
selected = module.config.get("provider_cfg_selected", "")
cfg = module.config.get("provider_cfg", {}).get(selected, {})
api_key = cfg.get("api_key", "")
```

### repeater —— 可增删的分组容器（嵌套任意字段）

```python
"group_configs": {
    "type": "repeater", "label": "群组配置",
    "template": {
        "group_id": {"type": "string", "label": "群号"},
        "welcome":  {"type": "string", "label": "欢迎语", "default": "欢迎"},
    },
    "default": [],
}
```

渲染为「可新增/删除的分组卡片」，每个卡片内按 `template` 渲染子字段。
存储为数组：`[{group_id: "...", welcome: "..."}, ...]`。

---

## 三、list / dynamic 后端数据源

### 内置数据源（无需声明即可用）

| endpoint | 数据 | 字段 |
|----------|------|------|
| `groups` | Bot 所在群列表 | `group_id`、`group_name`、`member_count`、`max_member_count` |
| `friends` | Bot 好友列表 | `user_id`、`nickname` |

### 模块自注册数据源

在 `Module` 类上声明 `LIST_PROVIDERS` / `DYNAMIC_PROVIDERS`，
并实现对应方法（方法在模块加载时自动绑定）：

```python
class Module(BaseModule):
    LIST_PROVIDERS = {"my_list": "list_my_list"}
    DYNAMIC_PROVIDERS = {"providers": "dynamic_providers"}

    async def list_my_list(self, field, bot) -> dict:
        """field: 字段 schema；bot: 当前账号（IBot）或 None"""
        # 返回 {"items": [ {<id_field>: ..., <name_field>: ...} ]}（也可用 groups/friends 别名）
        return {"items": [...]}

    async def dynamic_providers(self, field, bot, value=None) -> dict:
        if value is None:                      # 下拉框选项
            return {"options": [{"value": "a", "label": "A"}]}
        return {"fields": [{"key": "k1", "type": "string", "label": "字段1"}]}  # 某选项的子表单
```

> 也可以调用 `self.ctx.services.providers.register_list(name, endpoint, handler)` /
> `register_dynamic(...)` 在 `on_load()` 中手动注册。
> 同步或异步 handler 均可。

### 已存配置合并

`list` 接口返回数据时会自动把已存配置 `{<id>: {enabled, index}}` 合并进每项
（`enabled`/`index` 以已存为准），并按 `index` 排序，同时返回已存的 `mode`。
`dynamic` 的选项字段定义每次拉取都会刷新。

---

## 四、示例：完整字段声明

```python
SCHEMA = {
    "group_api": {"type": "group", "label": "API 设置", "collapsible": True},

    "api_key": {
        "type": "password", "label": "API密钥",
        "default": "", "placeholder": "sk-...", "group": "group_api",
    },
    "model": {
        "type": "string", "label": "模型名称", "default": "deepseek-chat",
        "group": "group_api",
    },
    "enable": {"type": "boolean", "label": "启用", "default": True},
    "trigger_time": {"type": "time", "label": "每日触发", "default": "00:00"},
    "mode": {
        "type": "select", "label": "模式", "default": "auto",
        "options": {"auto": "自动", "manual": "手动"},
    },
    "target_groups": {
        "type": "list", "label": "目标群", "endpoint": "groups",
        "id_field": "group_id", "name_field": "group_name",
        "sortable": True, "checkboxes": True, "mode_select": True, "default": {},
    },
    "provider_cfg": {
        "type": "dynamic", "label": "提供商配置", "endpoint": "providers", "default": {},
    },
    "per_group": {
        "type": "repeater", "label": "分组配置",
        "template": {"group_id": {"type": "string", "label": "群号"}}, "default": [],
    },
    "keywords": {"type": "string_list", "label": "关键词", "default": []},
    "advanced": {
        "type": "boolean", "label": "高级选项", "default": False,
        "showIf": {"key": "mode", "value": "manual"},  # 仅当模式=手动时显示
    },
}
```

---

## 五、说明

- 分组：`type: "group"` 的项作为分组头，普通配置项用 `group` 属性归组；
  未归组的项显示在「默认配置」分组。
- `showIf` 的值与当前字段值做严格比较（`true`/`false`/字符串）。
- 前端保存时按上述序列化格式 `POST /api/module/<name>/config`，后端经配置中心
  （SQLite）持久化，模块通过 `module.config.get(key, default)` 读取。

---

## 六、模块声明（Module 类上的额外声明）

### 定时任务 SCHEDULES —— 精确到点触发（替代旧 time_core 广播）

```python
class Module(BaseModule):
    SCHEDULES = {"05:00:00": "daily_push"}        # 每日 05:00:00
    SCHEDULES = {"0 5 * * *": "daily_push"}       # 等价 cron 写法
    SCHEDULES = {"0 9 * * 1-5": "weekday_job"}    # 每周一到五 09:00
    SCHEDULES = {"*/30 8-18 * * *": "frequent"}   # 8-18 点每 30 分钟

    async def daily_push(self):
        # self.ctx.bot 为当前账号（IBot）；self.config 为该账号的配置
        for gid in resolve_enabled_ids(self.config.get("group_list", {}),
                                       self.config.get("group_list_mode", "all")):
            await self.ctx.bot.send_group_msg(int(gid), "...")
```

- 支持 5 字段 cron（`分 时 日 月 周`，`*` / `*/n` / `a-b` / `a,b,c`）与每日 `HH:MM[:SS]` 简写；
- 仅对**真实 Bot 实例**注册；模块加载自动注册、卸载/重载自动注销；
- 参考实现：`module/modules/msg_df_password`（定时推密码）、`module/modules/time_sign_in`（每日打卡）。

### 定时任务动态注册（时间可配置时）

`SCHEDULES` 是编译期声明，时间固定。**定时时间需要由配置决定**（如 WebUI 的 `cron_time` 字段）时，
在模块 `on_load` 中经 `SchedulerService.register` 动态注册（key 含 `<module>:<bot_id>:` 前缀，卸载自动清理）：

```python
# module.py
class Module(BaseModule):
    async def on_load(self):
        from .service import register_schedule
        await register_schedule(self)

# service.py
async def register_schedule(module):
    """按配置的 cron_time 动态注册每日任务（on_load 调用）。"""
    scheduler = module.ctx.services.scheduler
    if scheduler is None or module.bot_id is None:
        return
    if not module.config.get("enable_cron", False):
        await scheduler.unload_module(module.module_name, module.bot_id)
        return
    time_str = module.config.get("cron_time", "08:00")
    key = f"{module.module_name}:{module.bot_id}:cron"
    await scheduler.register(key, time_str, lambda: daily_push(module, module.ctx.bot))
```

- 与 `SCHEDULES` 声明**二选一**：动态注册的模块不要同时声明 SCHEDULES（避免双注册）；
- 配置变更（WebUI 修改时间）后需「刷新模块」重新 on_load 生效；
- 参考实现：`msg_df_password`（cron_time）、`time_sign_in`（daily_signin_time）。

### LLM Provider 抽象（框架级 app/llm）

`app/llm/providers/` 提供「调哪个 LLM」与「怎么用」解耦：

```python
from app.llm.providers import get_provider

provider = get_provider(config)                  # 按 config["provider"] 选择
resp = await provider.chat(messages, model=model, temperature=0.7, max_tokens=150)
# resp: LLMResponse(text, reasoning, usage, raw)；resp.ok 判断是否成功
```

- 内置 `openai`（OpenAI/DeepSeek/中转兼容）：**429/5xx/网络错误指数退避重试**（`retry_attempts`），认证 4xx 立即失败，`api_key` 支持多 key 换行/逗号分隔轮换；
- 新增后端 = 在 `providers/` 加一个 `BaseProvider` 子类并注册到 `PROVIDERS`。
- 会话/对话分离：`#chat new [标题]` 开新对话、`#chat switch <id>` 切换、`#chat list` 列出当前会话的多个对话线程。
- **主动消息**（化用 astrbot_plugin_proactive_chat）：`proactive_*` 配置分组——私聊按随机间隔主动发言、
  群聊沉默后主动开口、免打扰时段、未回复上限；状态持久化到 `proactive_data.json`；
  模块自带 `pages/index.html`（LLM 基础 + 主动消息合并配置页，WebUI 中以自定义页渲染）。

### 数据源 LIST_PROVIDERS / DYNAMIC_PROVIDERS

见上文「list / dynamic 后端数据源」一节；模块在 `Module` 类上声明方法名，加载时自动绑定。

### 配置新特性（ModuleConfig）

- **深合并**：`module.config.raw_config` 会递归合并默认值（补缺 / None 回填 / 类型修复），保留默认值之外的多余键；
- **类型安全**：`module.config.set(key, value)` 按旧值类型自动转换（bool/int/float/str，容器原样），前端保存不再产生脏数据；
- **点号只读**：`module.config.api_key` 等价于 `module.config.get("api_key")`。

