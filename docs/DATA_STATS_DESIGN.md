# 数据统计设计（v2）

> 状态：**方案讨论稿**（待细化后施工）
> 范围：**token 用量** + **消息/事件收发量**，三类同级
> 不做：金额/花费

v2 相对 v1 的变化：
1. **记账点从 `turn.run_turn` 下移到 provider 调用层**（因为模块有多条绕过流水线的 LLM 路径，见 §1）
2. `source` 粒度 = **模块名**
3. `msg_in` 拆成**两个维度**：收到（全部）与需响应（触发）
4. 会话展示**全名**（名字 + group/private + QQ）
5. 数据**永久保留**，提供手动清理

---

## 0. 两个事实问题

### 0.1 上游是否返回本次消耗？

**会，在响应体里；流式必须显式索要。**

| 上游 | 非流式 | 流式 | 备注 |
|---|---|---|---|
| DeepSeek | ✅ | ✅ 需 `stream_options.include_usage=true` | 最后一帧带 usage |
| OpenAI 系 | ✅ | ✅ 同上 | 同上 |
| Gemini | ✅ `usageMetadata` | ✅ 只在最后 chunk | 中途读不到 |
| Anthropic | ✅ | ✅ | 口径同 OpenAI |
| vLLM / ollama / 部分中转 | ⚠️ 多数不回 | ❌ | 只能估算 |

已实现：`app/agent/providers/openai_compat.py:442`（含"上游不认就降级重试"）。

### 0.2 上游是否返回缓存命中数？——**已实测，会返回**

用仓库里现成的两个 provider 配置（`provider_presets` 表）真实请求验证：

**① DeepSeek 官方（`https://api.deepseek.com`，`deepseek-v4-flash`）**

```json
// 首次（无缓存）
{"prompt_tokens":653,"completion_tokens":8,"total_tokens":661,
 "prompt_tokens_details":{"cached_tokens":0},
 "completion_tokens_details":{"reasoning_tokens":8},
 "prompt_cache_hit_tokens":0,"prompt_cache_miss_tokens":653}

// 第二次同样 prompt（命中）
{"prompt_tokens":653,"completion_tokens":2,
 "prompt_tokens_details":{"cached_tokens":512},
 "prompt_cache_hit_tokens":512,"prompt_cache_miss_tokens":141}
```

**② opencode（`https://opencode.ai/zen/go/v1`，同一模型）**

```json
// 首次
{"prompt_tokens":653,"completion_tokens":8,
 "prompt_tokens_details":{"cached_tokens":0,"cache_write_tokens":null}}
// 第二次（命中）
{"prompt_tokens":653,"completion_tokens":8,
 "prompt_tokens_details":{"cached_tokens":640,"cache_write_tokens":null}}
```

**③ 流式（`stream_options.include_usage=true`）**：两家**末帧都带完整 usage**（含缓存命中）。

#### 结论

| 事实 | 结果 |
|---|---|
| 是否返回 usage | ✅ 两家**都返回**（流式也有，需 `include_usage`） |
| 是否返回缓存命中 | ✅ 两家**都返回** |
| 字段差异 | DeepSeek 直接给 `prompt_cache_hit_tokens` + `prompt_cache_miss_tokens`；opencode 只给 `prompt_tokens_details.cached_tokens`（**miss 要自己减**） |
| 缓存粒度不同 | 同一 prompt：DeepSeek 命中 512、opencode 命中 640 → **各家缓存策略不一样，别互相比较** |
| 额外字段 | 两家都有 `completion_tokens_details.reasoning_tokens` → **值得单独统计** |

用现有 `cache_hit_miss()` 跑这 4 份真实 payload：

| 场景 | hit | miss | 命中率 |
|---|---|---|---|
| DeepSeek 首次 | 0 | 653 | 0.0% |
| DeepSeek 二次 | 512 | 141 | 78.4% |
| opencode 首次 | 0 | 653 | 0.0% |
| opencode 二次 | 640 | 13 | 98.0% |
| 模拟"不返回缓存字段的中转" | NULL | NULL | 显示 `—` |

