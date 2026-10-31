# 长期记忆 v3 重写设计（llm_memory 插件）

> 本文是实现契约：`app/plugins/llm_memory/src/*` 各文件的公开接口、数据格式、
> 打分公式与错误处理约定。实现必须以本文为准；若实现发现契约不可行，先改本文再改代码。

## 1. 为什么重写（旧实现的问题清单）

旧实现（v2）的机制可借鉴，但工程上有 8 个硬伤（详见 `docs/memory-design.md` 的评审补记）：

1. `#llm memory` 命令未接线（`handle_memory_command` 无调用方）；
2. 启用被已废弃的 `experimental_long_term_memory` 门卡死；
3. 会话重置 / 会话归档两条链路读 `runtime.memory`（字段已删）→ 死代码；
4. `negative` / `superseded` 不可恢复；同措辞再次出现不复活；
5. 冲突判定是裸子串，且与 0.85 近义合并叠加会把「不喜欢」合并进「喜欢」并静默翻转；
6. `keywords` 从不生成；召回只有整 token + 2-gram；
7. 同步 SQLite 直接跑在事件循环里；
8. 配置 schema 与代码 fallback 默认值多处不一致。

## 2. 目标与非目标

- 目标：跨会话、跨过期的事实记忆；**用户画像（profile）** 与 **事实记忆** 分层；
  写入走「原始语料缓冲 → 后台 LLM 巩固」，检索走「关键词 + 可选 embedding」混合召回；
  所有 DB 访问 `asyncio.to_thread`；管理命令与工具面完整可运维；旧库自动迁移。
- 非目标：图数据库 / 多跳图谱推理 / 文档 RAG（知识库是另一模块）/ 训练或微调。

## 3. 借鉴的市面方案（调研结论）

> 调研补充（2026-10-31，官方文档核对）：
> - **Mem0 开源版已改为 add-only**（官方 "Additive storage"），四操作只存在于论文/平台版；
>   本设计仍采用「候选集 + 一次 LLM 决策」（ADD/UPDATE/SUPERSEDE/NOOP）的论文范式，但
>   UPDATE/SUPERSEDE 必须带 target_id，且只做「旧条失效」不物理删除。
> - **Zep 社区版已废弃**，Graphiti 仅支持 Neo4j/FalkorDB/Neptune → 不引入；只复刻
>   bi-temporal 的落地部分（valid_from/invalid_at + episode provenance）。
> - **Letta memory blocks 已标 legacy**，但其「常驻画像块 + 字符预算 + 自曝用量」仍是最轻
>   的落地方式；本设计采用（画像块字符上限默认 800，渲染时标注用量）。
> - **LangMem** 的关键结论：召回相关性 = 相似度 + 重要度 + 新近度/使用频率；本设计打分
>   加入 `freq = min(1, log1p(hit_count)/5)` 项（见 §5.6）。
> - SQLite FTS5 无内置中文分词 → 不依赖 FTS5，继续用 Python 侧 token + 中文 2-gram；
>   向量用纯 Python 余弦（float32 BLOB），不引 sqlite-vec/numpy 依赖。


| 方案 | 借什么 | 不借什么 |
|---|---|---|
| Mem0 | LLM 抽取 + 记忆操作（ADD / UPDATE / DELETE / NOOP）、去重后写入 | 平台化/向量服务依赖 |
| Zep / Graphiti | 记忆的**时间有效性**（新说法使旧说法失效，而不是覆盖）、情景（episode）来源 | 图数据库、双时序完整实现 |
| Letta / MemGPT | **core memory blocks**（用户画像常驻，事实记忆按需召回）、自编辑工具 | 分页换页机制 |
| Memobase | 用户画像与 episodic 记忆分层、异步写入缓冲 | SaaS |
| A-MEM / Cognee | 记忆之间可关联（本版只保留 episode→memory 溯源，不做图） | 知识图谱 |
| 通用实践 | 混合检索（BM25 式词法 + 向量）、时间衰减、置信度、注入时的「可能不准确」降级措辞、prompt injection 防护 | — |

**关键取舍**：默认零依赖（纯 SQLite + 纯 Python 余弦，几千~几万条足够）；
embedding 是可选增强，未配置时自动退化为词法召回，不报错、不阻断聊天。

## 4. 数据模型（SQLite，per bot）

