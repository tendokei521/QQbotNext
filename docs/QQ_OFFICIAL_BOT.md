# 官方 QQ 机器人适配方案

> 读者：决定「要不要做 / 怎么做」的人，以及落地改代码的人。
> 事实基线：2026-11 官方文档（`https://api.bot.qq.com`，接口域名 2026-08-10 迁移）；
> 协议细节与来源见 [QQ_OFFICIAL_API.md](QQ_OFFICIAL_API.md)。本文只写**改造方案**，不重复抄接口表。
>
> 状态：**方案稿，尚未实现**。当前代码只支持 OneBot v11；本文不承诺任何已支持的能力。

---

## 1. 结论

1. **适配的本质是「加第二种协议适配器」，不是重写框架。**
   本项目的领域层已经把「一个已连接账号」抽象成 `IBot`（`app/domain/bot.py`），
   业务（Agent、插件、工具、会话）只依赖领域事件与 `IBot`。官方协议只需要在
   `app/infrastructure/` 下新增一个 `qqbot` 适配器，产出同一套 `domain.events`
   并实现同一套发送方法；`app/agent/`、`app/plugin/`、`app/plugins/` 不需要改协议相关代码。

2. **推荐路线：原生适配器 + 协议路由（GatewayHub）。** 账号级选择协议，
   OneBot 账号行为保持不变，官方账号走新适配器。预估 2–3 周（单人，含联调）。

3. **不推荐把官方 API 包成 OneBot 协议端（桥接）作为长期方案。**
   桥接能「框架零改动」先跑起来，但 openid→ID 映射、被动回复窗口、去重、群列表
   都要在桥里自己造，等于自养一个协议端；框架每加一个特性还要改桥。
   仅在「先验证可行性 / 多框架共用同一个桥」时考虑，见 §7。

4. **最大风险不在代码，在产品形态。** 官方群默认只推 @ 机器人的消息；被动回复窗口
   群 5 分钟 / 5 次；openid 不是 QQ 号且跨 AppID 不可关联。这三条直接决定
   「参与度 / 主动接话 / 群管理 / 用户画像」在官方账号上能保留多少。

### 1.1 决定改造方式的事实

| 事实 | 对改造的影响 |
|---|---|
| 接口域名 2026-08-10 起统一 `https://api.bot.qq.com`；取 token 也是该域名 | 不要照抄社区旧代码里的 `bots.qq.com` / `api.sgroup.qq.com` |
| 鉴权头 `Authorization: QQBot <access_token>`；token 7200s，过期前 60s 内可刷新 | 适配器需要 token 缓存 + 提前刷新，不能每次调用都取 |
| 群 + 单聊事件只在一个 intent：`GROUP_AND_C2C_EVENT (1<<25)` | 第一版只订阅这一位；频道另算，暂不做 |
| 事件是 JSON 对象（content/attachments/mentions/message_scene），不是消息段数组 | 需要独立 codec，把官方字段映射成 `text/at/image/reply` 段 |
| 群默认只收 @（`GROUP_AT_MESSAGE_CREATE`）；开「接收所有消息」后收 `GROUP_MESSAGE_CREATE`（全量） | 官方群账号默认等价「仅 @ 触发」；全量模式要群主授权，且能力要按开关降级 |
| 发消息：`msg_type` 0 文本 / 2 markdown / 7 富媒体；被动回复带 `msg_id` + `msg_seq` | 出站层要维护「最近一条入站消息」窗口，并在每次发送时分配 `msg_seq` |
| 被动窗口：群 5 分钟 / 5 次；单聊 60 分钟 / 4 次；重复 `msg_id+msg_seq` 会失败 | 逐句流式发送会迅速耗尽次数 → 官方账号必须限制单轮出站条数 |
| 主动消息可用，但有频控、用户/群主可关闭、按认证等级限额 | 主动消息/主动性插件在官方账号上默认关，做配额会计后再开 |
| 富媒体必须先上传换 `file_info`，再 `msg_type=7` 发送；本地文件走分片上传 | 本地图片/语音/文件可以发，但不能直接把本地路径塞进消息 |
| 错误常以 HTTP 200 + body `err_code` 返回（如消息过期、无权限、限频） | 响应判定要看 `err_code`，不能只看 HTTP 状态 |
| 官方会重复推送同一 `msg_id` | 入站必须按账号 + 消息 id 去重，否则重复回复 |
| openid 按 AppID（群聊还按群）隔离；没有 QQ 号、群名、群成员列表（部分内邀） | 拥有者/管理员名册要支持填 openid；群名只能本地起别名 |
| WS 与 Webhook 二选一 | 本机部署默认 WS；Webhook 需要公网 HTTPS（80/443/8080/8443） |

