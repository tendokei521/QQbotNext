> 本文只负责：说明 `app/agent/session/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/session/` —— 会话与历史：切换 / 落盘 / 渲染 / 富化。

## 包含

| 文件 | 职责 |
|---|---|
| `manager.py` | `SessionManager`：会话生命周期、当前会话、落盘时机 |
| `history.py` | 历史持久化：`history_events` 只追加表 + `conversations.messages` 派生缓存 |
| `session_log.py` | `SessionLog`：`append` / `events` / `derive_messages`，投影规则只放 `user/message`、`assistant/message` 进 messages |
| `verify.py` | 在线自检：日志投影与内存历史对账 + 请求指纹可复现（`prompt_dump_verify` 默认关，不一致落 `desync-*.json` / `header-desync-*.json`） |
| `model.py` | 历史的数据模型（一条消息长什么样） |
| `enrich.py` | 富化：给历史补上图片/昵称等引用信息 |

## 注意

历史的**存储接口**在 `app/agent/storage/`，**数据目录**在 `app/infrastructure/fs/paths.py`。

历史是**追加式事件日志**：写入口在 `manager.py`（`add_message` / `upsert_streaming_assistant` /
`mark_last_assistant_message_id` 双写内存列表 + 事件），`messages` 只是派生缓存，读取方不变。
老库用 `scripts/migrate_session_events.py` 迁移（`--dry-run` / 自动备份 / 幂等）。

**日志里还有哪些事件**（都只入日志，不进模型可见历史）：`turn/start`、`turn/end`（带
`reason`/`rounds`/`interrupted`）、`request/header`（system 与工具定义指纹，供回放对账）、
`tool/call` 与 `tool/result`（后者用 `source_event_seqs` 回指前者）、`usage`、
`compaction/start|summary|end`（压缩括号，未闭合即说明上次压缩中途崩了）。

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` 与本包内文件。
- 不得 import `app.webui` / `app.bootstrap`。跨子包依赖请走对方 `__init__.py` 的导出。
- 新增文件前先问：它属于「取料 / 决策 / 发送 / 存储」哪一类？答不上来就别加。