路径不变：`data/llm/<safe_bot_id>/memory/memory.db`。单连接 + `RLock`，建表后 `WAL`。
旧库（v2）在 `__init__` 里做**幂等增量迁移**：`ALTER TABLE ... ADD COLUMN` + 建新表，不删数据。

### 4.1 `memories`（保留表名，增量加列）

```sql
CREATE TABLE IF NOT EXISTS memories (
    id             TEXT PRIMARY KEY,
    bot_id         TEXT NOT NULL,
    owner          TEXT NOT NULL,
    kind           TEXT NOT NULL DEFAULT 'fact',      -- fact | profile | preference
    content        TEXT NOT NULL,
    keywords       TEXT NOT NULL DEFAULT '',
    importance     REAL NOT NULL DEFAULT 0.5,
    visibility     TEXT NOT NULL DEFAULT 'group',
    source         TEXT NOT NULL DEFAULT 'tool',      -- tool|deterministic|extract|correct|manual
    source_user    TEXT NOT NULL DEFAULT '',
    source_task    TEXT NOT NULL DEFAULT '',
    embedding      BLOB,                              -- float32 little-endian
    status         TEXT NOT NULL DEFAULT 'active',    -- active|superseded|negative
    confidence     REAL NOT NULL DEFAULT 0.5,
    confirmed      INTEGER NOT NULL DEFAULT 0,
    expires_at     INTEGER,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL,
    hit_count      INTEGER NOT NULL DEFAULT 0,
    -- v3 新增：
    valid_from     INTEGER NOT NULL DEFAULT 0,        -- 该说法开始生效的时间（Zep 式）
    invalid_at     INTEGER,                           -- 被新说法取代/否认的时间
    last_used_at   INTEGER NOT NULL DEFAULT 0,
    embedding_model TEXT NOT NULL DEFAULT '',
    embedding_dim  INTEGER NOT NULL DEFAULT 0,
    meta           TEXT NOT NULL DEFAULT '{}'         -- JSON：episode_ids / subject / reason 等
);
CREATE INDEX IF NOT EXISTS idx_mem_owner   ON memories(bot_id, owner, updated_at);
CREATE INDEX IF NOT EXISTS idx_mem_status  ON memories(bot_id, owner, status);
CREATE INDEX IF NOT EXISTS idx_mem_profile ON memories(bot_id, owner, kind);
```

### 4.2 `episodes`（原始语料缓冲，v3 新增）

```sql
CREATE TABLE IF NOT EXISTS episodes (
    id            TEXT PRIMARY KEY,
    bot_id        TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    owner         TEXT NOT NULL,           -- 说话人画像 owner（群聊=user_x@group_y）
    user_id       TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'user',
    content       TEXT NOT NULL,
    message_id    TEXT NOT NULL DEFAULT '',
    created_at    INTEGER NOT NULL,
    consolidated_at INTEGER               -- NULL=未巩固
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ep_dedupe ON episodes(bot_id, session_id, message_id, role)
    WHERE message_id != '';
CREATE INDEX IF NOT EXISTS idx_ep_session ON episodes(bot_id, session_id, consolidated_at);
```

### 4.3 `memory_events` / `memory_owners`

沿用旧表结构（审计 / owner 重置线），`memory_events` 增加 `ref_id TEXT DEFAULT ''`。

## 5. 模块接口契约

> 所有函数中文 docstring + 类型标注；异常永不裸吞（`except Exception as e:` 必须
> `logger.debug/warning` 留痕）；不做跨文件的私有名字依赖（`_` 前缀仅文件内使用）。
> 时间统一 epoch 秒；owner 命名沿用 `owner_private/owner_group/owner_group_member/owner_global`。

### 5.1 `src/vector.py`（Lead 不分配；T1 实现）

```python
def pack(vector: list[float]) -> bytes        # struct float32 LE
def unpack(blob: bytes | None) -> list[float] # 空/损坏 → []
def cosine(a: list[float], b: list[float]) -> float   # 长度不等或空 → 0.0，范围 [-1,1]
def norm(vector: list[float]) -> list[float]  # 零向量原样返回
```

### 5.2 `src/store.py`（T1 实现）