---

## 2. 现有实现的耦合点（改哪里）

### 2.1 连接与事件入口

| 现状 | 官方差异 | 改造 |
|---|---|---|
| `app/infrastructure/onebot/gateway.py` 的 `_bot_server`（约 :520）按 OneBot 报文收发：先消费 `echo` 响应、再检查 `post_type`、最后 `decode()` 入队 | 官方 WS 是 `Hello → Identify → 心跳 → Dispatch`，没有 `echo`、没有 `post_type` | 新增 `QqBotGateway`；现有 `GatewayLifecycleMixin`（`gateway_lifecycle.py`，:83 起）的启动/监督/重连/僵尸扫描逻辑可复用，连接与收包部分按协议重写 |
| `OneBotGateway` 被 `bootstrap.py` 直接注册为唯一的 `gateway`，并注入 `BotService` | 两种协议需要按账号路由 | 新增 `GatewayHub`（见 §3），对外保持 `connections / add_bot / connect_bot / get_bots_info / recent_events / shutdown` 等方法面，`BotService` 与 WebUI 调用点零改动 |
| 登录信息靠 `get_login_info()` + `get_group_list()` 拉取（`gateway.py` `_get_login_info` / `_get_login_groups`） | 官方没有 QQ 号与群列表接口；`READY` 事件给机器人自身 id/昵称；群只能靠 `GROUP_ADD_ROBOT` / 群消息学习 | 官方账号登录用 `READY`；`get_group_list` 返回本地学习到的群（见 §2.4） |
| `IBot` 有 41 个 OneBot API 方法（`app/domain/bot.py`） | 官方只覆盖其中一小部分（发消息、撤回、少量群管理） | `QqBotConnection` 实现子集；未支持方法返回统一失败响应，**不要抛异常**（工具循环按响应判断） |

### 2.2 事件模型

`app/domain/events.py` 与 `app/infrastructure/onebot/codec.py`：

- 消息内容按 OneBot 消息段数组保存（`event.message` 是 `MessageSegment` 列表）；
- `user_id` / `group_id` / `message_id` / `bot_id` 都按 `int` 处理；
- `is_at_me()` 判断 `at` 段的 `qq == self_id`；
- 私聊的 `group` 恒为 `None`（判场景用 `is_private_event()`，不要看 `group`）。

官方事件是「content 字符串 + attachments + mentions + msg_elements」，因此：

- 新增 `app/infrastructure/qqbot/codec.py`，把官方事件直接构造为
  `GroupMessageEvent` / `PrivateMessageEvent` / `NoticeEvent`，复用现有 `event_type` 语义；
- `content` 里的 `<@member_openid>` 剥离为 `at` 段，`username` 映射 `UserInfo.nickname`，
  `member_role` 映射 `UserInfo.role`，`attachments` 映射 `image/record/file` 段（`file` 用附件 URL）；
- `message_type=103`（引用消息）与 `msg_elements` 映射 `reply` 段与引用文本；
- `timestamp` 是 RFC3339 字符串，不是 Unix 秒（`GROUP_ADD_ROBOT` 等事件例外仍是秒，需要兼容两种）。

### 2.3 发送与出站链

- 唯一发送出口是 `app/agent/delivery/channel.py`，它调用
  `bot.send_group_msg(int(target), ...)` / `bot.send_private_msg(int(target), ...)`（:116–118）。
  **`int(target)` 会让 openid 直接抛 `ValueError`**，必须改为字符串 ID。
- 所有发送都会经过出站节点链：`app/plugin/nodes/outbound.py` 的 `SendNode`（:44）
  调 `bot._direct_send(action, params)`，`action` 是 OneBot 名（`send_group_msg` 等），
  `params` 是 OneBot 参数。因此 **`QqBotConnection` 必须提供一层「OneBot action → 官方请求」映射**，
  而不是让上层改写成官方 API。
