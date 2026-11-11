# 官方 QQ 机器人：待办与恢复清单

> 用途：跨会话/关机后续做的**唯一待办入口**。状态与代码同步，完成一项就改这里并提交。
> 方案与架构见 [QQ_OFFICIAL_BOT.md](QQ_OFFICIAL_BOT.md)，协议事实见 [QQ_OFFICIAL_API.md](QQ_OFFICIAL_API.md)。

## 0. 一句话状态（2026-11-11 更新）

Phase 0 / 1 / 2 与 Phase 3（Webhook + 主动消息开关/日配额）**已落地**；全量 pytest 2926 passed。
剩余：**真机联调**（需要 AppID/AppSecret）、per-account 主动消息 UI、前端截图重验。
工作区提交后即 clean，可随时关机。

## 1. 已完成（按提交）

| 提交 | 内容 |
|---|---|
| `444bfc13` | Phase 0：协议能力真源 + 字符串 ID（`app/domain/protocol.py`、`ids.py`、`IBot` 能力接口） |
| `2f0ad7a4` | Phase 1：`qqbot` WS 适配器（token/事件/去重/被动窗口/撤回/群学习）+ `GatewayHub` + 配置列 |
| `62339e58` / `53de715a` | 修复：子网关 provider 过滤被覆盖（重启后幽灵账号）、合并连接表兼容 int key |
| `e8daf179` | 首启向导支持官方账号 |
| `6c38881e` | 官方账号强制单条输出（禁流式/发言通道/拆分） |
| `92b1deaf` / `8be86797` | 清理 + 上下文 ID 容错 |
| `763f305e` | 文档状态 |
| `32830c22` / `0d0fb89d` | 前端：协议三选一、能力拦截、dist 重建（51 张截图 overflow 全 0） |
| `371d44ab` | 账号状态暴露 `transport/app_id` |
| `ad2ef66f` | Phase 2：出站富媒体（URL / 本地分片上传）+ 群信息/成员/禁言/踢人 |
| `f3bed116` | Phase 3：Webhook（Ed25519 验签 / op13 / op12）+ `cryptography` 依赖 |

## 2. 待办（按优先级）

### P0 真机联调（凭据已提供，2026-11-11 进行中）

- [x] 取 access_token（`api.bot.qq.com`）→ 成功，7200s。
- [x] WS 模式：连接成功、收到 READY（机器人 P.L.A.N.A，bot 用户 id 10573840902808435688），`/api/bots` 状态 connected。
- [x] C2C 私聊：收到文本 + 图片附件事件（openid `39EEAC…`），事件时间/附件解析正常。
- [x] 被动文本回复：带 `msg_id/msg_seq` 发送成功（用户确认收到）。
- [x] 本地图片分片上传 → `msg_type=7` 发送成功（库路径 + 调试脚本各一次），2 分钟内撤回成功（用户确认看到图片）。
- [x] 群 @ 消息：`GROUP_ADD_ROBOT` + `GROUP_MESSAGE_CREATE` 均收到；群文本/群图片被动回复成功、群图片撤回成功（真机 2026-11-11）。
- [x] 群信息 `GET /groups/{openid}/info` 真机可用（群名/成员数已返回并写入 known_groups）；群成员列表返回 40012010（无内邀权限）；禁言返回 40011030（机器人非管理员）——均按可读错误降级。
- [x] 应用层群回复：`/api/bots/{id}/test-message` 走服务器内连接发送成功（用户收到「应用层测试…OK」）。
- [x] 「@ 我」触发：带 @ 的群消息会进入 Agent 流水线（日志停在「没有可用模型」，说明门控已放行）；不带 @ 的全量群消息不触发。
- [x] 群名自动补全：下一次群消息后 known_groups/账号群列表显示真实群名。
- [ ] LLM 端到端回复：测试库未配模型（日志 `no_api_key`/`没有可用模型`），需在 Agent 页接入模型后再验一轮。
- [ ] Webhook：公网反代到 `/qqbot/webhook/{account_id}`，平台配置回调，验证 op13 与事件 ACK。