```python
@dataclass
class WriteOutcome:
    id: str
    action: str            # add | update | merge | superseded
    superseded: list[str]  # 本次被置 superseded 的旧记忆 id

class MemoryStore:
    def __init__(self, bot_id, db_path: str | None = None) -> None: ...
    def close(self) -> None: ...
    db_path: str; bot_id: str

    # ---- 读写 ----
    def upsert_fact(self, content, owner, *, importance=0.5, keywords="",
                    source="tool", source_user="", source_task="",
                    confidence=0.5, kind="fact", valid_from=None,
                    expires_at=None, meta=None, embedding=None,
                    embedding_model="", supersede_conflicts=False) -> WriteOutcome: ...
        # 规则（顺序固定）：
        # 1) 精确同内容（同 owner+kind，任意 status）→ update：confidence=MAX、
        #    evidence_count+1、updated_at=now；status 保持原值（negative 不复活）；
        # 2) supersede_conflicts=True 且命中否定极性 → 旧 active 行 status=superseded,
        #    invalid_at=now, 写 audit("supersede")；本行再插入（action="superseded"）；
        # 3) 词面相似 ≥0.92 且**极性相同** → merge（覆盖 content、保留 id、confidence=MAX、
        #    evidence+1）；否定/反义一律不合并（见 has_negation_conflict）；
        # 4) 否则 insert（action="add"）。
        # content 截断 500 字；空内容 ValueError。

    def get(self, mid) -> dict | None
    def get_owned(self, mid, owner) -> dict | None
    def list_by_owner(self, owner, limit=200, include_all=False, kind=None) -> list[dict]
    def list_for_owners(self, owners, limit=500, include_all=False) -> list[dict]
    def list_profiles(self, owner, limit=10) -> list[dict]      # kind='profile' AND status='active'
    def find_by_query(self, owner, query, limit=20) -> list[dict]
    def count_by_owner(self, owner) -> int
    def count_active_by_owner(self, owner) -> int

    # ---- 状态 / 更正 ----
    def set_status(self, mid, status, owner=None, reason="") -> bool   # 写 audit
    def deny(self, owner, target, reason="user_deny") -> int
    def confirm(self, owner, target) -> int            # confidence=MIN(+0.2,1), confirmed=1
    def supersede(self, owner, target, reason="supersede") -> int
    def correct(self, owner, old_target, new_content, *, confidence=0.85,
                source_user="") -> str | None          # 旧置 superseded + 新写，写 audit

    # ---- 删除 ----
    def delete_fact(self, mid, owner=None) -> bool
    def delete_by_query(self, owner, query) -> int
    def clear(self, owner) -> int
    def enforce_limit(self, owner, max_per_owner) -> int   # score=importance/(1+age/7)，升序物理删

    # ---- owner 重置线 ----
    def set_reset(self, owner) -> None
    def get_reset(self, owner) -> int

    # ---- episodes ----
    def add_episode(self, session_id, owner, content, *, role="user",
                    user_id="", message_id="", ts=None) -> str
    def list_episodes(self, session_id, *, limit=40, unconsolidated_only=True) -> list[dict]
    def mark_episodes_consolidated(self, ids: list[str]) -> int
    def prune_episodes(self, *, keep_days=7, max_per_session=200) -> int

    # ---- embedding ----
    def set_embedding(self, mid, vector, *, model="", dim=None) -> None
    def missing_embeddings(self, limit=100) -> list[dict]
    def touch_used(self, ids: list[str]) -> None   # last_used_at/hit_count

    # ---- 审计 / 统计 ----
    def audit(self, action, *, owner="", ref_id="", user_id="", summary="",
              source="", source_task="") -> None
    def recent_audit(self, owner=None, limit=200) -> list[dict]
    def stats(self) -> dict   # {"memories": n, "owners": n, "events": n, "profiles": n, "episodes": n}
```

兼容要求：旧调用方（若还有）只用上面已列的方法；`upsert_fact` 返回值从 `str|None`
变为 `WriteOutcome`，本插件内部全部改动到位，**不要**保留旧式返回。

### 5.3 `src/embeddings.py`（T2 实现）

```python
class EmbeddingClient:
    def __init__(self, *, runtime=None, services=None, bot_id="", model_id="") -> None
    async def available(self) -> bool
    async def embed(self, texts: list[str]) -> list[list[float]]  # 失败返回 []，内部 batch≤16
    async def embed_one(self, text: str) -> list[float] | None
    def describe(self) -> dict   # {"enabled": bool, "model": str, "dim": int, "reason": str}
```

