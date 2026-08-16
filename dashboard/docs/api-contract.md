# QQBot Next WebUI 前后端契约文档

> 用途：为使用 Vue3 重写前端提供精确的 API 文档。
> 依据源码：`app/webui/**`、`app/services/bot_service.py`、`app/infrastructure/config/config_service.py`、`app/infrastructure/onebot/gateway.py`、`app/llm/{scheduler,proactive,config_schema}.py`、`app/services/log_service.py`。
> 字段名与代码保持一致；所有旧版值类型/含义均按当前实现记录。

---

## 0. 总体约定

- **基础路径**：所有 HTTP API 挂在 `/api` 前缀下。首页 `GET /` 渲染 `index.html`（Jinja2 服务端渲染，内联数据），静态资源在 `/static`。
- **通用响应形态**：业务端点大多返回 **对象**（非统一 `{status,message,data}` 外壳）。仅少数端点用 `{status,message,...}`，详见逐端点标注。
- **错误响应**（`_err` 统一结构，HTTP 状态码 400/404/500/502/401）：
  ```json
  { "status": "error", "message": "错误描述" }
  ```
- **成功响应**（`_ok` 统一结构）：
  ```json
  { "status": "success", "message": "成功描述", ...额外字段 }
  ```
- **解码说明**：若响应体含 `status:"success"` 即业务成功；`status:"error"` 即失败（`message` 为原因）。

---

## 1. 认证方式

集成于 `app/webui/app.py` 的 `_install_auth_middleware`（HTTP）与 `app/webui/ws.py`。

- **令牌来源**：`WEBUI_TOKEN`（配置 `core.settings.webui_token`）。**为空则不启用鉴权**；非空时所有 `/api/*` 与 `/ws/logs` 强制校验。
- **令牌下发**：`GET /` 不鉴权，服务端将令牌经 Jinja 注入 `window.WEBUI_TOKEN = {{ webui_token | tojson }}`，由前端持有。
- **HTTP 携带方式**：两种皆可，二选一：
  - `Authorization: Bearer <token>`
  - 查询参数 `?token=<token>`（适用于无法改 header 的场景）
- **鉴权失败**：HTTP 中间件返回 `401 {"status":"error","message":"未授权"}`。鉴权中间件只拦截以 `/api` 开头或恰为 `/ws/logs` 的路径；`/`,`/static/*` 不鉴权。
- **WebSocket 携带方式**：HTTP 中间件对 WS scope 不生效，故在 `ws.py` 端点内**自行校验**，同样两种：
  - 连接头 `Authorization: Bearer <token>`
  - 查询参数 `?token=<token>`
  - 校验失败：`websocket.close(code=4401, reason="未授权")`。
- 前端统一封装（旧版 `main.js`）：`apiFetch()` 自动附 `Authorization: Bearer`；`apiWsUrl(path)` 在无可用 header 场景把 token 拼进 url 查询串（`path + (?或&) + token=...`）。

---

## 2. HTTP 端点总表

> 所有路径均省略 `/api` 前缀。`bot_id` 为**查询参数**，取值 `int | null`（可传 `null`/`'null'`/`'None'`/空串，`parse_bot_id` 统一归 `None`）。GET 返回对象直接作为响应体（不再包 `{status,message,data}`），逐条已注明。

### 2.1 bots（文件 `api/bots.py`，tags: bots）

#### GET /bots
- 查询参数：无
- 响应：`{ "bots": BotInfo[] }`（**直接对象**）
- `BotInfo`（来自 `get_bots_info()`）：
  - `index`: `int` 账号索引
  - `bot_id`: `int|null` 已连接的真实 bot id
  - `owner_id`: `int|null`
  - `status`: `string`，枚举 `connected` / `disconnected` / `connecting` / `reconnecting` / `error`
  - `ws_url`: `string` 仅基础地址（单独拆出、不含 access_token）
  - `login_info`: `object|null`（如含 `user_id`、`nickname`）
  - `reconnect_attempts`: `int`
  - `last_error`: `string|null`
  - `auto_connect`: `bool`

#### POST /bots/{index}/connect
- 路径参数：`index: int`
- 请求体：无
- 行为：若内存中无该索引连接则新建（显式 index），否则 `readd_bot`；随后 `connect(index)`。
- 成功（200）：`{"status":"success","message":"Bot <id> connected"}`
- 失败（404）：`{"status":"error","message":"Bot at index N not in config"}`；失败（500）：连接失败。

#### POST /bots/{index}/disconnect
- 成功（200）：`{"status":"success","message":"Bot at index N disconnected"}`

#### POST /bots/{index}/reconnect
- 成功（200）：`{"status":"success","message":"Bot <id> reconnected"}`；失败（500）：`"…reconnection failed"`

#### GET /bots/config
- 响应：`{ "bots": BotConfigItem[] }`（**直接对象**）
- `BotConfigItem`（来自 `get_bots_public()`）：
  - `ws_url`: `string` 基础地址
  - `access_token`: `string` 独立回显的真实 token（**注意：此接口不回显打码，真实返回**）
  - `owner_id`: `int|null`
  - `auto_connect`: `bool`

#### POST /bots/config/save
- 请求体 JSON：`{ "bots": BotConfigSaveItem[] }`
  - `BotConfigSaveItem`：`{ ws_url: string, access_token: string, owner_id: int|null, auto_connect: bool }`
  - `access_token` 语义（见 `config_service.save_bots`）：缺失或等于打码哨兵 → 保留旧 token；显式值（含空串）→ 直接采用（空串=清除）。
- 成功（200）：`{"status":"success","message":"配置已保存"}`

