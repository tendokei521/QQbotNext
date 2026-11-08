# 《QQ 开放平台官方机器人 API 调研》

> 调研时间 2026-11，以 bot.q.qq.com/wiki（更新至 2026-10/11）为准。**接口域名已统一为 `https://api.bot.qq.com`（2026-08-10 变更记录），旧文 `api.sgroup.qq.com`、`bots.qq.com` 已失效；与社区旧文冲突处，以官方文档为准。**

## 1. 鉴权

- **换 token**：`POST https://api.bot.qq.com/app/getAppAccessToken`，body `{"appId":"...","clientSecret":"..."}`，返回 `{"access_token":"...","expires_in":"7200"}`。
- **请求头**：`Authorization: QQBot {ACCESS_TOKEN}`。**确认是 `QQBot <token>`**，不是 `Bot <appid>.<token>`（Discord 风格，QQ 未采用）。WS Identify 的 `token` 同格式。
- **有效期**：7200 秒；期内重复获取返回同值（不续期）；过期前 **60 秒内**再取会发新 token，旧 token 该窗口内仍有效。需自行定时刷新。
- **错误**：业务错误走 body 的 `code`（100001 限频、100007 appid 无效、100016 secret 错、10004 不存在），**HTTP 仍 200**。
- **IP 白名单**：可选，**仅单个 IPv4、不支持 CIDR、最多 50 条**；不配则不限来源。

## 2. 事件接入

### WebSocket
1. `GET /gateway` 或 `GET /gateway/bot`（返回 `url`、`shards`、`session_start_limit`）→ `wss://api.bot.qq.com/websocket/`。
2. 连接后收 `op 10 Hello`，`d.heartbeat_interval`（示例 45000ms）。
3. `op 2 Identify`：`{token:"QQBot <AT>", intents, shard:[0,1], properties}`，成功下发 `op 0 / t=READY`（含 `session_id`）。
4. `op 1` 心跳，`d` 为最新 `s`（首帧 `null`），回 `op 11` ACK。
5. `op 6 Resume`：`{token, session_id, seq}`，补发遗漏事件后下发 `RESUMED`。
6. **intents**：`GUILDS 1<<0`、`GUILD_MEMBERS 1<<1`、`GUILD_MESSAGES 1<<9`（仅私域）、`DIRECT_MESSAGE 1<<12`、**`GROUP_AND_C2C_EVENT 1<<25`（群聊/私聊唯一开关）**、`INTERACTION 1<<26`、`MESSAGE_AUDIT 1<<27`、`FORUMS_EVENT 1<<28`、`AUDIO_ACTION 1<<29`、`PUBLIC_GUILD_MESSAGES 1<<30`。传无权限 intent 会直接断连。
7. **分片**：`shard:[id,num]`，`shard_id=(guild_id>>22)%num_shards`；连接数受 `remaining` 限制。
8. **op 码**：0 Dispatch、1 Heartbeat、2 Identify、6 Resume、7 Reconnect、9 Invalid Session、10 Hello、11 Heartbeat ACK、**12 HTTP Callback ACK（仅 webhook）**、**13 回调验证（webhook）**。WS 错误码 4001/4002/4006–4014、4914(仅沙箱)、4915(封禁)。

### Webhook
- HTTPS 回调，**端口仅 80/443/8080/8443**。
- 签名头：`X-Signature-Ed25519`（hex，64 字节）、`X-Signature-Timestamp`；签名体 = `timestamp + raw body`。公钥由 Bot Secret repeat 到 32 字节作 seed，再 `ed25519.GenerateKey(seed)` 派生。
- **回调验证（op 13）**：body `{"d":{"plain_token":"...","event_ts":"..."},"op":13}`，头含 `X-Bot-Appid`、UA `QQBot-Callback`；应答 `{"plain_token":"...","signature":hex(ed25519_sign(event_ts+plain_token))}`。
- 普通事件回 `op 12` ACK。
- **超时与重试次数：官方未给数值 → 未确认**。

## 3. 群聊与 C2C（v2）

**事件**：`GROUP_AT_MESSAGE_CREATE`（默认，仅 @）、`GROUP_MESSAGE_CREATE`（全量模式，需群主开启）、`C2C_MESSAGE_CREATE`、`GROUP_ADD_ROBOT`/`GROUP_DEL_ROBOT`、`GROUP_MSG_RECEIVE`/`REJECT`、`FRIEND_ADD`/`FRIEND_DEL`、`C2C_MSG_RECEIVE`/`REJECT`、`GROUP_MEMBER_ADD`/`REMOVE`、`GROUP_JOIN_REQUEST`。全部走 `1<<25`。

**字段**：`id`、`content`（已去 @机器人 前缀；@某人 为 `<@member_openid>`）、`group_openid`、`author{member_openid|user_openid, username, member_role, bot}`、`timestamp`（RFC3339 东八区，**非**旧文 Unix 秒；`GROUP_ADD_ROBOT` 例外仍为秒）、`message_type`（0 文本/3 卡片/102 聊天记录/103 引用）、`attachments[]{url, filename, size, content_type, voice_wav_url, asr_refer_text}`、`mentions`、`ark_data`、`msg_elements`、`message_scene{source, ext[]}`。

**发消息**：`POST /v2/groups/{group_openid}/messages`、`POST /v2/users/{openid}/messages`。body：`msg_type`（群 0/2/7/8；单聊另有 6=输入中）、`content`、`markdown{content, force_verify_image_resource}`、`media{file_info}`、`keyboard{id|content.rows}`、`message_reference{message_id}`、`card{type:"tuwen", content}`、`msg_id`、`event_id`、`msg_seq`、`is_wakeup`（仅单聊）、`input_notify`。**v2 群/单聊发送侧无 `ark`、`embed`**（结构化卡片只能收不能发）。
`msg_id` 与 `event_id` 二选一；`event_id` 仅支持 `INTERACTION_CREATE`、`GROUP_ADD_ROBOT`/`GROUP_MSG_RECEIVE`、`FRIEND_ADD`/`C2C_MSG_RECEIVE`。