- 解析模型：`model_id` 优先；否则 `services.provider_models` 里 `provider_type=="embedding"`
  且 `enabled` 的第一条；再否则 `runtime.config_service.list_provider_models()`。
- 解析配置：`runtime.provider_runtime_manager.resolve_provider_config(model_id)`，
  然后 `app.agent.providers.get_provider(config)`（`provider_type=embedding` → `OpenAIEmbeddingProvider`）。
- 无 runtime/services/模型/密钥时 `available()` 返回 False，`embed()` 返回 `[]`，
  只 `logger.debug` 留痕（不刷屏、不抛异常）。
- 实例内 LRU 缓存：`{text: vector}` 最多 256 条；相同 query 不重复请求。

### 5.4 `src/extract.py`（T2 实现）

```python
EXTRACT_SYSTEM_PROMPT: str
def render_extract_prompt(speaker_label: str, lines: list[str],
                          existing: list[dict], *, max_chars=6000) -> str
def parse_ops(text: str) -> list[dict]
    # 返回 op dict：{"op": "ADD"|"UPDATE"|"SUPERSEDE"|"NOOP",
    #              "content": str, "importance": float, "confidence": float,
    #              "target_id": str, "subject": "self"|"group"|uid, "reason": str}
    # - 容忍 ```json 围栏 / 前后废话；不是 JSON 时再尝试逐行 <importance> [语气] 内容
    # - content 经 sanitize_content；非法 op 丢弃
def sanitize_content(text: str) -> str
    # 去空白/引号；≤200 字；命中注入特征（"忽略以上/忽略之前/你现在是/system prompt/
    # 以管理员身份/必须执行" 等，大小写不敏感）→ 返回 ""（丢弃）。
def _confidence_from_tag(tag: str) -> float   # [很确定]0.8 [好像]0.6 [不确定]0.4，默认 0.6
```

抽取 prompt 要求（JSON array，一条一行不重要）：
- 只记录用户**明确说过**的内容或明确表达的偏好；禁止推断（动机、未来计划、他人评价）；
- `subject`：`self`=说话人本人；`group`=群公共约定；其他=被提到的群成员 QQ 号（只在群聊）；
- `op=UPDATE` 时必须给 `target_id`（来自 existing 列表）；拿不准就 `ADD`；
- 同一事实反复出现 → `ADD` 一次即可，框架按内容去重；
- 无值得记的内容 → `[]`；
- `render_extract_prompt(..., today="YYYY-MM-DD")` 会把当前日期写进 prompt，要求把
  「昨天/下周/下个月」换算成绝对日期（时间语义不能靠模型猜）。

### 5.5 `src/consolidate.py`（T2 实现）

```python
class Consolidator:
    def __init__(self, module, manager) -> None
    async def run(self, session_id: str, *, is_group: bool, force=False,
                  source="chat") -> int          # 返回写入条数；内部限频 + spawn 后台
    def should_run(self, session_id: str) -> bool
    def status(self) -> dict                     # {"last_run": {sid: ts}, "running": bool}
    def stop(self) -> None
```

流程：
1. 门：`manager.enabled()` + `manager.scene_enabled(session_id)` + `memory_extract_enable`；
   限频：同 session 距上次 `>= memory_extract_interval_min * 60`，`force=True` 跳过；
2. 取 `store.list_episodes(session_id, limit=memory_consolidate_batch)`（未巩固）；
   不足 `memory_consolidate_min_chars`（默认 30 字）直接返回 0；
3. 取候选事实：`manager.candidates_for_extraction(session_id, is_group, text)`（见 manager）；
4. `prompt = render_extract_prompt(...)`；`resp = await manager.llm_chat([...])`；
   `resp.ok` 为假 → 记 warning、返回 0（**不** mark 已巩固，下次再试）；
5. `ops = parse_ops(resp.text)[:6]`（单组每轮最多 6 条，防一次刷屏）；
   逐条 `manager.apply_op(op, session_id=..., is_group=...)`；
   成功后 `mark_episodes_consolidated(ids)` + audit("consolidate")；
   单条失败只 warning，不中断其余；
6. 结束 `prune_episodes()`（保留策略见 store）。

### 5.6 `src/recall.py`（T3 实现）

```python
MEMORY_BLOCK_TITLE = "### 供参考（可能不准确）的历史记忆："
def clean_query(text) -> str               # 剥「你还记得吗/之前说过/帮我查查」元话语
def rank(store, *, owners, mention_owners=None, query="", query_embedding=None,
         limit=8, max_chars=1200, require_match=True, min_confidence=0.5,
         max_age_days=0.0, owner_resets=None, keep_saved_before_reset=True,
         now=None) -> list[dict]