#### POST /bots/config/add
- 请求体：无
- 行为：追加空配置 `{ws_url:"", owner_id:None, auto_connect:False}`，返回新索引。
- 成功（200）：`{"status":"success","message":"已添加新账号配置","index": <int>}`

#### POST /bots/config/delete/{index}
- 路径参数：`index: int`
- 行为：`gateway.del_bot(index)` + `delete_bot(index)`（删配置并整体保存）。
- 成功（200）：`{"status":"success","message":"已删除账号配置"}`；失败（404）：`"索引 N 不存在"`

#### GET /bots/groups
- 响应：`{ "bots_groups": { "<index>": BotGroups } }`（**直接对象**）
- `BotGroups`：`{ "bot_id": int|null, "index": int, "groups": int[] , "groups_info": object[] }`（`groups` 为群号数组，`groups_info[i]` 含 `group_name` 等）

#### GET /bots/{index}
- 路径参数：`index: int`
- 响应（成功，200）：**直接返回 BotInfo 对象**（字段同 `/bots`，但**无 `reconnect_attempts`/`last_error`/`auto_connect`**，只有 `bot_id/owner_id/status/login_info/ws_url`，见 `get_bot_info_by_index`）
- 失败（404）：`{"status":"error","message":"Bot at index N not found"}`

### 2.2 modules（文件 `api/modules.py`，tags: modules）

> 含「虚拟 Agent 模块」：模块名 `agent`（`VIRTUAL_AGENT_MODULE="agent"`）不读模块目录，而是经由 `_AgentProxy`/`_AgentAuthority` 代理到框架 Agent 运行时。所有 `{module_name}` 为 `agent` 的端点都走此代理。

#### GET /modules
- 查询参数：`bot_id?: int|null`
- 响应：`{ "<module_name>": ModuleData }`（**直接对象**，顶层为模块名→数据的映射）。详见第 4 节。
- 无论有无模块，`agent` 键恒存在（框架级注入）。

#### GET /modules/{module_name}
- 查询参数：`bot_id?: int|null`
- 响应：`{ "<module_name>": ModuleData }`（**直接对象**，单键）
- 失败（404）：`{"status":"error","message":"Module X not found"}`

#### POST /module/{module_name}/toggle
- 查询参数：`bot_id?: int|null`
- 请求体：**FormData** 字段 `enabled: bool`（`Form(...)`）
- 成功（200）：`{"status":"success","message":"模块 <name> (Bot <id>) 已启用/已禁用"}`
- 成功后通过 WS 广播 `module_authority_updated`（见 §3）。
- 失败（404）：`{"status":"error","message":"模块 X (Bot <id>) 不存在"}`

#### POST /module/{module_name}/permission
- 查询参数：`bot_id?: int|null`（**必须存在**，否则 404 `"模块 X 无 Bot ID 实例"`）
- 请求体：**FormData** 字段：
  - `group_mode`: `string`（`whitelist` | `blacklist`）
  - `group_list`: `string`（每行一个群号，以 `\n` 拆分）
  - `user_mode`: `string`（`whitelist` | `blacklist`）
  - `user_list`: `string`（每行一个 QQ 号）
- 成功（200）：`{"status":"success","message":"模块 <name> (Bot <id>) 权限已更新"}`
- 成功后广播 `module_authority_updated`。

#### GET /module/{module_name}/config
- 查询参数：`bot_id?: int|null`
- 响应：`{ "ok": true, "module": module_name, "bot_id": bot_id, "config": <脱敏后的 config> }`（**直接对象**）
- 说明：给自定义配置页读取使用；password 字段已打码为哨兵。
- 失败（404）：`{"status":"error","message":"模块 X 不存在"}`

#### POST /module/{module_name}/config
- 查询参数：`bot_id?: int|null`（**必须存在**，否则 404）
- 请求体 JSON：扁平键值对象 `{ "<config_key>": <value>, ... }`（**非 `{config:...}` 包裹**，即直接把各配置字段作为顶层键）。password 字段值为打码哨兵时保留旧值。
- 成功（200）：`{"status":"success","message":"模块 <name> (Bot <id>) 配置已更新"}`
- 成功后广播 `module_config_updated`（config 已脱敏打码）。

#### POST /modules/reload
- 查询参数：`bot_id?: int|null`
- 请求体：无
- 行为：`registry.reload_all(bot_id)`，随后广播 `modules_reloaded`。
- 成功（200）：`{"status":"success","message":"模块已重新加载"}`

#### GET /module/{module_name}/page
- 返回 **HTMLResponse**（自定义配置页）。
- 行为：从模块目录 `pages/index.html` 读取；若无自定义页 → 404 `{"status":"error","message":"模块 X 无自定义页面"}`。
- **注入脚本**：在 `<head>` 后（无 `<head>` 则前置）注入：
  ```html
  <script>
    window.PLUGIN_MODULE = "<module_name>";
    window.PLUGIN_BOT_ID = <int> | null;
    window.WEBUI_TOKEN = "<token>";
  </script>
  ```
- 前端加载：`<iframe src="/api/module/{module_name}/page?bot_id=<id>">`（见 §6）。

#### GET /module/{module_name}/list/{endpoint}
- 路径参数：`endpoint`；查询参数：`bot_id?: int|null`
- 前置：查找该模块 `config_schema` 中 `type=="list"` 且 `endpoint` 匹配的字段；找不到 → 404。
- 请求方：`ProviderRegistry.call(module.module_name, endpoint, "list", module, bot, field)`。
- 响应：`{ "ok": true, "items": ListItem[], "mode": string }`（**直接对象**）
  - `mode`: 读 `module.config.get(key+"_mode","all")`，枚举 `all`/`partial`/`none`。
  - `ListItem`：`{ "id": string, "name": string, "meta": any[], "enabled": bool, "index": int }`
    - 数据源返回的 `items`/`groups`/`friends` 逐项归一化；`id` 取 `item[id_field]`，`name` 取 `item[name_field]`，`meta` 取各 `meta_fields`，`enabled`/`index` 先合并已存配置再回填，最后按 `index` 升序排序。