**被动 vs 主动**：群 5 分钟 / 5 次；单聊 **60 分钟 / 4 次**。同名 `msg_id + msg_seq` 会去重失败，递增 `msg_seq` 多次回复。主动消息：群 60 qpm（认证）/30 qpm（未认证），单关系 20 qpm，**1000 条/群/天**；单聊 10 qps（未认证 5 qps & 30 qpm），20 qpm，**1000 条/用户/天**；用户或群主可关闭接收。**每月配额官方未标注 → 未确认**。`is_wakeup` 互动召回：30 天内 4 个周期各 1 条。

**富媒体**：`POST /v2/groups/{group_openid}/files`、`POST /v2/users/{openid}/files`。`file_type`：1 图片（png/jpg，软 20MB/硬 200MB）、2 视频（mp4，30/200）、3 语音（silk，20/200）、4 文件（200/200）。上传：`url` 直传、分片（`upload_prepare` → PUT 预签名 → `upload_part_finish` → 带 `upload_id` 合并，推荐）、二进制（≤5MB）。返回 `file_uuid`/`file_info`/`ttl`，`srv_send_msg=true` 另返回 `id`。**单聊与群聊上传不互通**，过期需重传。发送用 `msg_type=7` + `media.file_info`。

**撤回**：`DELETE /v2/groups/{group_openid}/messages/{message_id}`，2 分钟内；管理员可撤普通成员消息，普通成员只能撤自己的。

**@ 与格式**：`<qqbot-at-user id="member_openid" />`（群聊仅 markdown 支持）、`<qqbot-at-everyone />`（文本/markdown，需管理员，每群每天 1 次）；`<@userid>`、`@everyone` 即将弃用。`<emoji:id>` 仅频道。Markdown 已全量开放（2026-04-23），频道需内邀。**文本字数上限官方未给（仅错误码 40054007）→ 未确认**。

## 4. 权限与上架

- **主体**：未认证 / 个人认证 / 企业认证；机器人数个人 5 个、企业 10 个（同主体合计 50）。
- **服务范围**：未认证只能加自己是群主/管理员的群；**个人认证不支持公开使用**，进群上限 500；企业认证可公开。内部体验号码 20 个。
- **群聊能力**：群主添加机器人时「设置权限」可开：接收全量消息、主动推送、撤回成员消息（后两者需机器人是群管理员）。
- **沙箱**：新版管理端无沙箱，管理员账号直接测试；旧版沙箱群/频道已不支持（WS 4914 仍提沙箱）。**WebSocket 与 Webhook 只能二选一，切换即时生效。**
- **公域/私域**：仅频道用（`GUILD_MESSAGES`、`FORUMS_EVENT` 限私域）；群/单聊无此区分，只有「服务范围」与主体认证。
- **内邀接口**：群成员列表、群基本信息等返回 `11253`，标注「仅白名单机器人可用」。

## 5. 与 OneBot v11 的差异清单

**明确做不到**：① 拿不到 QQ 号——只有 `user_openid`/`member_openid`/`group_openid`，**不同 AppID 下同一人 openid 不同**；② 好友列表（仅进出事件）；③ 戳一戳；④ 合并转发的**发送**（仅能收 `message_type=102`）；⑤ 历史消息；⑥ 群文件；⑦ 匿名；⑧ 改群名/群名片；⑨ CQ 码（@ 改 XML 标签）；⑩ 文本消息直接带图 URL（须先上传换 `file_info`，markdown 可嵌公网图）；⑪ 私聊主动消息受配额与用户开关限制。
**默认受限**：只收 @ 机器人的消息，全量需群主授权。
**已能（旧结论过时，勿照抄社区）**：群禁言、群成员列表、批量移除、黑名单、入群审批（2026-08-10 / 09-03 新增）、撤回他人消息（2 分钟内、仅普通成员）。
**结构差异**：JSON 事件而非数组段；`timestamp` 为 RFC3339 字符串；错误码在 body 且 HTTP 200。

## 6. 未确认项

Webhook 超时与重试次数；`content` 字数上限；主动消息每月配额——官方均未给数值，勿写死。

## 7. 来源

- 鉴权/调用：https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/access-token.html ；https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/api-call-guide.html
- 事件与 op 码：https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/event-emit/payload.html ；.../event-emit/websocket.html ；.../event-emit/webhook.html
- 签名：https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/sign.html
- 消息概述与频控：https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html
- 发群消息：https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html
- 发单聊消息：https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html
- 群 @ 事件：https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_at_message_create.html
- 全量群消息：https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html
- 单聊事件：https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/c2c_message_create.html
- 富媒体：https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/rich-media.html ；.../autogen/api/v2_groups_group_openid_files.post.html
- 撤回：https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages_message_id.delete.html
- Markdown/文本交互：https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/markdown.html ；.../message/trans/text-chain.html
- 群管理：https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_info.get.html ；.../v2_groups_group_openid_members.get.html ；.../v2_groups_group_openid_restrict_chat_setting.post.html
- 上架/认证/IP 白名单：https://bot.q.qq.com/wiki/ ；https://bot.q.qq.com/wiki/bot_FAQ/
- 变更记录：https://bot.q.qq.com/wiki/develop/api-v2/changelog.html