**所以：不需要 unknown 兜底，主流路径都能拿到缓存数据。**
只有第三方中转/自建时才可能是 `NULL`，因此 `cache_hit` / `cache_miss` 仍**必须可空**（NULL = 上游没报 ≠ 0）。

---

## 1. 现状核查：模块调 LLM 有没有统一总线？

**结论：没有。目前有 4 条路径，只有 1 条走流水线。**

| # | 路径 | 入口 | 过流水线 | 有 usage | 若记账放 `turn` 会漏 |
|---|---|---|---|---|---|
| **A** | 模块发起完整一轮 | `BaseModule.llm_turn()` → `turn.run_turn` | ✅ | ✅ `usage_sink` 累计 | 不漏 |
| **B** | 模块直接对话/流式 | `BaseModule.llm_chat()` / `llm_stream()` → `AgentClient.chat/stream` | ❌ | ✅ `LLMResponse.usage` | **漏** |
| **C** | 模块自建调用 | `resolve_invoker(runtime).chat/stream(...)` | ❌ | ✅ | **漏** |
| **D** | 模块直连适配器 | `get_provider(cfg).chat(...)` | ❌ | ✅ | **漏** |

**实际在用的调用点**：

```
C  app/plugins/llm_proactive/manager.py:357   resolve_invoker(...).chat(...)
C  app/plugins/llm_proactive/stream.py:115    resolve_invoker(...).stream(...)
C  app/plugins/llm_schedule/scheduler.py:582  resolve_invoker(...).chat(...)
D  app/plugins/llm_schedule/scheduler.py:596  get_provider(...)  (legacy 兼容分支)
D  app/plugins/llm_schedule/scheduler.py:607  provider.chat(...)
D  app/plugins/llm_knowledge/manager.py:52    get_provider(config)  (embedding)
```

**这些恰恰是 token 大头**（主动消息 / 定时任务 / 知识库 embedding）。
所以记账点**不能**放 `turn.run_turn`，否则统计会漏掉最费钱的部分。

### 1.1 唯一真正的收口点

`get_provider(config)`（`app/agent/providers/__init__.py`）是**所有** provider 实例的工厂：

```
BaseModule.llm_turn     → run_turn → invoker.chat ─┐
BaseModule.llm_chat     → client.chat ─────────────┤
模块 resolve_invoker()  → invoker.chat ─────────────┼→ chat_with_fallback → get_provider().chat()
模块 get_provider()     → provider.chat ───────────┘
```

**所以：给 `get_provider()` 返回的实例套一层计量装饰器，一次收住全部四条路径。**

### 1.2 归属怎么来（谁花的 token）

provider 层拿不到 `session_id` / `source`，用 **contextvar** 补齐：

```python
# app/agent/usage/context.py
CURRENT = contextvars.ContextVar("usage_ctx", default=None)

@contextmanager
def usage_scope(*, bot_id, source, session_id="", group_id=""):
    token = CURRENT.set({...})
    try:
        yield
    finally:
        CURRENT.reset(token)
```

| 调用方 | 谁 set context | 结果 |
|---|---|---|
| A `llm_turn` / `run_turn` | `run_turn` 内部 | `source` = 模块名 |
| B `llm_chat` / `llm_stream` | `BaseModule` 包装 | `source` = 模块名 |
| C/D 模块自建 | **没人 set** | `source = "unknown"` |

`source="unknown"` 本身是有用信号：**它说明有模块绕过了总线**。
（建议顺手把那 6 个直连点改成 `llm_chat` / `llm_stream`，归属就全准了。）

**关键保证：总量永不漏** —— 即使 `source` 未知，行照样写，汇总照样对。

---

## 2. 表结构（一张表，三类同级）