- 上游失败（502）：`{"status":"error","message":"数据源请求失败: …"}`

#### GET /module/{module_name}/dynamic/{endpoint}
- 查询参数：`bot_id?: int|null`；`endpoint: string`
- 响应：`{ "ok": true, "options": Option[] }`（**直接对象**）
  - `Option`：`{ "value": string, "label": string }`
- 上游失败（502）同上。无动态字段 → 404。

#### GET /module/{module_name}/dynamic/{endpoint}/{value}
- 查询参数：`bot_id?: int|null`；`value: string`（URL 编码）
- 响应：`{ "ok": true, "fields": FieldSchema[] }`（**直接对象**）
  - `FieldSchema`：同 `renderSubField` 支持的子字段 schema（`key/label/type/default/min/max/step/placeholder/rows/options/hint`）。`type in {boolean, select, textarea, time, number, password, ...}`（见 §6）。
- 上游失败（502）同上。

### 2.3 agent（文件 `api/agent.py`，tags: agent）

> 所有端点依赖 `bot_id`（查询参数）。若 `bot_id is None` 或无运行时 → 404 `{"status":"error","message":"Bot None 无 Agent 运行时"}`。

#### GET /agent/config
- 查询参数：`bot_id?: int|null`
- 响应（**直接对象**）：
  ```json
  {
    "ok": true,
    "bot_id": <int|None>,
    "enabled": <bool>,
    "permission": {
      "group_mode": "<whitelist|blacklist>",
      "group_list": ["..."],
      "user_mode": "<whitelist|blacklist>",
      "user_list": ["..."]
    },
    "config": { "<key>": <value>, ... },   // password 字段已打码
    "schema": { "groups": {...}, "items": {...} }  // 见 §3 / §4
  }
  ```
- `schema` 由 `app/llm/config_schema.SCHEMA` 经 `_split_schema` 拆为 `{groups, items}`；`groups` 为 `type=="group"` 的字段，`items` 为其余字段。

#### POST /agent/config
- 查询参数：`bot_id?: int|null`
- 请求体 JSON：
  ```json
  {
    "config": { "<key>": <value>, ... },
    "permission": { "group_mode": ..., "group_list": [], "user_mode": ..., "user_list": [] },
    "enabled": <bool|null>
  }
  ```
  - `config` 内 password=哨兵 → 保留旧值；`permission` 缺子字段时用默认 `whitelist` 白名单 / `[]`；`enabled` 为 `null`/缺省时不改启停。
- 成功（200）：`{"status":"success","message":"Bot <id> Agent 配置已更新"}`

#### GET /agent/tasks
- 查询参数：`bot_id?: int|null`
- 响应：`{ "ok": true, "tasks": TaskItem[] }`（**直接对象**）
- `TaskItem`（来自 `scheduler.status()`，按 next_trigger_time 升序）：
  - `task_id`: `string`
  - `session_id`: `string`（`group_<id>` 或 `private_<id>`）
  - `target`: `string`
  - `type`: `"group" | "private"`
  - `repeat`: `string`
  - `trigger_expr`: `string`
  - `content`: `string`
  - `next_trigger_time`: `int`（Unix 秒）
  - `fired_count`: `int`
  - `active`: `bool`
  - `created_at`: `int`（Unix 秒）

#### POST /agent/tasks
- 查询参数：`bot_id?: int|null`
- 请求体 JSON：
  ```json
  {
    "trigger": "<时间表达式>",
    "content": "<内容>",
    "is_group": <bool>,
    "target": "<群号或QQ号>",
    "repeat": "<可选重复规则>"   // 空串则不含
  }
  ```
- 行为：`session_id = "group_<target>" if is_group else "private_<target>"`；`schedule_enable` 为假 → 400；trigger/content/target 缺省 → 400；表达式无法解析 → 400。
- 成功（200）：`{ "ok": true, "task_id": string, "next_trigger_time": int(Unix秒), "repeat": string }`（**直接对象**）
- 失败（400）：`{"status":"error","message":"缺少 trigger 或 content"}` 等。

#### POST /agent/tasks/{task_id}/trigger
- 路径参数：`task_id`；查询参数：`bot_id?: int|null`
- 行为：`scheduler.trigger_now(task_id)`。
- 成功（200）：`{"status":"success","message":"已立即触发任务 <id>"}`；失败（400）：`"任务 <id> 不存在或已结束"`

#### POST /agent/tasks/{task_id}/cancel
- 成功（200）：`{"status":"success","message":"已取消任务 <id>"}`；失败（400）：`"任务 <id> 不存在"`

#### GET /agent/proactive/status
- 查询参数：`bot_id?: int|null`
- 响应：`{ "ok": true, "sessions": ProactiveSession[] }`（**直接对象**）
- `ProactiveSession`（来自 `proactive.status()`）：
  - `session_id`: `string`
  - `target`: `string`
  - `type`: `"group" | "private"`
  - `enabled`: `bool`
  - `unanswered`: `int`
  - `last_user_time`: `int|null`
  - `next_trigger_time`: `int|null`（Unix 秒）
  - `timer`: `"" | "private" | "silence"`

#### POST /agent/proactive/trigger
- 查询参数：`bot_id?: int|null`
- 请求体 JSON：`{ "session_id": "<必须>" }`
- 行为：`proactive.manual_trigger(session_id)`。
- 成功（200）：`{"status":"success","message":"已触发 <session_id> 主动发言"}`；失败（400）：`"会话 <session_id> 未启用或不在主动列表"` / 缺 `session_id`。

