# 长期记忆（Memory）设计定稿

> 状态：P0~P5 已完成并测试通过。本文档为设计基准，未来升级（embedding/RAG）按第 5 节走。

## 1. 目标与非目标

- 目标：为 Agent 提供**跨会话、跨过期**的对话事实记忆（“小明喜欢美式”这类）。
- 非目标（现阶段）：语义联想、文档/知识库 RAG、向量数据库。见第 5 节升级路径。

## 2. 分层

- **L1 会话记忆**：`private_<uid>` / `group_<gid>` 范围内的事实。
- **L2 用户画像**：群内按成员隔离（`user_<uid>@group_<gid>`）；跨群画像（`user_<uid>`）默认关闭（隐私）。
- **L3 全局**：不使用用户内容，保留 `global` 占位。

## 3. 存储（SQLite）

- 位置：`data/llm/<bot_id>/memory/memory.db`（每 bot 单文件，磁盘级隔离）。
- 表：`memories`（事实）+ `memory_events`（审计）。
- owner 路由：

| 场景 | owner | 可见 |
|---|---|---|
| 私聊 `private_<uid>` | `user_<uid>` | 该用户 |
| 群公共 | `group_<gid>` | 全群 |
| 群内某成员 | `user_<uid>@group_<gid>` | 仅该群 |
| 跨群 | `user_<uid>` | 需 `memory_user_cross_group=true` |

- 隔离由 owner + `scope_owners()` **代码强制**，不在召回层越权。
- 上限淘汰：`memory_max_per_owner`（默认 300），按 `重要度×时间衰减` 最低者淘汰。
- 审计：write / read / inject / delete / forget / clear / distill 全留痕，`#chat memory audit` 查看。

## 4. 能力与接入点

| 能力 | 实现 | 接入 |
|---|---|---|
| 召回注入 | `recall.py::rank/render_block` + `MemoryManager.recall_block(_async)` | `chat.py` 三入口 + `scheduler._build_messages` + `proactive._check_and_chat` |
| 群聊提及扩展 | `MemoryManager.mention_owners_for`（@ + 昵称→uid，TTL 缓存） | 注入与工具召回双链路 |
| 原生工具 | `tool.py`：memory_save / memory_recall / memory_delete | `chat._collect_llm_ext` 按会话追加（复用 ToolSpec + 工具循环） |
| 确定性兜底 | `detect.py`：“记住/我喜欢/我叫…”直接入库 | `chat.py` 三入口 `_maybe_autosave` |
| 隐式蒸馏 | `extract.py`（按用户分组、LLM 一次调用）+ 限频 | 回复后 `_fire_consolidate` + 会话归档 `session.on_archive` |
| 管理命令 | `commands.py` | `#chat memory list / search / forget / clear / audit` |
| 配置 | `config.py` 默认值 + `config_schema.py` Web 表单 | `group_memory` 分组 |

消息流示意（群聊“小明喜欢什么”）：
```
user 消息 → 会话历史 + ② 记忆注入(召回 owner=群公共+本人+提及小明定向)
        → provider（可调 memory_recall 深挖）
        → 回复 → ⑤ 确定性兜底/蒸馏入库 → 会话归档时整段蒸馏(force)
```

## 5. 升级路径（embedding / RAG 接缝）

- **存储合同不变**：`memories` 表已预留 `embedding BLOB` 列，`MemoryStore` 仍是唯一事实源。
- **召回只改 `recall.py`**：`rank()` 是纯函数。将来升级 = 写入时后台补 embedding → `rank()` 内先关键词预筛 top-K → 向量余弦精排 → 与重要度/新度加权融合。上层（工具/注入/命令/审计）零改动。
- **RAG（文档/知识库）**：另建独立模块（如 `app/llm/knowledge/`），挂同一 `ToolContext`/ToolSpec 工具体系（`knowledge_search`），**不要**耦合进 memory。
- 触发时机：当记忆量上万条或出现真实“想不起来”反馈且关键词召回假阴性高时，再引入 embedding；在此之前不上，避免依赖/成本/调优开支。

## 6. 回滚与安全

- `memory_enable=false` 一键关闭（数据保留）；删除 `memory/` 包 + `agent` 配置 `memory_*` 字段即回退。
- 记忆按 bot、按 owner 隔离；跨群默认关；`#chat memory forget/clear` 可定点清除；audit 全留痕可解释可删除。
- 蒸馏触发额外 LLM 调用，靠 `memory_extract_interval_min` 限频，任何异常静默降级不阻断主流程。

---

## 7. v2：权威降级 · 置信度 · 状态管理

> 定位：记忆是「模型对聊天历史的**模糊记忆**」，不是需要遵守的规则，也不是事实断言。
> 缓解“错误讨论 / 重置后旧记忆仍生效”靠以下机制。

### 7.1 记忆不再像“事实”
- 注入块标题为「供参考（可能不准确）的历史记忆」；
- 内容按置信度加试探前缀：高（≥0.75）陈述、中（0.5~0.75）`（好像）`、低（<0.5）`（记不太清）`；
- 低置信默认不注入（被点名时才上浮供核对）。

### 7.2 置信度
- 来源定起点：tool 0.8 / deterministic 0.75 / correct 0.85 / extract 0.55；
- 确认 `confirmed=1` 上调、再次出现累计 `evidence_count`（评分上浮）；
- 过滤：`memory_min_confidence`、`memory_max_age_days`、`expires_at`。

### 7.3 状态管理（`status`）
- `active` → `correct` 置 `superseded` + 写新说 active；
- `active` → `deny` 置 `negative`（下架不注入、可恢复）；
- 矛盾判重（否定词对：喜欢↔不喜欢/喝不惯…）写在 `upsert_fact(supersede_conflicts=True)`：用户明确来源自动下架旧说，防止新旧双条并存；
- 近义（相似度 ≥0.85）自动合并为一条；
- 真删除仅 `clear` / `reset hard`；`list --all` 可查全部状态。

### 7.4 蒸馏只“复述”不“捏造”
- 只记录用户明确说过的内容；行内含推断词（看起来/应该会/估计…）直接丢弃；
- 输出信度词 `[很确定]/[好像]/[不确定]` → confidence 0.65/0.55/0.45；
- 蒸馏不自动下架既有记忆（避免误杀）。

### 7.5 会话重置（suspend/clear/keep）
- 只在**显式**重置（`#chat new` / `#chat exit` / `#chat stop` / `#chat memory reset`）写 `last_reset_at`（被动超时归档只做蒸馏，不挂起，避免频繁断记忆）；
- suspend（默认）：`updated_at < last_reset_at` 的旧记忆默认不注入，仅「已保存/已确认」型保留（`memory_upgrade_saved_only`）。

### 7.6 纠错闭环入口
- 命令：`#chat memory list [--all] / correct <旧> <新> / deny <词|id> / confirm <词|id> / reset [hard]`；
- 工具：`memory_correct` / `memory_deny`（模型在对话中感知“记错了/不是这样/我没说过”时调用）。