```sql
CREATE TABLE stat_event (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 时间
    ts          INTEGER NOT NULL,          -- UTC 秒（真值）
    day_local   TEXT    NOT NULL,          -- '2026-10-22'（本地日，进索引）

    -- 类型（三类同级）
    kind        TEXT    NOT NULL,          -- llm | msg_in | msg_out

    -- 维度 facets
    bot_id      TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',  -- 模块名：chat / llm_proactive / llm_schedule / ...
                                           --   框架内路径用 'chat'；绕过总线的为 'unknown'
    api         TEXT NOT NULL DEFAULT '',  -- provider（deepseek / openai / gemini …）
    model       TEXT NOT NULL DEFAULT '',  -- 实际服务模型（SERVED_MODEL_KEY）
    session_id  TEXT NOT NULL DEFAULT '',  -- group_123 / private_456
    session_label TEXT NOT NULL DEFAULT '',-- 展示快照：'名字前5字…' + group/private + id
    group_id    TEXT NOT NULL DEFAULT '',
    user_id     TEXT NOT NULL DEFAULT '',
    event_type  TEXT NOT NULL DEFAULT '',  -- message_group / message_private / notice / request
    direction   TEXT NOT NULL DEFAULT '',  -- in | out
    outcome     TEXT NOT NULL DEFAULT '',  -- msg_in 专用：ignored | command | module | llm
                                           --   指令响应 / 模块普通响应 / LLM 响应 / 纯围观

    -- 度量（全部可 SUM）
    -- llm：
    requests      INTEGER NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit     INTEGER,                 -- NULL = 上游未报（≠ 0）
    cache_miss    INTEGER,
    tokens_total  INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,  -- 思考 token（reasoning_tokens）
    -- msg_in / msg_out：
    messages      INTEGER NOT NULL DEFAULT 0,   -- 收到/发出的消息条数
    segments      INTEGER NOT NULL DEFAULT 0,   -- 消息段数
    bytes         INTEGER NOT NULL DEFAULT 0,   -- 文本字节数

    -- 状态
    ok          INTEGER NOT NULL DEFAULT 1,
    estimated   INTEGER NOT NULL DEFAULT 0,  -- 1 = 上游没回 usage，值按字数估
    err_kind    TEXT    NOT NULL DEFAULT '',
    latency_ms  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_stat_day_kind       ON stat_event(day_local, kind);
CREATE INDEX idx_stat_day_kind_model ON stat_event(day_local, kind, model);
CREATE INDEX idx_stat_day_kind_src   ON stat_event(day_local, kind, source);
CREATE INDEX idx_stat_day_kind_api   ON stat_event(day_local, kind, api);
CREATE INDEX idx_stat_day_session    ON stat_event(day_local, session_id);
CREATE INDEX idx_stat_ts             ON stat_event(ts);
```

### 2.1 `msg_in`：一行一条入站消息，`outcome` 分四种结局

| `outcome` | 含义 | 例子 |
|---|---|---|
| `ignored` | 收到但**不需要响应** | 群里的围观聊天（没 @、没关键词） |
| `command` | **指令响应** | `#llm schedule list`、`#help` |
| `module` | **模块普通响应**（不走 LLM） | bilibili/douyin 解析、签到、撤回通知 |
| `llm` | **LLM 响应**（进入流水线并回复） | 正常对话 |

于是这四种视角都是同一个筛选下的纯 SQL：

```sql
SUM(messages)                                            -- 收到总数
SUM(CASE WHEN outcome <> 'ignored' THEN messages END)    -- 需响应总量
SUM(CASE WHEN outcome = 'command'  THEN messages END)    -- 指令响应
SUM(CASE WHEN outcome = 'module'   THEN messages END)    -- 模块普通响应
SUM(CASE WHEN outcome = 'llm'      THEN messages END)    -- LLM 响应
```

不需要第二张表、不需要第二次请求，`breakdown=outcome` 还能直接画堆叠饼图。

**写入时机**：在触发/路由判定**之后**一次成型（此时结局已知），一次 INSERT。

### 2.2 会话展示用全名（按你的要求）

写入时存快照 `session_label`，格式（按你的要求）：

```
{名字前 5 字}{超过 5 字则 '…'}{group|private}{id}
```

例子：

```
才羽 ミド…group466052056        ← 群聊（名字取群名）
小明…private1901691195          ← 私聊（名字取昵称）
测试群group466052056            ← 名字不超过 5 字，不省略
```

实现：

```python
def session_label(name: str, kind: str, ident: Any) -> str:
    name = str(name or "").strip()
    short = name[:5] + ("…" if len(name) > 5 else "")
    return f"{short}{kind}{ident}"        # kind = 'group' | 'private'
```