### 2.4 logs（文件 `api/logs.py`，tags: logs）

#### GET /logs
- 查询参数：`mode`（可选）：`simple`（默认，读取 `user.log`）或 `raw`（读取 `debug.log`）；缺省时按 WebUI 配置 `show_raw_logs` 决定
- 响应：**直接数组（JSON 数组，不是对象）**：`LogItem[]`
  - 遵循 `webui_config.logs.visible_levels`（缺省 `["info","warning","error"]`）与 `max_lines`（缺省 50）。
- `LogItem`：`{ "timestamp": string, "level": string, "message": string }`

### 2.5 webui（文件 `api/webui_cfg.py`，tags: webui）

#### GET /webui/config
- 响应：**直接返回 webui_config 对象**（见 §8）。无 `{status,message}` 包裹。

#### POST /webui/config
- 请求体 JSON：`{ "logs": {…}?, "module_preferences": {…}? }`
- 行为：合并更新——仅 `logs`（`current.logs.update(data["logs"])`）与 `module_preferences`（整值替换）。
- 成功（200）：`{"status":"success","message":"配置已保存"}`；保存成功后经 `config_service.save_webui_config` 触发 `webui` scope 广播（见 §3）。

#### POST /webui/config/logs
- 请求体 JSON：`{ "show_raw_logs": bool?, "visible_levels": string[]?, "max_lines": int?, "console_height": int? }`（只更新在请求体中的键；`show_raw_logs` 默认 `false` = 简洁日志）
- 成功（200）：`{"status":"success","message":"日志配置已保存"}`

#### GET /webui/module-preferences
- 响应：**直接返回 `module_preferences` 对象**（默认 `{}`）

#### POST /webui/module-preferences
- 请求体 JSON：`{ "module_preferences": {…} }`
- 成功（200）：`{"status":"success","message":"模块偏好已保存"}`

#### GET /webui/single-service
- 响应：`{ "single_service": { "<module>": bool } }`（**直接对象**）

#### POST /webui/single-service
- 请求体 JSON：`{ "single_service": { "<module>": bool } }`
- 成功：`{"status":"success","message":"单一服务配置已保存"}`

#### GET /webui/multi-group
- 响应：`{ "multi_group": { "show_all": bool, "groups": { "<gid>": { "service_bot_index": int } } } }`（**直接对象**；缺省 `{"show_all":False,"groups":{}}`）

#### POST /webui/multi-group
- 请求体 JSON：`{ "multi_group": { "show_all": bool, "groups": {...} } }`
- 成功：`{"status":"success","message":"多群管理配置已保存"}`

---

## 3. WebSocket `/ws/logs`

文件：`app/webui/ws.py` + `app/webui/app.py` 的广播源。

### 连接

```
ws(s)://<host>/ws/logs[?token=<WEBUI_TOKEN>][&mode=simple|raw]
```
- 鉴权：非空 token 时传 `?token=` 或 `Authorization: Bearer`（HTTP 中间件不覆盖 WS，端点内自校验）；失败 `close(4401)`。
- `mode`：`simple`（默认，读取 `user.log`）或 `raw`（读取 `debug.log`）；缺省时按 WebUI 配置 `show_raw_logs` 决定。
- 后端逻辑：接受连接后加入 `ConnectionManager`，随后**每秒**循环：按 `mode` 读 `get_recent_logs(max_lines, visible_levels, source=...)` 并经 `send_text(json.dumps(logs))` 推送，直到断开（`manager.disconnect`）。
- connection manager 支持**广播**（多连接 push）。

### 收到的消息类型

WS 通道会混合推送两类内容：

**(a) 纯日志快照**：对象顶层为 **JSON 数组**（`LogItem[]`），即 `get_recent_logs` 的返回。前端以此为准渲染控制台。
- `LogItem`：`{ "timestamp": string, "level": string, "message": string }`
  - `level` 枚举：`debug` / `info` / `warning` / `error`（`get_recent_logs` 按 `" - "` 拆分，`parts[1].strip().lower()`；`mode=simple` 读 `user.log`，`mode=raw` 读 `debug.log`）。
  - 渲染方式（旧前端 `_buildLogItem`）：`<span class="log-time">timestamp</span>` + `<span class="log-level {level}">LEVEL.toUpperCase()</span>` + `<span class="log-message">message</span>`；用 `textContent` 防 XSS。
  - 增量 diff：按 `timestamp|level|message` 键从尾部对齐新旧数组，只追加新增行（`updateLogsDisplay`）。

**(b) 事件消息**：顶层为 `{ "type": <string>, ... }` 对象。全部可推送到此通道的 type 与 payload：