- 插件与工具仍可能调用 `bot.call_api("send_group_msg", {...})`，同一映射层覆盖即可。

建议映射：

| OneBot action | 官方实现 |
|---|---|
| `send_group_msg` | `POST /v2/groups/{group_openid}/messages`（`msg_type=0`，带被动 `msg_id/msg_seq`） |
| `send_private_msg` | `POST /v2/users/{user_openid}/messages` |
| `send_msg` | 按 `message_type` 分派到上面两个 |
| `delete_msg` | 需要 scene+target：适配器自己维护「发出去的 message_id → (scene, target)」映射 |
| `get_login_info` | `READY` 快照 |
| `get_group_list` | 本地学习的群（见 §2.4） |
| 其余（戳一戳、合并转发、群文件、历史消息…） | 返回 `{"status": "failed", "retcode": "unsupported", "message": "该协议不支持"}` |

### 2.4 群列表与群名

- `app/application/group_service.py:105` 的 `refresh()` 调 `conn.get_group_list()`，
  成功后写 `known_groups`（表结构在 `database.py:119`，`group_id` 本来就是 `TEXT`）。
- 官方没有「机器人已加入群列表」接口。让 `QqBotConnection.get_group_list()` **返回本地学习的群**
  （`GROUP_ADD_ROBOT` / `GROUP_DEL_ROBOT` / 群消息里的 `group_openid`），
  `GroupService`、群选择器、按群配置都不用改。
- 群名官方不提供：`known_groups.name` 先用 openid 尾号占位，允许用户在 WebUI 里改别名
  （群名只在展示层用，不参与协议判断）。

### 2.5 ID：统一用字符串

- 持久化层已经是字符串：`known_groups`、`session_id`、`stat_event.group_id/user_id` 都是 `TEXT`；
  `app/domain/session.py` 也只做字符串前缀解析。
- 真正会炸的是代码里的 `int()` 强制转换。**必须清理的 13 处**：

| 文件 | 行 | 说明 |
|---|---|---|
| `app/agent/delivery/channel.py` | 116 / 118 | 发送目标 `int(target)` |
| `app/agent/loop/command_dispatch.py` | 47 / 49 / 288 / 290 | 指令回复与合并转发目标 |
| `app/agent/tools/session_tools.py` | 271 | 群成员信息 |
| `app/agent/context/group.py` | 525 / 578 / 598 | 群历史/私聊历史/群信息 |
| `app/agent/context/nicknames.py` | 82 | 群名片 |
| `app/plugins/llm_memory/src/manager.py` | 364 | 群成员列表 |
| `app/infrastructure/onebot/gateway_events.py` | 210 / 213 | `latest_group_id()` 只认 `int` 或纯数字字符串，openid 会被丢弃 |

- 事件 dataclass 的 `user_id / group_id / message_id / bot_id` 类型标注改为 `str | int`（或统一 `str`），
  `codec.py` 里 `int(payload...)` 的转换一并去掉（OneBot 的数字 ID 用 `str()` 保存即可，展示不变）。
- **不要**把 openid 哈希成整数：会碰撞、会丢可读性，而且多账号/多群时语义更糟。
- `bot_id`（展示用「当前账号」）对官方账号取 `READY.user.id` 或 AppID；存储路径与会话键继续用
  `account_id`（`SessionManager` 本身按 `str(bot_id)` 建键，字符串可用）。

### 2.6 工具与能力

- OneBot 工具清单有 175 项（`app/agent/tools/onebot/manifest.json`），
  `build_onebot_tools()`（`executor.py:300`）按 scope/权限生成。
- 官方账号只支持其中极少一部分。需要给 `IBot` 增加能力声明：
  `protocol: str` + `features: frozenset[str]`（`send_text / send_image / recall / group_admin / member_list / history …`），
  由 `build_onebot_tools()` 按 `bot.features` 过滤；`manifest.json` 每项补一个「所需能力」字段
  （默认 onebot-only）。
- 好处：模型看不到不可用工具，不会反复调用失败；工具页也能展示「本账号不可用」的原因。

### 2.7 账号配置与 WebUI