- **写入时定快照**，历史数据保持当时的名字（否则群改名后历史全变）
- 查询时若 `session_label` 为空（老数据），用 `session_id` 兜底
- 前端直接显示，不需要再查一次

### 2.3 `cache_hit` 的 NULL 语义

| 情况 | 显示 |
|---|---|
| `cache_hit IS NULL` | `—`（该上游不回报缓存） |
| `cache_hit = 0` | `0%`（缓存未生效，值得查） |
| `cache_hit > 0` | `命中率 = hit / (hit + miss)` |

---

## 3. 记账点（一个装饰器，四条路径全覆盖）

### 3.1 计量装饰器

```python
# app/agent/usage/metered.py
class MeteredProvider:
    """包一层：每次 chat/stream 结束都记一条 stat_event。"""

    def __init__(self, inner, recorder):
        self._inner, self._rec = inner, recorder

    async def chat(self, messages, **kw):
        started = time.monotonic()
        try:
            resp = await self._inner.chat(messages, **kw)
        except Exception as e:
            self._rec.record_llm_failure(...)      # 失败也记
            raise
        self._rec.record_llm(resp, latency_ms=...)  # 从 resp.usage 取
        return resp

    async def stream(self, messages, **kw):
        usage, started = {}, time.monotonic()
        async for ev in self._inner.stream(messages, **kw):
            if getattr(ev, "usage", None):
                merge_usage(usage, ev.usage)         # 流式：末帧才有
            yield ev
        self._rec.record_llm_stream(usage, latency_ms=...)
```

挂载点（一处）：

```python
# app/agent/providers/__init__.py::get_provider()
instance = cls(config)
return wrap_metered(instance)   # ← 唯一改动
```

### 3.2 为什么这样最稳

| 好处 | 说明 |
|---|---|
| **不漏** | 四条路径全部经过 `get_provider()` |
| **不双计** | 一次 provider 调用 = 一行；`usage_sink` 只做 turn 内部记账，不参与统计 |
| **更真实** | 回退/重试是**多次**上游调用，就记多行（真实消耗），比"一轮一行"更准 |
| **失败可见** | 异常路径也写一行 `ok=0`，能看出"某模型一直 429" |

### 3.3 `msg_in` / `msg_out` 记账

| kind | 位置 | 说明 |
|---|---|---|
| `msg_in` | 事件分发入口（inbound 节点）+ 触发判定之后 | `messages+1`；`triggered+1` 仅当进入流水线 |
| `msg_out` | `app/agent/delivery/channel.py::send_item` | 唯一发送出口，天然不漏 |

`msg_in` 需要**两处**写入（收到时 + 判定后补 `triggered`）？
→ 不：**在触发判定之后再写**，一次成型（`triggered` 已知）：
- 触发判定前收到 → 缓存在 job 上
- 判定后统一写一行（`messages=1, triggered=1|0`）

### 3.4 写入策略

- 内存攒批：**每 20 条 或 每 2 秒** flush
- 运行体关闭 / 进程退出**强制 flush**
- **禁止 fire-and-forget**（历史教训：`Cannot operate on a closed database`）
- 写失败只 `logger.debug`，不影响主流程

---

## 4. 筛选机制

### 4.1 参数（封闭白名单）

```http
GET /api/stats/query
  &kind=llm                 # llm | msg_in | msg_out | all
  &metric=tokens            # 见 §4.3
  &granularity=day          # minute | hour | day | week | month
  &from=2026-10-01&to=2026-10-22
  &breakdown=model          # none | model | api | source | session | bot | event
  &bot_id= &source= &api= &model= &session_id= &group_id= &user_id=
  &ok_only=1 &include_estimated=1
```

### 4.2 一份 SQL 覆盖所有组合