| type | payload 字段 |
|---|---|
| `log_item` | **注意：本实现中不存在该 type**。当前实现仅推数组快照与下列事件；旧版本代码不再生成 `log_item`。 |
| `bot_status_updated` | `{ "bot": { "index": int, "bot_id": int\|null, "status": string, "last_error": string\|null, "login_info": object\|null } }`。来源：`BotLifecycleEvent` 监听（`app.py:_install_bot_lifecycle_listener`）。`status` 枚举同 §2.1。 |
| `modules_reloaded` | `{ "bot_id": int\|null }`。来源：`@ /modules/reload`、Bot 生命周期 `state=="connected"` 且 `bot_id` 存在时。前端收到即静默 `refreshAllModulesData(true)`。 |
| `webui_config_updated` | `{ "config": <整个 webui_config 对象> }`。来源：ConfigService `webui` scope（`save_webui_config`）。 |
| `single_service_updated` | `{ "single_service": { "<module>": bool } }`。随 `webui` scope 一并追加广播。 |
| `multi_group_updated` | `{ "multi_group": { "show_all": bool, "groups": {...} } }`。随 `webui` scope 一并追加广播。 |
| `module_config_updated` | `{ "module": string, "bot_id": int\|null, "config": <脱敏后配置> }`。来源：`@ POST /module/{name}/config`；亦有 ConfigService `module_config` scope。 |
| `module_authority_updated` | `{ "module": string, "bot_id": int\|null, "enabled": bool, "permission": { "group_mode", "group_list", "user_mode", "user_list" } }`。来源：`@ toggle`、`@ permission`、ConfigService `authority` scope。 |
| `permission_updated` | 旧版遗留 type；当前实现 `app.py` 的 `_install_config_listener` 中已**不再生成**它（旧前端 `handleConfigUpdate` 仍保留分支，作为兼容）。 |

> 旧前端处理入口 `handleConfigUpdate(data)` 依据 `data.type` 分发：`webui_config_updated`/`module_config_updated`/`module_authority_updated`（过滤 `data.bot_id === currentBotId || null`）/`permission_updated`/`modules_reloaded`/`single_service_updated`/`multi_group_updated`/`bot_status_updated`。

---

## 4. 模块数据结构（/api/modules）

### 4.1 每个模块（`ModuleData`，`get_modules_data` / `_agent_module_data`）

顶层 `{ "<module_name>": ModuleData }`。`ModuleData` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 模块显示名（如 `LLM服务`）；代码 `mod.name` |
| `name_sign` | string | 简称/签名（如 `Agent`）；`mod.sign` |
| `description` | string | 描述 |
| `enabled` | bool | `mod.authority.enabled` |
| `permission` | string | `mod.permission`（如 `member`/`admin`/`group_admin`；Agent 虚模块读配置 `permission`） |
| `bot_id` | int\|null | 实例的 bot id |
| `category` | string | 缺省 `"未分类"`；Agent= `"LLM"` |
| `tags` | string[] | 缺省 `[]`；Agent= `["LLM","Agent"]` |
| `order` | int | 缺省 `100`；Agent= `0` |
| `hidden` | bool | 缺省 `false`；Agent= `false` |
| `pinned` | bool | 缺省 `false`；Agent= `true` |
| `permission_config` | object | `{ "group_mode", "group_list", "user_mode", "user_list" }` |
| `config` | object | 脱敏后配置（password→哨兵），`_mask_password_config(raw_config, schema)` |
| `config_schema` | object | `{ "groups": {...}, "items": {...} }`（`_split_schema`，见 §4.2/§6） |
| `has_page` | bool | 是否有自定义配置页（`registry.module_has_page`） |

### 4.2 config_schema 的两种形态

后端把模块原始 schema 经 `_split_schema` 统一转成 `{groups, items}`，但**模块原始 schema 有两种书写方式**，Jinja 渲染兼容两者：

1. **新式**（`config_schema` 顶层含 `items`/`groups`）：直接 `schema.get('items')` 存在即走分组渲染。
   - `items` = 非 `group` 类型的字段；`groups` = `type=="group"` 的字段。
   - 每个 `items` 字段可带 `group: "<gid>"` 归属到某分组；无 `group` 的落入「默认配置」。
   - `group` 字段本身：`{ "type": "group", "label": string, "collapsible": bool(default true) }`。
2. **旧版扁平形态**（`config_schema` 顶层无 `items`，直接是键→定义映射）：
   - 若存在 `type=="group"` 的键 → 前端按 `group` 关联拆分分组渲染（index.html 兼容分支）。
   - 若无 `group` → 逐字段平铺渲染（`config-item-flat`）。
   - 若 raw_items 根本不是 mapping（值非 dict）→ 完全退化为按 `config` 值类型渲染（boolean→switch，number→number，其余→text）。

> 对 Vue3 重写：**只面向 `config_schema.groups` + `config_schema.items` 双结构**即可；但需兼容旧前端从 server-rendered HTML 的两种退化形态。建议新版直接消费 `config_schema` 对象，不再依赖服务端 HTML。

### 4.3 字段级常用 schema 约定（index.html + widgets.js）

每个配置字段 schema 支持以下键（除 `type` 外均可选）：

- `type`: `boolean|bool|integer|float|number|password|select|textarea|time|string_list|list|dynamic|repeater|text|string|group`
- `label`: 显示标签（缺省用 key）
- `description`: 描述，支持 `\n`（服务端渲染 `<br>`）
- `placeholder`: 占位符
- `default`: 缺省值（渲染取值 `mod.config.get(key, schema.default)`）
- `min` / `max`: 数值上下限（`is not none` 判断）
- `step`: number 的步进（float/number 表单用 `step="0.01"`；widget subfield 用 `schema.step`）
- `rows`: textarea 行数
- `options`: select 选项。**两种形态都要兼容**：
  - 旧版 Jinja：`schema.options.items()` 期望 **dict** `{value: label}`；
  - widgets.js `renderSubField`：`iterable` of `{value,label}` **或** 字符串数组。
- `showIf`: `{ "key": <依赖字段>, "value": <目标值> }`（元素上 `data-showif-key`/`data-showif-value`；当前值 == 目标值时显示，否则 `display:none`）
- `group`: 归属分组 id（字符串）
- `collapsible`: bool（group 表头是否可折叠，缺省 `true`）
- `endpoint`: list / dynamic 字段的数据源端点名
- `id_field` / `name_field`: list 字段；缺省 `"id"` / `"name"`
- `meta_fields`: list 字段的附加展示字段名数组（`["..."]`）
- `sortable`: bool（list 可拖拽排序）
- `checkboxes`: bool（list 每项复选框）
- `mode_select`: bool（list 显示 all/partial/none 模式下拉）
- `template`: repeater 字段的子字段 schema 对象 `{ "<subkey>": <schema> }`