- `bots` 表（`database.py:141`）目前只有 `ws_url / conn_mode / owner_id / auto_connect`；
  `account_store.py:100`（`save_bots`）与 `:201`（`add_bot`）按这个形状读写。
- 需要新增列（启动期 `config_service._ensure_bot_columns` 幂等补列，老账号默认 `onebot`）：

```text
protocol      TEXT NOT NULL DEFAULT 'onebot'   -- onebot | qqbot
transport     TEXT NOT NULL DEFAULT ''         -- qqbot: ws | webhook；onebot 继续用 conn_mode
app_id        TEXT NOT NULL DEFAULT ''
app_secret    TEXT NOT NULL DEFAULT ''         -- 对外打码，日志脱敏
```

- `get_bots_public()` 要像 `access_token` 一样给 `app_secret` 打码（复用 `ws_url.py` 的
  `ACCESS_TOKEN_MASK` / `restore_ws_url` 思路），日志里不得出现 secret。
- 前端要改：

| 文件 | 改动 |
|---|---|
| `app/webui/dashboard/src/views/BotsPage.vue` | 账号卡片显示协议；官方账号隐藏「协议端地址」，改显示 AppID 与连接方式（WS/Webhook） |
| `app/webui/dashboard/src/components/bots/AddBotDialog.vue` | 先选协议，再选连接方式；官方账号收 AppID/AppSecret |
| `app/webui/dashboard/src/views/SetupPage.vue` | 首次向导支持选协议（默认 OneBot，保持现有体验） |
| `app/webui/dashboard/src/components/bots/ProtocolHelp.vue` | 增加官方机器人的开通步骤（q.qq.com 建号 → AppID/Secret → 拉进群 → 群主开权限） |

- 注意：仓库规则要求前端改动必须 `npm run build` 并重建 `dist/`（单独提交），
  本次是方案稿，尚未改前端。

### 2.8 就绪检查与诊断

- `app/application/readiness_service.py:157` 以「`ws_url` 为空」判定「没配协议端」。
  官方账号没有 `ws_url`，需要按 `protocol` 分支：检查 `app_id/app_secret` 是否齐全、连接状态如何。
- `app/webui/api/bots.py` 的诊断台（`:296` 最近事件、`:359` 测试发消息）复用现有接口即可：
  官方 payload 原样展示；但**测试发消息**在没有入站消息、没有被动窗口时只能走主动消息（有配额/开关），
  前端要提示，或改为「等收到消息后再测」。

---

## 3. 目标架构

```text
bootstrap
  └─ GatewayHub（新增：按 account.protocol 路由；对外保持 OneBotGateway 现有方法面）
       ├─ OneBotGateway（现有，行为不变）
       └─ QqBotGateway（新增）
            ├─ auth.py       access_token 缓存 + 提前刷新（7200s，过期前 60s）
            ├─ api.py        HTTPS OpenAPI：鉴权头、err_code 分类、限频/重试
            ├─ ws.py         gateway 连接：Hello → Identify → 心跳 → Resume → 退避重连
            ├─ codec.py      官方事件 → domain.events（含 @ 剥离、附件、引用、角色）
            ├─ connection.py QqBotConnection(IBot)：发送/撤回/能力声明/action 映射
            ├─ media.py      本地文件分片上传 → file_info（TTL 缓存）
            └─ inbox.py      msg_id 去重 + 被动回复窗口（msg_id/msg_seq）
```

**为什么用 GatewayHub 而不是把两个网关塞进 `OneBotGateway`：**
`BotService`、`WebUI`、`bootstrap` 都以 `gateway.connections` 为单一真源（`bot_service.py`、
`group_service.py`、`plugin/authority.py`、`webui/api/bots.py` 等）。Hub 提供合并后的
`connections` 与同名方法，调用点不用改；两个协议的生命周期、重连、诊断历史互不干扰。

**更彻底的重构（可选，Phase 3 再考虑）：** 抽出 `GatewayCore` + `ProtocolDriver`
（driver 负责 `start/stop/send/decode`），`OneBotGateway` 也改成 driver 组合。
收益是去掉 `GatewayLifecycleMixin` 里的 OneBot 假设；成本是要动测试最多的网关文件，
不建议和「新增协议」同批做。

### 3.1 事件映射表