```sql
SELECT {BUCKET_EXPR}   AS bucket,
       {BREAKDOWN_COL} AS series,
       SUM(messages) AS messages, SUM(triggered) AS triggered,
       SUM(requests) AS requests, SUM(input_tokens) AS input_tokens,
       SUM(output_tokens) AS output_tokens,
       SUM(cache_hit) AS cache_hit, SUM(cache_miss) AS cache_miss,
       SUM(tokens_total) AS tokens_total, SUM(bytes) AS bytes,
       COUNT(*) AS events
FROM stat_event
WHERE ts >= :from_ts AND ts < :to_ts
  AND (:kind   IS NULL OR kind   = :kind)
  AND (:bot_id IS NULL OR bot_id = :bot_id)
  AND (:source IS NULL OR source = :source)
  AND (:api    IS NULL OR api    = :api)
  AND (:model  IS NULL OR model  = :model)
  AND (:group_id   IS NULL OR group_id   = :group_id)
  AND (:session_id IS NULL OR session_id = :session_id)
  AND (:user_id    IS NULL OR user_id    = :user_id)
  AND (:ok_only = 0 OR ok = 1)
  AND (:include_estimated = 1 OR estimated = 0)
GROUP BY bucket, series
ORDER BY bucket
```

三条铁律：

1. **不拼 SQL**：命名参数 + `(:x IS NULL OR col = :x)` 空值短路（覆盖 2¹⁰ 组合，防注入，复用执行计划）
2. **时间条件只用 `ts`**（整数比较走索引）
3. **facet 等值匹配**，不做模糊（要搜就用 `/api/stats/facets`）

### 4.3 度量表

| metric | 表达式 | 单位 | 适用 |
|---|---|---|---|
| `tokens` | `SUM(tokens_total)` | k/M | llm |
| `input` | `SUM(input_tokens)` | k/M | llm |
| `output` | `SUM(output_tokens)` | k/M | llm |
| `cache_hit` | `SUM(cache_hit)` | k/M | llm |
| `requests` | `SUM(requests)` | 次 | llm |
| `reasoning` | `SUM(reasoning_tokens)` | k/M | llm |
| `messages` | `SUM(messages)` | 条 | msg_in / msg_out |
| `triggered` | `SUM(CASE WHEN outcome<>'ignored' THEN messages END)` | 条 | msg_in |
| `by_outcome` | `SUM(CASE WHEN outcome=:o THEN messages END)` | 条 | msg_in |
| `bytes` | `SUM(bytes)` | KB/MB | msg_in / msg_out |
| `events` | `COUNT(*)` | 次 | 全部 |

### 4.4 时间桶（SQL 里算）

```python
BUCKET = {
    "minute": "strftime('%Y-%m-%dT%H:%M', ts, 'unixepoch', 'localtime')",
    "hour":   "strftime('%Y-%m-%dT%H',    ts, 'unixepoch', 'localtime')",
    "day":    "day_local",
    "week":   "strftime('%Y-W%W',         ts, 'unixepoch', 'localtime')",
    "month":  "strftime('%Y-%m',          ts, 'unixepoch', 'localtime')",
}
```

### 4.5 下拉选项由后端给

```http
GET /api/stats/facets?kind=llm&from=...&to=...&api=deepseek
→ {"bot_id":[…], "source":[{"value":"llm_proactive","label":"主动消息","events":120}],
   "api":[…], "model":[…], "session":[{"value":"group_466052056","label":"测试群(466052056)","events":120}]}
```

- 只返回该范围内**真的出现过**的值（带 `events` 计数，按用量降序）
- **联动**：带 `api=deepseek` 时 `model` 只列该 api 的模型
- `source` 用中文 label（模块名 → 显示名映射），值仍是模块名

### 4.6 粒度自动默认

| 范围 | 默认 |
|---|---|
| ≤ 2 天 | `hour` |
| ≤ 60 天 | `day` |
| ≤ 400 天 | `week` |
| 更大 | `month` |

### 4.7 空桶补零 + top-N 收敛

- **补零**：后端按粒度生成完整时间轴，缺的填 0（否则柱状图断档）
- **top-N**：堆叠序列超 6 个合并成 `other`（`meta.truncated` 说明合并了几个）
- **summary 单独算**：顶部概览不能用被截断的 buckets 求和

---

## 5. 接口契约

```http
GET /api/stats/query?kind=llm&metric=tokens&granularity=day&from=2026-10-01&to=2026-10-22&breakdown=model
```