---

## 5. 配置表单字段类型全集：渲染 / 取值 / 保存规则

来源：index.html `render_config_input` + main.js `saveConfig` + widgets.js。

| type | 渲染控件 | 取值 | 保存到 config 的形式 |
|---|---|---|---|
| `boolean` / `bool` | `<input type="checkbox">` | `el.checked` | `bool` |
| `integer` | `<input type="number">`（带 min/max） | `parseInt(el.value) || 0` | `int` |
| `float` / `number` | `<input type="number" step="0.01">` | `parseFloat(el.value) || 0` | `float` |
| `password` | `<input type="password">`（placeholder=哨兵显示 `••••••••`） | `el.value`（若未改=哨兵，发给后端时被识别并保留旧值） | string |
| `select` | `<select>`（options: dict value→label） | `el.value` | string |
| `textarea` | `<textarea class="form-control auto-resize">`（rows） | `el.value` | string（含换行） |
| `time` | `<input type="time">`（缺省 `00:00`） | `el.value` | string `HH:MM` |
| `string_list` | widgets.js `initStringListWidget`：动态行 input，底部「+ 添加」/「✕ 删除」 | `items.map(s=>s.trim()).filter(Boolean)` | `string[]` |
| `list` | widgets.js `initListWidget`：后端数据列表（可拖拽/复选框/mode_select） | 见下 | `object`（id→`{enabled,index}`）+ 可选 `{key}_mode` |
| `dynamic` | widgets.js `initDynamicWidget`：选项选择 + 动态子字段 | 见下 | `object`（selected→字段值映射）+ `{key}_selected` |
| `repeater` | widgets.js `initRepeaterWidget`：可增删的分组卡片，每卡片按 `template` 渲染子字段 | `items`数组 | `Array` of objects |
| `text` / `string`（默认分支） | `<input type="text">` | `el.value` | string |

### 5.1 list 字段（widget）

- **加载端点**：`GET /api/module/{mod}/list/{endpoint}?bot_id=<currentBotId>`（须先连接 Bot，无 bot_id 只提示不请求）。
- 响应 `{ok, items:[ListItem], mode}` → 渲染每行：序号、`name`、`meta`（拼接 `meta_fields[i]: value`），按 `sortable` 可拖拽、按 `checkboxes` 有复选框、按 `mode_select` 有顶部模式下拉（all=全部 enabled、partial=仅勾选、none=全部 disabled）。
- 用户改 mode：`all`→把每项 enabled=true；`none`→enabled=false；改任何 checkbox/拖拽时若当前是 all/none 则自动切到 `partial`。拖拽后重排 `index`（0 起）。
- **保存取值**（widget.get）：`out[key] = { "<id>": {"enabled": bool, "index": int}, ... }`；若 `mode_select` 另加 `out[key + "_mode"] = mode`。随整体 config 一起 POST 到 `/module/{name}/config`。
- 后端把 `module.config.get(key)` 作为已存字典来合并回填 enabled/index（`saved[iid]`），并排序。

### 5.2 dynamic 字段（widget）

- **加载/刷新选项**：`GET /api/module/{mod}/dynamic/{endpoint}?bot_id=<id>` → `{ok, options:[{value,label}]}`。
- **选择某 option 后加载子字段**：`GET /api/module/{mod}/dynamic/{endpoint}/{value}?bot_id=<id>` → `{ok, fields:[FieldSchema]}`。每个 `FieldSchema` 经 `renderSubField` 渲染（支持 boolean/select/textarea/time/number/password/string 等，见 §5 子字段渲染规则）。
- 存值：`store[selected]` 累积子字段值；`set(): saved[selected]=store[selected]`。
- **保存取值**（widget.get）：`out[key] = saved`（selected→值映射 object）；`out[key + "_selected"] = selected`。随 config POST。

### 5.3 repeater 字段（widget）

- 模板：`data-template` 即 `schema.template`（子字段 schema 对象）；每项为独立卡片「分组 N」+ 删除按钮 +「+ 新增分组」。
- 取值：卡片内子字段输入时写回 `itemData[subKey]`（监听 `input`/`change`）。
- **保存取值**：`out[key] = items`（对象数组）。Array 类型直接保存。

### 5.4 子字段渲染（renderSubField）统一规则

widgets.js `renderSubField(schema, initial)` 复用给 dynamic / repeater，控件与取值：
- `boolean`: switch checkbox，`getValue=cb.checked`，`setValue=!!v`
- `select`: `<select class="form-control mode-select">`，options 支持 `{value,label}` 对象数组或字符串数组；`getValue=sel.value`
- `textarea`: rows=`schema.rows||3`，`auto-resize`；`getValue=ta.value`
- `time`: `<input type="time">`，缺省 `00:00`
- `number`: `<input type="number">` + min/max/step；`getValue=parseFloat`，NaN→null，`setValue`
- 其他(含 password/string/text): `<input type="text|password">` + placeholder。

### 5.5 读取时回填（refreshAllModulesData）

前端把服务端返回的 `moduleData.config` 逐键回填：
- 存在 widget（string_list/list/dynamic/repeater）→ `w.set(value)`（list 只合并 enabled/index；dynamic 只覆盖 `saved`；string_list/repeater 整体替换）。
- 普通控件 → `updateInputValue(input, value)`（checkbox→Boolean、select→String、textarea list 类型→join('\n')、其余 String），加 1s 高亮类 `config-updated`。
- 回填后 `markAllModulesClean()`（清除未保存态）。