> 真机发现并修复 1：分片上传 `parts[].index` 实际从 1 开始，原实现按 `index×block_size` 取片会得到空片 →
> 合并报 `40034002`。已改为按返回顺序累计偏移，并加单测锁住。
> 真机发现并修复 2：`GROUP_AT_MESSAGE_CREATE` 的 content 已去掉 @ 前缀、mentions 不含自己，
> 原实现缺 `at(self_id)` 段 → 「仅 @ 我」永远不会触发；全量 `GROUP_MESSAGE_CREATE` 的
> `<@member_openid>` 也需要映射到 self_id。已修并加单测。
> 真机发现并修复 3：known_groups 历史缓存会把 group_id 当 name 存，导致群名查询被跳过/被空值覆盖；
> 已把「占位名 = group_id」视为未知，并在刷缓存时保护真实群名。


### P1 收尾开发

- [x] 主动消息（2026-11-11）：`.env` 开关 `QQBOT_ALLOW_ACTIVE_MESSAGE`（默认 false）+ 每群/用户日配额 `QQBOT_ACTIVE_DAILY_LIMIT`（默认 20）；窗口外超限返回 `ACTIVE_QUOTA_EXCEEDED`。
- [ ] 主动消息 per-account 配置与 `GROUP_MSG_REJECT/RECEIVE`、`C2C_MSG_REJECT/RECEIVE` 事件持久化（用户/群主关闭后自动停发）。
- [x] 单聊 `stream_messages` 评估：**不做**。群聊没有流式发送接口；单聊需要额外维护流式会话与 msg_seq 语义，收益低于成本，官方账号继续单条整段发送。
- [x] WebUI 能力矩阵：工具总览（每工具 `available/reason_text`）+ 账号卡片 `capability_notes` + OneBot 工具页整页拦截，已覆盖「哪些工具在官方账号可用」。
- [x] README「接入 QQ」补官方机器人与 Webhook 部署说明；`docs/README.md` 已登记本文件。
- [x] `docs/QQ_OFFICIAL_API.md` 补 2026-10 群管理/上传接口字段（群信息、成员列表、禁言、批量移除、upload_prepare/part_finish/files）。

### P2 质量与验收

- [x] 全量 pytest：2026-11-11 跑出 **2926 passed, 19 skipped**（100s）；ruff 新增文件无违规。
- [x] 前端协议目录回归（2026-11-11）：9311 预览 + 临时脚本截图「添加账号」弹窗（1440/390）：
      3 张类型卡正常，官方下 WebSocket/Webhook 两张方式卡正常，`overflow=0`、弹窗在视口内；
      截图在 `logs/review/dialog-shots/`（gitignore）。
- [x] 官方账号拦截态回归：OneBot 工具页无工具行、工具总览 `capability_missing` 徽标、高级选项禁用（2026-11-10 截图验证，本轮无前端改动）。

## 3. 恢复步骤

```powershell
git status            # 应为 clean
git log --oneline -5
venv\Scripts\python.exe -m pytest tests\infrastructure\qqbot tests\webui\test_qqbot_webhook_api.py -q   # 快速冒烟
venv\Scripts\python.exe -m pytest -q                                                                    # 全量验收
```

## 4. 已知限制（不要当 bug）

- 官方群默认只收 @ 消息；全量需群主开「接收所有消息」。
- 群名/成员列表依赖「群信息/成员」接口权限（多数账号 11253）；群列表只能从事件学习。
- openid 按 AppID（群聊还按群）隔离，跨账号不可关联；拥有者/管理员名册按账号填 openid。
- 被动窗口：群 5 分钟 / 5 次，单聊 60 分钟 / 4 次；官方账号默认禁止主动消息、强制单条输出。
- Webhook 只能监听 80/443/8080/8443，需要公网 HTTPS 与反向代理；与 WS 二选一。
- Webhook 验签依赖 `cryptography`（已加入 `pyproject.toml`；旧 venv 需 `pip install -e .` 或 `pip install cryptography`）。
- `file_info` 有 TTL，缓存是内存态（重启后重传，可接受）；单聊/群聊上传接口不互通。