```json
{
  "meta": {"kind":"llm","metric":"tokens","granularity":"day","breakdown":"model",
           "from":"2026-10-01","to":"2026-10-22","timezone":"Asia/Shanghai",
           "unit":"tokens","truncated":0,"series_order":["deepseek-chat"]},
  "buckets": [
    {"bucket":"2026-10-22",
     "series":{"deepseek-chat":{"tokens":1800,"input":900,"output":900,
                                "cache_hit":600,"cache_miss":300,"requests":4}},
     "total":{"tokens":1800,"requests":4}}
  ],
  "summary": {"tokens":160000,"input":120000,"output":40000,
              "cache_hit":80000,"cache_miss":40000,"cache_rate":0.667,
              "requests":812,"messages":0,"triggered":0,
              "fail_rate":0.012,"unknown_cache":3}
}
```

返回**原始整数**，`K/M` 换算由前端做。

手动清理（永久保留 + 自己清）：

```http
POST /api/stats/purge  {"before":"2026-01-01"}      # 删该日期之前
POST /api/stats/purge  {"bot_id":"...", "before":"..."}
```

分批删（每次 1000 行），避免长事务锁库。

---

## 6. 前端结构

```
┌ 筛选行 ───────────────────────────────────────────────────────────────┐
│ [类型▾ LLM|收消息|发消息] [度量▾] [时间范围▾] [粒度: 时|天|周|月]        │
│ [账号▾] [模块▾] [API▾] [模型▾] [会话▾]                                │
├ 概览卡 ───────────────────────────────────────────────────────────────┤
│ LLM：总 160K │ 输入 120K │ 输出 40K │ 缓存命中 66.7% │ 请求 812        │
│ 收消息：4,210 条 │ 需响应 380 条（9.0%）                                │
│ 发消息：900 条                                                        │
├ 主图：堆叠柱 ─────────────────────────────────────────────────────────┤
│ y = token（自动 K/M）    x = 日期      ▉deepseek-chat ▉gpt-4o ▉other   │
├ 明细表（同一份数据，不二次请求）──────────────────────────────────────┤
│ 日期 | 模型 | 请求 | 输入 | 输出 | 缓存命中 | 失败                      │
└───────────────────────────────────────────────────────────────────────┘
```

交互：

- 切「类型」→ 度量下拉联动（LLM 出 token 类；收发消息出条数/字节）
- 切「粒度」→ 重新请求
- 图例点击隐藏序列 → 纯前端
- 点柱子 → 下钻该时段明细（进阶）

---

## 7. 边界与坑

| 坑 | 处理 |
|---|---|
| **流式无 usage** | `estimated=1`，图上可筛掉 |
| **一轮多请求**（工具轮/回退/重试） | provider 级记账天然按"每次上游调用"记，更真实 |
| **`usage_sink` 与 provider 记账** | 互不干扰：`usage_sink` 只给 turn 内部用，统计只用 provider 层 |
| **缓存命中未知 vs 0** | NULL 语义（§2.3） |
| **时区** | 存 UTC 秒 + `day_local` 按配置时区算 |
| **群改名** | `session_label` 写时快照，历史不变 |
| **失败请求** | `ok=0` 也记，`tokens` 可能 0 |
| **重启丢数据** | 攒批 + 退出强制 flush |
| **保留** | 永久；提供 `POST /api/stats/purge` 手动清 |
| **绕过总线的模块** | `source=unknown` 可见；建议把那 6 个直连点改成 `llm_chat`/`llm_stream` |

---

## 8. 施工顺序

| 步 | 内容 | 验证 |
|---|---|---|
| S1 | 建表 + `StatRecorder`（攒批/强制 flush/清理） | 单测：批量写、关闭不丢、purge 分批 |
| S2 | `usage_scope` contextvar + `MeteredProvider` + 挂到 `get_provider()` | 单测：四条路径各记一条、失败也记、`source` 归属正确 |
| S3 | `msg_in` 记账（触发判定后一次成型，含 `triggered`） | 单测：收到 N 条 / 触发 M 条 |
| S4 | `msg_out` 记账（`send_item`） | 单测：segments/bytes |
| S5 | `/api/stats/query`（filter/bucket/breakdown/补零/top-N/summary） | 单测：代表组合、空桶补零、summary 独立 |
| S6 | `/api/stats/facets`（含联动、中文 label） | 单测：只返回出现过的值 |
| S7 | `/api/stats/purge` | 单测：分批删除 |
| S8 | 前端页面 | 手工验收 |
| S9（建议） | 把 6 处模块直连改成 `llm_chat` / `llm_stream` | 单测 + `source` 不再出现 unknown |