---

## 6. 插件自定义配置页 iframe 与 resizePluginPage

- 当模块 `has_page` 为真，Jinja 渲染 `<iframe id="plugin-page-{name}" data-page="/api/module/{name}/page">`（src 在 `initPluginPages` 里动态补 `?bot_id=`）。
- 页面对应 `@ GET /module/{name}/page`，注入 `window.PLUGIN_MODULE` / `window.PLUGIN_BOT_ID` / `window.WEBUI_TOKEN`，页面 JS 用它们拼接配置 API（如 `GET/POST /module/{name}/config?bot_id=`）并携带鉴权。
- **切换到不同账号**时 `switchBot()` 调 `initPluginPages()` 重设每个 iframe 的 `src`（`base + '?bot_id=' + botId` 或 `base`）。
- **高度自适应** `resizePluginPage(el)`：
  - 取 `el.contentDocument` 的 `documentElement.scrollHeight` 与 `body.scrollHeight` 的较大者，若 `>40` 设 `el.style.height=px`；跨域 try/catch 忽略。
  - iframe `load` 时 resize，并用 `ResizeObserver` 观察 `doc.body` 动态跟随（记录在 `el.__pluginPageInit` / `el.__pluginPageRO`）。
- 显示隐藏的 iframe 时（切到某配置卡片）会先 `resizePluginPage`，再延时 100ms 再测一次。

---

## 7. agent 端点数据细化

（端点与 §2.3 一致，这里补充 config 结构与字段。）

### 7.1 /agent/config 的 config/schema

- `config` 即框架 `AgentConfig.raw_config` 过滤 password 后（打码）输出。`schema.config`（未拆分前，即 `app/llm/config_schema.SCHEMA`）包含分组与实际配置项——见 config_schema.py 全量，归纳：
  - **分组（type:group）**：`group_api` API 设置 / `group_model` 模型参数 / `group_switch` 功能开关 / `group_session` 会话管理 / `group_trigger` 触发设置 / `group_stream` 流式回复 / `group_proactive` 主动消息 / `group_schedule` 定时任务 / `group_permission` 权限。均 `collapsible:true`。
  - **字段类型分布**：
    - `password`: `api_key`
    - `text`/`string`: `api_base`, `model`, `stream_send_prefix`, `stream_send_suffix`
    - `select`: `provider`, `stream_send_interval_mode`, `stream_send_curve`, `stream_queue_full_policy`, `include_private_pre_history`, `permission`
    - `number`: `retry_attempts`, `max_tokens`, `temperature(step 0.1)`, `reply_cooldown`, `stream_sentence_max_length`, `stream_send_interval_base_ms`, `stream_send_interval_min_ms`, `stream_send_interval_max_ms`, `stream_send_curve_k`, `stream_short_message_length`, `stream_short_message_delay_ms`, `stream_long_message_delay_ms`, `stream_send_max_queue`, `session_timeout`, `history_rounds`, `max_message_length`, `proactive_min_interval_minutes`, `proactive_max_interval_minutes`, `proactive_max_unanswered`, `proactive_quiet_hours_start`, `proactive_quiet_hours_end`, `proactive_group_idle_minutes`
    - `boolean`: `group_enable`, `private_enable`, `stream_output`, `stream_send_pool_enabled`, `stream_send_by_sentence`, `stream_flush_on_finish`, `stream_keep_order`, `include_pre_history`, `trigger_at`, `proactive_friend_enable`, `proactive_group_enable`, `stream_proactive_enabled`, `schedule_enable`, `stream_scheduled_enabled`
    - `string_list`: `trigger_keyword`, `proactive_friend_sessions`(QQ号), `proactive_group_sessions`(群号)
    - `textarea`: `system_prompt`, `proactive_prompt(rows 5)`, `schedule_prompt(rows 7)`
  - 每个字段带 `group` 归属。`permission` 选项：`everyone`/`member`/`group_admin`/`group_owner`/`owner`。

### 7.2 Agent 页前端联动（agent 特殊面板）

- Agent 模块无独立 page（`has_page:false`），其 config 表单由框架 schema 渲染（与普通模块统一走 `/module/agent/config` POST）。
- **额外面板**（默认 `mod_name == 'agent'` 时渲染）：「定时任务」+「主动消息状态」两个表格，走 §2.3 的 tasks/proactive 端点。
- 前端 `loadAgentPanels()` 切到 Agent 卡片调用；无当前 Bot 时显示「请先选择账号」。

---

## 8. webui 配置结构（GET/POST /webui/config）

`config_service` 的 `DEFAULT_WEBUI_CONFIG`：

```jsonc
{
  "logs": {
    "visible_levels": ["info", "warning", "error"],
    "max_lines": 50,
    "console_height": 200
  },
  "single_service": {},                       // module -> bool
  "multi_group": { "show_all": false, "groups": {} }
}
```

- `get_webui_config()` 返回**深拷贝**；`save_webui_config` 以 `{**DEFAULT,**config}` 合并后整体落库并广播 `webui` scope。
- **logs**：过滤控制台级别（`debug/info/warning/error`）、显示行数、控制台高度。保存走 `POST /webui/config/logs`（只更新出现键）。
- **single_service**：`{ "<module>": bool }`。启用后该模块群消息仅由「指定服务账号」处理；前端据此显示黄色警告条（见 §9）。保存 `POST /webui/single-service`（整值替换）。
- **multi_group**：`{ "show_all": bool, "groups": { "<group_id>": { "service_bot_index": int } } }`。`groups[gid]` 无 `service_bot_index` 视为未指定；空选择即删除该 gid 条目。保存 `POST /webui/multi-group`（整值替换）。`show_all` 控制前端是否展示仅单账号在的群。
- **module_preferences**：`get_webui_config()` 中不存在默认值；由 `POST /webui/config` 或 `POST/GET /webui/module-preferences` 读写。用途：模块/用户的本地 UI 偏好（如排序、固定、隐藏、折叠），**前端自行约定 key 结构**，后端仅原样存取一个 dict。旧前端在 `index.html`/`main.js` 中对此字段没有实际消费（后端已保留该接口；重做时可自由设计结构，例如 `{ pinned:[], hidden:[], collapsed:{} }`）。