def render_block(hits, *, title=MEMORY_BLOCK_TITLE, hedge=True,
                 annotate_age=False, now=None) -> str
def render_profile(profiles) -> str        # "### 关于你的长期印象：" + 列表；空→""
def cosine(a, b) -> float                  # 转调 vector.cosine（兼容）
```

打分（**固定公式**，便于回归）：

```
kw  = 0.6 * token_hits/len(tokens) + 0.4 * gram_hits/max(1,len(grams))   # 无 query → 0
vec = max(0.0, cosine(query_embedding, row_embedding)) if 两者都有 else None
rec = 1 / (1 + age_days/30)
freq = min(1.0, log1p(hit_count)/5)                 # 使用频率（LangMem：relevant = sim + importance + recency/frequency）
imp = clamp(importance, 0, 1); conf = clamp(confidence, 0, 1)
if vec is not None:  score = .30*vec + .35*kw + .15*imp + .10*rec + .05*conf + .05*freq
else:                score = .65*kw + .15*imp + .10*rec + .05*conf + .05*freq
if row.owner in mention_set: score += .25
```

> 两段式召回（延迟优化）：注入路径先跑词法；只有结果为空 / top1 归一化分 < 0.35
> 才补一次 embedding 再精排（工具显式检索始终走混合）。查询向量有 LRU 缓存。
>
> 查询清洗：`clean_query()` 用规则剥掉「你还记得吗/之前说过/帮我查查」等元话语，
> 比每轮让 LLM 改写便宜（0ms），实体词不动。
>
> 多样性：候选 ≥8 且多于 limit 时做一次 MMR 重排（λ=0.7；有向量用余弦，无向量用
> 字符 2-gram Jaccard），避免同一件事的多条近义记忆占满预算。
>
> 时间提示：`render_block(annotate_age=True)` 给 ≥1 天的记忆追加「（N 天前）」，
> 由配置 `memory_inject_recency` 控制（默认开），帮模型判断信息是否过时。

过滤（mention 豁免）：`status='active'`、`expires_at` 过期、`max_age_days` 超龄
（confirmed=1 豁免）、`min_confidence`、重置线（`updated_at < last_reset_at` 且非
「已保存型」`confirmed=1 or source in (tool,deterministic,correct)`）。
`require_match=True` 且 query 非空时未命中（token/gram/mention 全无）不返回。

预算：按 score 降序；每条 content 渲染前截断到 `per_item = max(60, max_chars // max(1,limit))`
字；累计长度超 `max_chars` 停止；近似重复（去空白后相同或相似度 ≥0.95）只留高分一条。
mention 定向召回的 owner 并入候选集（`list_for_owners` 一次取回）。

### 5.7 `src/tool.py`（T3 实现）

保留 5 个工具名（框架 `app/agent/tools/policy.py` 已登记，勿改名）：

| 工具 | 参数 | 行为 |
|---|---|---|
| `memory_save` | content(必填), scope(session/group_public/user), importance | `manager.save_fact`；scenario/权限检查；`group_public` 仅群聊且要求 `event.is_admin`（无 event 的主动/定时路径放行） |
| `memory_recall` | query(必填), limit(1~20) | `manager.search_async`；返回渲染块或「未找到相关记忆」 |
| `memory_delete` | id/query（至少一个） | 只删本人层；返回条数 |
| `memory_correct` | old(必填), content(必填) | `manager.correct_own` |
| `memory_deny` | id/query（至少一个） | `manager.deny_own`（下架不删） |

- `build_memory_tools(runtime, session_id, user_id, is_private)` 签名不变；
  错误字符串以 `error: ` 开头，成功 `success: `；⾼危/越权只回错误不抛。
- `_resolve_memory`：优先 `runtime.memory_service`（旧 `runtime.memory` 仅测试替身兼容）。

### 5.8 `src/commands.py`（T3 实现）

```python
def render_group_help(entries, group="memory", prefix="/") -> str   # 直接复用框架的 render_group_help
async def handle_memory_command(module, session_id, user_id, is_private, is_admin,
                                action, send) -> bool                 # 兼容旧测试/直调
```

子命令（`/memory`、`／memory`、`#llm memory` 三种前缀都认，由 main.py 的
`@command_group("memory", prefixes=("/", "／", "#llm "))` 分发；`handle_memory_command`
只做纯函数式解析，便于测试）：

`list [--all]` / `search <词>` / `forget <id|词>` / `clear` / `audit [owner]`（管理员）/
`export`（导出 JSON 文本，最多 100 条）/ `profile`（列出本会话可见画像）/ `remember <内容>`
（本人层写入一条，等价确定性保存）。无子命令 → 帮助清单。

## 6. manager / service / main（Lead 实现）

`src/manager.py::MemoryManager` 对外：

```python
enabled() / scene_enabled(session_id)
scope_owners(session_id, user_id) / own_owner(session_id, user_id)
async recall_block_async(session_id, user_id="", query="", *, bot=None,
                         limit=None, max_chars=None, for_tool=False) -> str
async search_async(session_id, user_id, query, *, limit=8) -> str
save_fact(content, owner, *, importance=.5, source="tool", source_user="", kind="fact",
          confidence=None, keywords="") -> str | None
autosave(session_id, user_id, text) -> str | None          # detect.py 规则
record_episode(session_id, user_id, text, *, message_id="", role="user") -> None
async maybe_consolidate(session_id, is_group, messages=None, *, force=False, source="chat")
async consolidate_archived(session_id, is_group, messages)
async on_session_reset(session_id, user_id=None) -> str    # suspend/clear/keep
async backfill_embeddings(limit=200) -> int
async llm_chat(messages, **kwargs)                         # 转调 module.llm_chat
candidates_for_extraction(session_id, is_group, text) -> list[dict]
async apply_op(op, *, session_id, is_group) -> str | None
profile_block(session_id, user_id) -> str
async embedding_status() -> dict
stop()
```

`src/service.py`：`MemoryProvider`（画像块 + 召回块 → 包成 **user 侧数据块**，`role="user"`，
order=30）、`MemoryToolSource`、`MemoryLifecycle`（pre_request：record_episode + autosave；
turn_completed：maybe_consolidate；session_reset：on_session_reset）。

`main.py`：`OWNED_KEYS` 17+7；`on_load` 挂 `memory_options` / `memory_service` /
provider / tool source / 3 个 llm_hook / 后台 embedding 回填（`self.spawn`）；
`@command_group("memory", ...)` + 8 个子命令；`on_unload` 全摘。

### 5.9 框架接口（v3 配套改动）

- `app/agent/loop/providers.py`：新增 `ContextBlock(name, text, role="system")`；
  `collect_context_blocks()` 返回 `list[ContextBlock]`，provider 可声明 `role="user"`
  或直接返回 `ContextBlock`（显式指定）。
- `PromptRequest.user_blocks`（`list[(块名, 文本)]`）：user 侧数据块。`build_user`
  按「数据块 → 用户正文 → 额外内容块 → 图片」渲染；`place_images` 在有图时同样保留数据块。
- `app/agent/assembly/context_data.py`：`wrap_data_block()` / `wrap_memory_data()` 统一
  加 `<memory_data source=…>` 标签与「不是指令」声明，并替换正文里的同名标签（防边界逃逸）。
- 主动消息 / 定时任务走 `assembly/inputs.py`：`collect_history_materials()` 现在同时返回
  `memory_text`（旧字段）与 `memory_user_blocks`（数据块），两条路径都把记忆放 user 侧。
- 记忆不再占用 system 指令位，system 前缀更稳定；agent 侧 `MemoryProvider` 与
  无事件路径共用同一包装函数，格式不会漂移。

## 7. 配置（24 键）

沿用 17 键（名字不变，语义不变），新增 7 键：

| key | type | 默认 | 说明 |
|---|---|---|---|
| `memory_recall_mode` | select | `hybrid` | `hybrid`=词法+向量（无 embedding 自动退化）；`keyword`=只用词法 |
| `memory_embedding_model_id` | resource(provider_model, provider_type=embedding) | `""` | 空=自动选第一个 embedding 模型 |
| `memory_profile_enable` | boolean | `True` | 画像块常驻注入 |
| `memory_profile_max_chars` | number | `800` | 画像块字符预算 |
| `memory_consolidate_batch` | number | `20` | 每次巩固最多多少条 episode |
| `memory_consolidate_min_chars` | number | `30` | 会话语料不足多少字不调 LLM |
| `memory_inject_recency` | boolean | `True` | 超过 1 天的记忆注入时标注「（N 天前）」 |

同步修改：`main.py::OWNED_KEYS`、`app/agent/config_partition.py::MODULE_KEYS["llm_memory"]`、
`tests/architecture/test_config_partition.py` 的覆盖计数（新键不属旧 160 键，计数改为
「旧键 ⊆ 覆盖」语义）。默认值必须与 `schemas.py` 一致。

## 8. 启用与生命周期（修断点）

- `enabled()` **只看 `memory_enable`**（删除 `experimental_long_term_memory` 检查）。
- `#llm new` / `#llm stop` / 会话归档：框架改读 `runtime.memory_service`（不再读
  `runtime.memory`），并统一走 `session_reset` 钩子 / `consolidate_archived`。
- `#llm memory ...`：由模块 `@command_group` 接管（前缀含 `#llm `），框架里那段
  「长期记忆未启用」兜底保留（模块被禁用时的正确提示）；`#llm help` 文案保留。

## 9. 错误处理与降级

- 所有 DB 访问由 manager 用 `asyncio.to_thread` 包装；store 本身同步。
- 召回/注入失败 → 返回空串 + debug 留痕，绝不阻断本轮。
- 蒸馏失败 → warning + 不 mark episodes（下次重试）；单条 op 失败只跳过该条。
- embedding 失败 → 当轮退化为词法召回；不回写坏向量；后台回填失败只 debug。
- 任何 `except Exception` 必须留痕（`tests/architecture/test_no_silent_except.py`）。

## 10. 迁移

- 旧库：`ALTER TABLE memories ADD COLUMN valid_from/invalid_at/last_used_at/embedding_model/
  embedding_dim/meta`（存在则跳过）；`memory_events` 加 `ref_id`；建 episodes 表。
- 旧数据语义：`status='active'` 保持；`superseded/negative` 保持；`valid_from=created_at`。
- 旧配置：17 键同名，无需迁移；`memory_on_reset` 旧的 `archive` 值按 `keep` 处理并
  在 status 里标注（不报错）。
- embedding 回填：`on_load` 后 `spawn` 一次性后台任务，只处理 `active` 且
  `embedding IS NULL` 的行，每批 50，失败即停（下次启动再试）。

## 11. 测试（契约，≤每功能 3 例）

- `tests/plugins/memory/test_memory_store.py`：旧库迁移、owner 隔离、去重/合并、
  否定不翻转、limit/reset、episode 缓冲。
- `tests/plugins/memory/test_memory_recall.py`：过滤（低置信/超龄/重置线）、
  混合打分排序、预算截断、hedge 渲染。
- `tests/plugins/memory/test_memory_consolidate.py`：JSON 解析、注入过滤、
  否定 op → supersede、失败不 mark。 
- `tests/plugins/memory/test_memory_tools_commands.py`：5 工具写入/检索/删除、
  `/memory list|forget|clear|audit` 回显。
- `tests/plugins/test_llm_memory_knowledge.py` 重写为 `test_memory_module.py`：
  on_load 接线（memory_options/memory_service/provider/tool_source/3 hooks）、
  新配置键、卸载还原、无 `experimental` 门。
- 集成回归：`memory_service` 被归档/reset 路径使用（fake runtime + fake module）。

## 12. 提交拆分

1. `feat(memory)!: new storage schema with episodes and temporal validity`（store/vector）
2. `feat(memory): optional embedding provider and hybrid recall`（embeddings/recall）
3. `feat(memory): LLM consolidation with memory ops`（extract/consolidate）
4. `feat(memory): wire manager, tools, commands and lifecycle`（manager/service/main/tool/commands）
5. `feat(memory): add embedding/profile config keys and fix enablement`（schemas/config/framework）
6. `test(memory): rewrite contract tests for memory v3`
7. `docs(memory): document memory v3`（memory-design.md + 本文 + CHANGELOG）