---

## 9. 施工状态：**S1–S9 全部完成**

| 步 | 内容 | 落地位置 | 提交 |
|---|---|---|---|
| S1 | `stat_event` 单表（29 列，5 个索引） | `infrastructure/persistence/database.py` | `236cd80` |
| S1 | `StatRecorder`（攒批 20 条 / 2 秒、退出强制 flush、分批 purge） | `agent/usage/recorder.py` | `236cd80` |
| S2 | `usage_scope` contextvar + `MeteredProvider` 挂 `get_provider()` | `agent/usage/{context,metered}.py`、`providers/__init__.py` | `236cd80` |
| S2 | bootstrap 装配 + `run_turn` / `llm_chat` / `llm_stream` 归属 | `bootstrap.py`、`loop/turn.py`、`plugin/base.py` | `ea20df7` |
| S3 | `msg_in` 记账（`outcome` 四分类，判定在路由之后一次成型） | `plugin/nodes/inbound.py` | `775d2a1` |
| S4 | `msg_out` 记账（`send_item` 唯一出口，失败也记） | `agent/delivery/channel.py` | `775d2a1` |
| S5 | `GET /api/stats/query`（9 facet + 桶 + 补零 + top-N + summary） | `agent/usage/query.py`、`webui/api/stats.py` | `6b6b696` |
| S6 | `GET /api/stats/facets`（只列出现过的值，自身筛选不约束自身） | 同上 | `6b6b696` |
| S7 | `POST /api/stats/purge`（分批删，无参数拒绝防误清） | 同上 | `6b6b696` |
| S8 | 前端「数据统计」页（筛选行 + 概览卡 + 堆叠柱 + 明细表，K/M 单位） | `dashboard/src/views/StatsPage.vue` + 路由 + 侧边栏 | `45c3b21` |
| S9 | 模块直连 provider 的 4 处补归属（`llm_proactive` ×2、`llm_schedule`、`llm_knowledge`） | 各模块 | `957bb9d` |

测试：`tests/agent/usage/*`（37 例）+ `tests/webui/test_stats_api.py`（17 例），全仓 **2102 passed**。

### 9.1 端到端验收（真实跑通）

```
LLM 用量（按模块）
  总 token     1983
  输入/输出    1959 / 24
  缓存命中     1536（未命中 423，命中率 78.4%）   ← 与实测 DeepSeek 返回值一致
  思考 token   24
  请求次数     3   未知缓存 0
  2026-10-22  chat             tokens=   661
  2026-10-22  llm_proactive    tokens=   661
  2026-10-22  llm_schedule     tokens=   661

入站消息（按结局）
  收到 4 条，需响应 3 条
  指令 1 / 模块 1 / LLM 1

出站消息
  发出 1 条，48 字节

facets（只列出现过的值）
  source       ['llm_schedule', 'llm_proactive', 'chat']
  api          ['deepseek']
  model        ['deepseek-v4-flash']
  session_id   ['private_1901691195', 'group_466052056', 'group_1080000001']
```

### 9.2 使用入口

- 页面：WebUI 侧边栏「**数据统计**」（`/#/stats`）
- 接口：`/api/stats/query` · `/api/stats/facets` · `/api/stats/purge`
- 清理：`POST /api/stats/purge?before_ts=...` 或 `?bot_id=...`（数据永久保留，手动清）

## 9. 已确认的决策

| 项 | 决策 |
|---|---|
| 金额 | ❌ 不做，只看 token |
| 表数量 | 一张 `stat_event`，三类统计同级 |
| `source` 粒度 | **模块名** |
| `msg_in` | 收到总量 + 需响应量，且需响应再分 **指令 / 模块 / LLM** 三种（`outcome`） |
| 会话展示 | **全名**：`名字前5字…group123456` / `名字前5字…private123456` |
| 保留 | **永久**，用户可手动清理 |