---

## 9. 旧前端需在新版复刻的细节行为清单

1. **账号切换与持久化**（`main.js switchBot` / `restoreBotSelection`）：
   - `localStorage['qqbot_current_bot_index']` 记住当前账号 index，刷新恢复；失效回退第一个。
   - 切换后：更新顶栏状态、`loadModulesForBot`、`refreshAllModulesData`、`reloadDataWidgets()`（重拉 list/dynamic）、`initPluginPages()`（iframes 换 bot_id）、`updateAllSingleServiceWarnings()`。
2. **统一鉴权封装**：`apiFetch`（Bearer Header）+ `apiWsUrl`（token 进查询串）。所有 fetch 走这俩。
3. **配置自动保存**（`main.js` §自动保存）：
   - 配置/权限输入后 2s（`AUTOSAVE_DELAY=2000`）防抖自动保存；`markModuleDirty`→定时 `doAutoSave`→`saveConfig/savePermission(silent)`。
   - 卡片标题显示 `save-status-*` 徽标：`dirty`/`saving`/`saved(时间)`/`error`。
   - 手动「保存配置」按钮 = `forceSave`（立即保存兜底）；`markAllModulesClean` 在数据回填同步后清掉未保存标记。
   - widgets 的 change/input 也会 `markModuleDirty`。
4. **config-updated 高亮**：`updateInputValue` 在回填/同步时给输入加 1s `config-updated` 类。
5. **单服务警告条**（`updateSingleServiceWarning`）：
   - 模块卡顶部 `single-service-switch-*` 开关（全部账号共有）。
   - 黄色警告 `single-service-warning-{mod}`：启用单服务且当前账号不是群指定 `service_bot_index`、且群内在线 Bot≥2 时显示，文案列出受影响群号（对齐后端 `is_single_service_skipped` 逻辑）。数据源 `GET /webui/multi-group` + `GET /bots/groups`。
6. **多群管理弹窗**（`renderMultiGroupList`）：
   - `show_all` 勾选时展示所有群，否则只展示 ≥2 账号的群；按群内账号数降序。
   - 每行 `<select>` 设置群的服务账号 `service_bot_index`；单账号群 disabled 但默认选唯一账号。变更即 `POST /webui/multi-group`。
7. **模块搜索过滤**（`renderModuleList`）：输入框匹配 `data-name`/`data-sign`/`data-tags`（全小写）。
8. **模块分组/折叠**：无搜索词时按 `module.category`（`data-category`，缺省「未分类」）分组，`localStorage['qqbot_module_collapsed']` 记录各分组折叠态。`toggleModuleView` 切换网格/列表（`module-grid` class）。
9. **快捷定位**：快捷键 `/` 聚焦模块搜索框（避开 INPUT/TEXTAREA）。
10. **日志控制台**：
    - WS 每秒快照数组增量渲染；`logCache` 尾部 diff；`logFilterText` 关键字过滤匹配 message/level/timestamp；`logPaused` 暂停时累积 `pendingLogCount`，继续时全量重绘。
    - 服务端初始日志（Jinja 渲染）在 WS 首包后 `firstBatch` 清空避免重复。
    - `console_height` 拖动存 `localStorage`（min100/max600）。
    - 清空 = 本地 `logCache=[]` 清 DOM（不调后端）。
11. **WS 事件分发与去重**：`handleConfigUpdate` 按 `type` 分发；`module_config_updated`/`module_authority_updated`/`permission_updated` 只有在匹配当前 bot（`data.bot_id===currentBotId||null`）时才处理；模块开关/权限操作后 `isRecentOperation`（2s 内）抑制重复 toast。
12. **Bot 状态实时**：`handleBotStatusUpdate` 合并 `botsData`、更新顶栏与账号卡片状态徽标、状态变化 toast（error 带 `last_error`）。
13. **模块数据刷新**（`refreshAllModulesData`）：每次拉 `GET /modules?bot_id=`，回填开关（`switch-{name}`）、权限控件、config 输入与 widgets，最后 `markAllModulesClean`。自动保存触发/WS `modules_reloaded` 均走它。
14. **Agent 面板**：切到 `agent` 模块卡片时加载 tasks/proactive 两个表格；空态显示 `—`；操作后重新拉取。
15. **无 Bot 容忍**：模块 list/dynamic 在无当前 Bot 时不请求、提示「连接 Bot 后获取数据」；`refreshAllModulesData`/`loadModulesForBot` 在无 bot_id 时静默返回。
16. **密码脱敏哨兵**：渲染 config（`password` 打码 `••••••••`）；保存时后端识别哨兵保留旧值；切换账号/刷新以服务端最新值作准。Bot access_token 同理（`/bots/config` 回显真实 token，`/bots/get_bots_info` 不回显）。

---

## 附：打码哨兵常量

- 模块/Agent 配置 password：`PASSWORD_MASK = "••••••••"`（`app/services/bot_service.py`）
- Bot access_token：`ACCESS_TOKEN_MASK`（`config_service`）；`/bots/config` 回显真实值，`/bots`(get_bots_info) 不含 token。