| 官方事件 | 内部事件 | 要点 |
|---|---|---|
| `GROUP_AT_MESSAGE_CREATE` | `GroupMessageEvent`（`group_<group_openid>`） | `content` 已去 @ 前缀；`user_id=member_openid`；`nickname=author.username`；`role=member_role`；附件→image 段 |
| `GROUP_MESSAGE_CREATE`（全量，需群主开） | 同上 | 事件本身不是 @，触发判定改看 `mentions` / 关键词 / 参与度；`is_at_me` 需实测确认机器人自身 openid 在 content 里的形态 |
| `C2C_MESSAGE_CREATE` | `PrivateMessageEvent`（`private_<user_openid>`） | 单聊事件没有 `group`；`group` 恒为 `None` |
| `GROUP_ADD_ROBOT` / `GROUP_DEL_ROBOT` | 不派发业务事件，更新 known_groups | 入群/退群时刷新群列表缓存 |
| `FRIEND_ADD` / `GROUP_JOIN_REQUEST` 等 | `NoticeEvent` / `RequestEvent`（可选） | 第一版可以先只留日志 |
| `READY` | 登录信息 | 设置 `bot_id`/`login_info`，触发 `on_bot_login` |

`is_at_me()`：官方群 @ 事件天然就是 @ 机器人，可直接视为 `at_triggered=True`；
全量模式与引用回复需要 codec 合成 `at`/`reply` 段后再交给现有触发逻辑。

### 3.2 被动回复窗口与出站节奏

- `inbox.py` 按 `(account_id, scene, target)` 记录最近入站消息：
  `{msg_id, ts, seq, max_seq, window}`；群 `max=5 / window=300s`，单聊 `max=4 / window=3600s`。
- 每次发送：窗口内 → body 带 `msg_id`、`msg_seq=++seq`；窗口外 → 不带（= 主动消息）。
- **官方账号默认禁止主动消息**（配置项 `qqbot_allow_active=false`）。窗口外/次数用尽时：
  返回失败响应并在日志里写清「被动窗口已用完，本条未发送」，不要静默改走主动消息。
- 流式输出：官方目前只给了**单聊**的流式消息接口（`/v2/users/{openid}/stream_messages`），
  群聊未见流式发送接口，逐句发送会很快用完 5 次。因此官方群账号应强制「单条输出」：
  `stream_output=false`，关闭按句切分与发送池；或把一轮的多条合并成一次发送。
- 超长文本被官方限制切片时，**每一片都占一次被动额度**：宁可让模型短回复，也不要切片轰炸。

### 3.3 入站去重

- 官方明确「相同 `msg_id` 可能重复推送」。`inbox.py` 维护
  `account_id + event.id` 的 LRU（TTL 10–30 分钟）；命中直接丢弃并记 debug 日志。
- 与「同群多账号」不冲突：去重键带账号 id，每个账号各自处理自己的事件。

### 3.4 错误分类

| `err_code` / 现象 | 处理 |
|---|---|
| `304026` / `304027` / `40034005`（msg_id 错误/过期） | 不重试被动发送；若允许主动再发，否则明确失败 |
| `22009` / `1100100` / HTTP 429 | 指数退避 + jitter 重试，记限频日志 |
| `11253` / `11254`（接口无权限/被封禁） | 把对应 `feature` 置 false，日志提示去开放平台申请 |
| `304018`（机器人没连上 gateway） | 触发 WS 重连 |
| `304003`（URL 未报备） | 去掉/替换 URL，提示在开放平台配置「消息 URL」 |
| `50045` / `50046` / `50047` | 非 @ 消息/回复语义错误：不重试 |
| token `100001` / `100016` | 丢弃缓存重新取；secret 错则把账号状态置 error |

### 3.5 富媒体（Phase 2）

- 本地上传：分片上传（`upload_prepare` → 分片 `PUT` 预签名 URL → `part_finish` → 带 `upload_id` 合并），
  不需要公网 CDN；小文件也可整文件 URL 上传（要求公网可访问）。
- `file_info` 有 TTL，按 `(account, scene, target, content-hash)` 缓存；单聊与群聊上传接口不互通。
- 入站附件：`attachments[].url` 直接作为 `image` 段的 `file`，现有图片流水线
  （`app/agent/media/image.py`）支持 http 直链下载。

### 3.6 Webhook（Phase 3，可选）

- FastAPI 路由（同进程）`POST /qqbot/webhook/{account_id}`：
  用 `X-Signature-Ed25519` + `X-Signature-Timestamp` 验签（Bot Secret repeat 成 32 字节 seed）；
  `op=13` 返回 `plain_token + signature`；普通事件回 `op=12` ACK 后入队（处理放后台 worker）。
- 官方只允许 80/443/8080/8443，通常要反代；与 WebSocket **二选一**，切换即时生效，
  所以账号配置里 `transport` 必须显式。
- 本机部署默认 WS：不需要公网、不需要证书，功能等价。

---

## 4. 分阶段实施

### Phase 0：解耦（不改行为，可独立提交）

- [ ] 清理 §2.5 的 13 处 `int()`，事件 dataclass 的 ID 类型改为字符串。
- [ ] `IBot` 增加 `protocol` / `features` / `supports(action)`；`BotConnection` 声明 OneBot 全能力。
- [ ] `build_onebot_tools()` 按能力过滤；OneBot 账号行为保持不变。
- [ ] 抽出 `GatewayHub`，先只包 `OneBotGateway`，跑通现有网关测试。
- [ ] 契约测试：字符串 ID 收发、能力过滤、未支持 action 返回失败而不是抛异常。

**验收**：现有测试与架构闸门通过；OneBot 账号行为无变化。

### Phase 1：文本收发 MVP（核心）

- [ ] `bots` 表与账号读写增加 `protocol/transport/app_id/app_secret`（打码 + 迁移）。
- [ ] 新增 `app/infrastructure/qqbot/{auth,api,ws,codec,connection,inbox}.py`。
- [ ] `GatewayHub` 路由 + `bootstrap.py` 装配。
- [ ] 事件：群 @、C2C、全量群消息（能收到就收）；`READY` 登录。
- [ ] 发送：文本 + 被动窗口 + `msg_seq`；撤回自己消息；action 映射表。
- [ ] 群学习：`GROUP_ADD_ROBOT/DEL_ROBOT` + 群消息 → `known_groups`；`get_group_list` 兼容。
- [ ] 官方账号：强制单条输出、主动消息默认关、工具按能力过滤。
- [ ] WebUI：协议选择、AppID/Secret、状态与诊断台文案（`dist` 重建单独提交）。
- [ ] 测试：codec fixture、`msg_seq` 分配、去重、token 刷新、错误映射；
      假 OpenAPI/WS 服务端的集成测试。

**验收**：测试群里完成私聊、群 @、全量（若开）、断线重连、重复推送不重复回复、
token 过期自动刷新、发送失败原因可见；架构闸门通过。

### Phase 2：富媒体与群能力

- [ ] 本地文件分片上传 → `file_info`（TTL 缓存）→ `msg_type=7`；图片/语音/文件。
- [ ] 入站附件 → 图片流水线；引用/聊天记录（`message_type=103`、`msg_elements`）→ reply 段。
- [ ] 群禁言/踢人/成员列表（内邀）按 `features` 暴露；`11253` 时自动降级。
- [ ] 主动消息（可选）：配额会计 + 用户开关 + 默认关。
- [ ] markdown 与 @ 语法（`<qqbot-at-user>`）按需。

### Phase 3：Webhook / 流式 / 收尾

- [ ] Webhook 模式 + Ed25519 验签 + `op=12` ACK + 回调地址配置说明。
- [ ] 单聊 `stream_messages` 评估（群聊无流式）。
- [ ] WebUI 能力矩阵（哪些工具/插件在官方账号可用）。
- [ ] 更新 README「接入 QQ」与 `docs/README.md`。

---

## 5. 风险与未确认项

**未确认（官方文档未给数值，勿写死）**：Webhook 超时与重试次数；`content` 字数上限；
主动消息每月配额。

**风险**：

1. **协议易变**：2026 年已多次调整域名、字段与能力；端点与字段要集中到常量 + fixture，
   升级只改一处，并保留一份「官方返回样本」回归。
2. **权限/认证不确定**：未认证只能加自己是群主/管理员的群；个人认证不支持公开；部分接口
   内邀（`11253`）。适配器要把「无权限」做成可解释的状态，而不是笼统失败。
3. **openid 隔离**：多账号同群时，同一个人在不同账号下 openid 不同，用户画像/记忆不能跨账号合并；
   拥有者/管理员名册要按账号填 openid。
4. **默认只收 @**：参与度模式、主动接话、群历史在官方账号上会明显缩水；全量模式依赖群主授权，
   且仍拿不到群名/成员列表。
5. **被动窗口**：群 5 分钟 / 5 次意味着长工具链、长思考、流式逐句都可能发不出去；
   必须在产品层限制「一轮最多几条/多长」。
6. **无沙箱**：只能用真实机器人 + 测试群联调；上线有审核周期。
7. **富媒体 TTL 与场景隔离**：`file_info` 过期要重传；单聊/群聊上传不互通。
8. **凭据安全**：AppSecret 落库、WebUI 打码、日志脱敏；IP 白名单可选（单 IPv4，最多 50 条）。

---

## 6. 与 OneBot v11 的能力差异速查

| 能力 | OneBot v11（现状） | 官方 QQ 机器人 |
|---|---|---|
| 用户标识 | QQ 号（数字） | `user_openid` / `member_openid`（字符串，按 AppID 隔离） |
| 群标识 | 群号 | `group_openid` |
| 群消息 | 默认全量 | 默认仅 @；全量需群主授权 |
| 群列表/群名 | `get_group_list` | 无接口，只能从事件学习；群名不可得 |
| 群成员 | `get_group_member_list` | 部分内邀 |
| 群管理 | 踢人/禁言/改名/名片 | 禁言、踢人（新）、成员列表（内邀）；改名/名片不行 |
| 好友/戳一戳/合并转发发送/历史消息/群文件/匿名 | 支持 | 不支持 |
| 发图 | 本地路径/URL/Base64 | 必须先上传换 `file_info` |
| 消息格式 | 消息段数组 + CQ 码 | content 字符串 + 附件/引用对象；@ 用专有标签 |
| 主动消息 | 无限制 | 有频控、用户/群主开关、认证等级限制 |
| 事件接入 | WS（正向/反向） | WS 或 Webhook（二选一） |
| 重复推送 | 少见 | 常见，必须去重 |

---

## 7. 备选方案：OneBot 桥接

```text
官方 QQ 机器人  →  [桥接进程]  →  反向 OneBot v11 WS  →  本框架（零改动）
```

- **实现**：桥接进程对官方扮演客户端（WS 收事件 + OpenAPI 发消息），对框架扮演 OneBot 协议端
  （反向 WS 连入），把官方事件翻成 OneBot 事件、把 OneBot action 翻成官方请求。
- **优点**：框架与 WebUI 完全不动；先验证可行性最快；同一个桥也能给别的 OneBot 框架用。
- **缺点**：
  - openid 是字符串，OneBot v11 的 `user_id/group_id` 通常是数字 → 桥里必须维护映射表
    （哈希会碰撞，递增表要持久化）；
  - 被动窗口、去重、群列表伪造、登录信息伪造都在桥里；
  - 官方能力差异仍存在（工具大多不可用），只是错误从「框架层」挪到「桥层」；
  - 等于多养一个协议端进程，长期维护成本高于原生适配器。
- **适用**：只想先跑通 / 多框架共用；不作为本项目的长期方向。

---

## 8. 来源

- 鉴权与调用：<https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/access-token.html>、
  <https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/api-call-guide.html>
- 事件与 op 码：<https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/event-emit/payload.html>、
  <https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/event-emit/websocket.html>、
  <https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/event-emit/webhook.html>
- 签名：<https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/sign.html>
- 消息概述与频控：<https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html>
- 群 @ 事件：<https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_at_message_create.html>
- 全量群消息：<https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html>
- 单聊事件：<https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/c2c_message_create.html>
- 发群消息：<https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html>
- 发单聊消息：<https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html>
- 富媒体：<https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/rich-media.html>
- 撤回：<https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages_message_id.delete.html>
- 群管理事件：<https://bot.q.qq.com/wiki/develop/api-v2/server-inter/group/manage/event.html>
- 唯一身份：<https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/unique-id.html>
- 变更记录：<https://bot.q.qq.com/wiki/develop/api-v2/changelog.html>
