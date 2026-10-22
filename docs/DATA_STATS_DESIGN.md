# 数据统计设计（v1）

> 状态：**方案讨论稿**（等需求细化后施工）
> 范围：**token 用量** + **消息/事件收发量**，三者同级
> 不做：金额/花费（用户明确不需要）

---

## 0. 先回答两个事实问题

### 0.1 上游是否返回"本次消耗"？

**会，在响应体里；流式必须显式索要。**

| 上游 | 非流式 | 流式 | 关键点 |
|---|---|---|---|
| DeepSeek | ✅ `usage.*` | ✅ 需 `stream_options.include_usage=true` | 最后一帧带 usage |
| OpenAI 系 | ✅ `usage.*` | ✅ 同上 | 同上 |
| Gemini | ✅ `usageMetadata` | ✅ 只在**最后**一个 chunk | 中途读不到 |
| Anthropic | ✅ `usage.*` | ✅ 同 OpenAI 口径 | — |
| 自建 vLLM/ollama | ⚠️ 多数不回 | ❌ | 只能估算 |
| 第三方中转 | ⚠️ 看代理是否透传 | ⚠️ | 常见"非流式有、流式没" |

本仓库已实现（`app/agent/providers/openai_compat.py:442`）：

```python
if "stream_options" not in payload:
    payload["stream_options"] = {"include_usage": True}
# 上游不认该参数（400/422）→ 去掉重发一次，本次 token 为空
```

### 0.2 上游是否返回"缓存命中数"？

**主流四家都返回，但字段名不同；没有统一保证。**

| 上游 | 缓存命中字段 | 缓存未命中字段 | 备注 |
|---|---|---|---|
| **DeepSeek** | `usage.prompt_cache_hit_tokens` | `usage.prompt_cache_miss_tokens` | 两个都直接给 |
| **OpenAI 系** | `usage.prompt_tokens_details.cached_tokens` | 需自己算：`prompt_tokens - cached_tokens` | 2024-10 后才有 |
| **Gemini** | `usageMetadata.cachedContentTokenCount` | 不单独给 | 命中 = 显式上下文缓存 |
| **Anthropic** | `usage.cache_read_input_tokens` | `cache_creation_input_tokens`（写入量，语义不同） | — |
| 第三方中转 / 自建 | ❌ 通常不返回 | ❌ | 只能说"未知" |

本仓库已统一口径（`app/agent/providers/base.py::cache_hit_miss()`），并且**坚持一个原则**：

> **不自己估算**：上游没报就是 `None`（未知），而不是 0。
> 因为"命中 0"和"不知道"在统计上是两件事——前者说明缓存未生效（该查），后者只是上游不给。

**这条原则决定表结构**：`cache_hit` / `cache_miss` 必须允许 `NULL`。

---

## 1. 总设计：一张表，三类统计同级

### 1.1 三类统计（同一层级）

| kind | 说明 | 主要度量 | 记录点 |
|---|---|---|---|
| `llm` | 一次 LLM 请求的 token 用量 | input/output/cache_hit/cache_miss/tokens | `turn.run_turn` 收尾（流式/非流式都经过） |
| `msg_in` | 收到的消息 / 事件 | messages / events / bytes | inbound 节点 / 事件分发入口 |
| `msg_out` | 发出的消息 | messages / segments / bytes | `delivery/channel.py::send_item` |

三者共用**同一组 facet**（账号 / 会话 / 群 / 来源 / 模型）与**同一套查询机制**，
所以前端只需要一个 `kind` 下拉 + 一个 `metric` 下拉就能切换视角。

### 1.2 为什么能放一张表

因为三类统计的**形状完全一致**：

```
(time, kind, [facet...], [measure...])
```

- facet 都是"可空的字符串标签"（用于 GROUP BY / WHERE）
- measure 都是"可 SUM 的整数"

差别只在"哪些 measure 列有值"——没用的列留 0 / NULL 即可。

---

## 2. 表结构

```sql
CREATE TABLE stat_event (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,

    -- ── 时间 ──
    ts          INTEGER NOT NULL,          -- UTC 秒（真值，一切时间聚合由它派生）
    day_local   TEXT    NOT NULL,          -- '2026-10-22'（本地日，最常用，进索引）

    -- ── 类型（三类同级）──
    kind        TEXT    NOT NULL,          -- llm | msg_in | msg_out

    -- ── 维度 facets（全部 NOT NULL DEFAULT ''）──
    bot_id      TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',  -- chat/proactive/schedule/module/notice/command
    api         TEXT NOT NULL DEFAULT '',  -- provider 或预设名（仅 llm）
    model       TEXT NOT NULL DEFAULT '',  -- 实际服务模型（仅 llm）
    session_id  TEXT NOT NULL DEFAULT '',  -- group_xxx / private_xxx
    group_id    TEXT NOT NULL DEFAULT '',  -- 群号（session 的子集）
    user_id     TEXT NOT NULL DEFAULT '',  -- 触发者 / 发送者
    event_type  TEXT NOT NULL DEFAULT '',  -- message_group / message_private / notice / request
    direction   TEXT NOT NULL DEFAULT '' , -- in | out（冗余：便于直接筛）

    -- ── 度量 measures（全部可 SUM）──
    -- llm 用：
    requests      INTEGER NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit     INTEGER,                 -- NULL = 上游未报（≠ 0）
    cache_miss    INTEGER,                 -- NULL = 上游未报（≠ 0）
    tokens_total  INTEGER NOT NULL DEFAULT 0,
    -- msg_in / msg_out 用：
    messages      INTEGER NOT NULL DEFAULT 0,
    segments      INTEGER NOT NULL DEFAULT 0,
    bytes         INTEGER NOT NULL DEFAULT 0,

    -- ── 状态 ──
    ok          INTEGER NOT NULL DEFAULT 1,
    estimated   INTEGER NOT NULL DEFAULT 0,  -- 1 = 上游没回 usage，值按字数估
    err_kind    TEXT    NOT NULL DEFAULT '',
    latency_ms  INTEGER NOT NULL DEFAULT 0
);

-- 索引：按"最常用切法"建，覆盖 90% 查询
CREATE INDEX idx_stat_day_kind        ON stat_event(day_local, kind);
CREATE INDEX idx_stat_day_kind_model  ON stat_event(day_local, kind, model);
CREATE INDEX idx_stat_day_kind_api    ON stat_event(day_local, kind, api);
CREATE INDEX idx_stat_day_kind_src    ON stat_event(day_local, kind, source);
CREATE INDEX idx_stat_day_session     ON stat_event(day_local, session_id);
CREATE INDEX idx_stat_ts              ON stat_event(ts);
```

### 2.1 关键决策：`cache_hit` 可空

```sql
cache_hit  INTEGER,   -- NULL = 未知（上游没报）
cache_miss INTEGER,
```

前端展示规则：

| 情况 | 显示 |
|---|---|
| `cache_hit IS NULL` | `—`（该模型/上游不回报） |
| `cache_hit = 0` | `0%（缓存未生效）` |
| `cache_hit > 0` | `命中率 = hit / (hit + miss)` |

如果混成 0，"上游不报"会被误读成"缓存失效"，会误导调优。

### 2.2 数据量估算

| 场景 | 每天行数 | 一年 |
|---|---|---|
| 单账号、中等活跃 | ~1,500 行（llm 800 + in 400 + out 300） | ~55 万 |

SQLite 单表 55 万行 + 上述索引，`GROUP BY day_local` 约 20~50ms，**足够**。
不做汇总表（符合"不分多个表"的要求）；若将来真的慢，再加覆盖索引，而不是拆表。

---

## 3. 筛选机制（重点）

### 3.1 原则：参数是**封闭白名单**，不是自由查询

前端只允许这三类参数：

```http
GET /api/stats/query

# ① 画什么（3 个）
&kind=llm                    # llm | msg_in | msg_out | all
&metric=tokens               # 见 §3.3 度量表
&granularity=day             # minute | hour | day | week | month

# ② 时间范围
&from=2026-10-01
&to=2026-10-22               # 含当天

# ③ 筛选 facet（7 个，不传 = 不筛）
&bot_id=3437542570
&source=chat
&api=deepseek
&model=deepseek-chat
&group_id=466052056
&session_id=
&user_id=
&ok_only=1
&include_estimated=1
```

### 3.2 SQL：一份语句覆盖所有组合

```sql
SELECT {BUCKET_EXPR}      AS bucket,
       {BREAKDOWN_COL}    AS series,
       SUM(requests)      AS requests,
       SUM(input_tokens)  AS input_tokens,
       SUM(output_tokens) AS output_tokens,
       SUM(cache_hit)     AS cache_hit,        -- SUM 忽略 NULL
       SUM(tokens_total)  AS tokens_total,
       SUM(messages)      AS messages,
       SUM(bytes)         AS bytes,
       COUNT(*)           AS events,
       MAX(ok = 0)        AS has_error
FROM stat_event
WHERE ts >= :from_ts
  AND ts <  :to_ts
  AND (:kind             IS NULL OR kind       = :kind)
  AND (:bot_id           IS NULL OR bot_id     = :bot_id)
  AND (:source           IS NULL OR source     = :source)
  AND (:api              IS NULL OR api        = :api)
  AND (:model            IS NULL OR model      = :model)
  AND (:group_id         IS NULL OR group_id   = :group_id)
  AND (:session_id       IS NULL OR session_id = :session_id)
  AND (:user_id          IS NULL OR user_id    = :user_id)
  AND (:ok_only = 0      OR ok = 1)
  AND (:include_estimated = 1 OR estimated = 0)
GROUP BY bucket, series
ORDER BY bucket
```

三条铁律：

1. **禁止拼接 SQL**：全部命名参数 + `(:x IS NULL OR col = :x)` 空值短路。
   一份语句覆盖 2¹⁰ = 1024 种筛选组合，天然防注入，SQLite 还能复用执行计划。
2. **时间条件永远用 `ts`**（整数比较，能走索引），`day_local` 只用于 GROUP BY / 索引前缀。
3. **facet 比较用等值**，不做模糊匹配（要模糊就用 `/api/stats/facets` 先搜出精确值）。

### 3.3 时间桶表达式（在 SQL 里算，不存 5 个桶列）

```python
BUCKET = {
    "minute": "strftime('%Y-%m-%dT%H:%M', ts, 'unixepoch', 'localtime')",
    "hour":   "strftime('%Y-%m-%dT%H',    ts, 'unixepoch', 'localtime')",
    "day":    "day_local",
    "week":   "strftime('%Y-W%W',         ts, 'unixepoch', 'localtime')",
    "month":  "strftime('%Y-%m',          ts, 'unixepoch', 'localtime')",
}

BREAKDOWN = {
    "none":    "''",
    "model":   "model",
    "api":     "api",
    "source":  "source",
    "bot":     "bot_id",
    "session": "session_id",
    "event":   "event_type",
}
```

### 3.4 度量表（`metric` → 表达式 + 单位）

| metric | 表达式 | 单位 | 适用 kind |
|---|---|---|---|
| `tokens` | `SUM(tokens_total)` | k / M | llm |
| `input` | `SUM(input_tokens)` | k / M | llm |
| `output` | `SUM(output_tokens)` | k / M | llm |
| `cache_hit` | `SUM(cache_hit)` | k / M | llm |
| `requests` | `SUM(requests)` | 次 | llm |
| `messages` | `SUM(messages)` | 条 | msg_in / msg_out |
| `bytes` | `SUM(bytes)` | KB / MB | msg_in / msg_out |
| `events` | `COUNT(*)` | 次 | 全部 |
| `cache_rate` | `SUM(cache_hit)/SUM(cache_hit+cache_miss)` | % | llm |

### 3.5 下拉选项：后端提供，前端不写死

```http
GET /api/stats/facets?kind=llm&from=...&to=...&api=deepseek
```

```json
{
  "bot_id":  [{"value":"3437542570","label":"才羽 ミドリ","events":812}],
  "api":     [{"value":"deepseek","label":"DeepSeek","events":700}],
  "model":   [{"value":"deepseek-chat","label":"deepseek-chat","events":700}],
  "source":  [{"value":"chat","label":"对话","events":600},
              {"value":"proactive","label":"主动消息","events":120}],
  "session": [{"value":"group_466052056","label":"…056","events":120}]
}
```

规则：

1. **只返回该时间范围内真的出现过的取值**——避免"选了永远空"的选项。
2. **联动**：请求带 `api=deepseek` 时，`model` 只列该 api 下的模型。
3. **带 `events` 计数**，前端按用量降序排，最常用的排最前。

### 3.6 粒度自动默认（前端可覆盖）

| 范围 | 默认 granularity |
|---|---|
| ≤ 2 天 | `hour` |
| ≤ 60 天 | `day` |
| ≤ 400 天 | `week` |
| 更大 | `month` |

理由：一小时一个柱子＝最多 48 根；一天一根＝最多 60 根。超过就挤在一起了。

### 3.7 空桶补零（后端必须做）

SQL 只返回"有数据的时间点"，柱状图会断档。后端按 granularity 生成**完整时间轴**：

```json
"buckets": [
  {"bucket":"2026-10-20","series":{"deepseek-chat":{...}}},
  {"bucket":"2026-10-21","series":{}},                      ← 无数据但保留
  {"bucket":"2026-10-22","series":{"deepseek-chat":{...}}}
]
```

### 3.8 堆叠序列 top-N 收敛

`breakdown=session` 可能有几十个序列 → 图例爆炸。只返回 top 6，其余并成 `other`：

```json
"meta": {"truncated": 12, "series_order":["deepseek-chat","gpt-4o","other"]}
```

### 3.9 `summary` 必须独立计算

顶部概览卡片不能用 `buckets` 求和（会被 top-N 截断），要单独跑一次无 breakdown 的聚合：

```json
"summary": {
  "events": 812, "requests": 812,
  "input": 120000, "output": 40000,
  "tokens": 160000,
  "cache_hit": 80000, "cache_miss": 40000, "cache_rate": 0.667,
  "messages_out": 900, "bytes_out": 210000,
  "fail_rate": 0.012,
  "unknown_cache": 3        -- 上游没报缓存命中的请求数
}
```

---

## 4. 接口契约（完整）

```http
GET /api/stats/query?kind=llm&metric=tokens&granularity=day
    &from=2026-10-01&to=2026-10-22&breakdown=model&bot_id=3437542570
```

```json
{
  "meta": {
    "kind":"llm", "metric":"tokens", "granularity":"day", "breakdown":"model",
    "from":"2026-10-01", "to":"2026-10-22",
    "timezone":"Asia/Shanghai",
    "unit":"tokens", "truncated":0, "series_order":["deepseek-chat"]
  },
  "buckets": [
    {"bucket":"2026-10-22",
     "series":{"deepseek-chat":{"tokens":1800,"input":900,"output":900,
                                "cache_hit":600,"cache_miss":300,"requests":4}},
     "total":{"tokens":1800,"requests":4}}
  ],
  "summary": { "...": "见 §3.9" }
}
```

返回**原始整数**，单位换算（`240000 → 240K`）由前端做。

---

## 5. 前端结构

```
┌ 筛选行 ─────────────────────────────────────────────────────────────┐
│ [类型▾ llm|收消息|发消息] [度量▾ tokens] [时间范围▾] [粒度: 时|天|周|月] │
│ [账号▾] [来源▾] [API▾] [模型▾] [会话▾]                              │
├ 概览卡 ─────────────────────────────────────────────────────────────┤
│ 总 token 160K │ 输入 120K │ 输出 40K │ 缓存命中 66.7% │ 请求 812 次   │
├ 主图：堆叠柱状 ────────────────────────────────────────────────────┤
│  y = token（自动 K / M）      x = 日期                              │
│  ▉ deepseek-chat   ▉ gpt-4o   ▉ other                              │
├ 明细表（同一份数据，不二次请求）────────────────────────────────────┤
│ 日期 | 模型 | 请求 | 输入 | 输出 | 缓存命中 | 失败                   │
└────────────────────────────────────────────────────────────────────┘
```

交互：

- **图例点击**隐藏某序列 → 纯前端，不重新请求
- **点柱子** → 下钻到该时段明细（进阶，可选）
- **粒度切换** → 重新请求（后端算桶）
- **切 kind** → 度量下拉联动（llm 只显示 token 类；msg 只显示条数/字节）

---

## 6. 记录点（三处，都在框架）

| kind | 位置 | 说明 |
|---|---|---|
| `llm` | `app/agent/loop/turn.py` 收尾（`_record_telemetry` 旁） | `usage_sink` 已是累计值（多轮/重试都算进去） |
| `msg_in` | inbound 节点 / 事件分发入口 | 按 `event_type` 分：message / notice / request |
| `msg_out` | `app/agent/delivery/channel.py::send_item` | 唯一发送出口，天然不漏 |

### 6.1 写入策略

**内存攒批 → 批量 INSERT**，不逐条落库：

- 每 20 条 或 每 2 秒 flush 一次
- 关闭运行时 / 进程退出**强制 flush**（避免丢最后一批）
- **绝不用 `_schedule_persist` 那种 fire-and-forget**（历史教训：`Cannot operate on a closed database`）
- 写失败只 `logger.debug` 留痕，不影响主流程

---

## 7. 边界与坑

| 坑 | 处理 |
|---|---|
| **流式拿不到 usage** | `estimated=1` 标记，图上单独显示/可筛掉 |
| **一轮多请求**（工具轮/重试/回退） | `usage_sink` 已累加，记累计值；`requests` 记实际请求次数 |
| **缓存命中未知 vs 0** | NULL 语义（§2.1） |
| **时区** | 存 UTC 秒 + `day_local` 用配置时区算；改时区需重算该列（提供脚本） |
| **失败请求** | `ok=0` 也记（否则看不出"某模型一直 429"），但 `tokens` 可能为 0 |
| **隐私** | 会话榜默认显示群号后 4 位（`…056`），完整值只在点开时查 |
| **保留期** | 建议事实表留 12 个月，超期按月清理（`DELETE WHERE ts < ?`，分批 1000 条） |
| **重启丢数据** | 攒批 + 强制 flush（§6.1） |

---

## 8. 施工顺序（建议）

| 步 | 内容 | 验证 |
|---|---|---|
| S1 | 建表 + `StatRecorder`（攒批写入）+ 迁移脚本 | 单测：写入/批量/关闭 flush |
| S2 | `llm` 记账（turn 收尾） | 单测：多轮请求累计值正确、`cache_hit` NULL 语义 |
| S3 | `msg_in` / `msg_out` 记账 | 单测：收发各一条、segments/bytes |
| S4 | `/api/stats/query`（含 filter/bucket/breakdown/补零/top-N） | 单测：128 组合中的代表组合、空桶补零 |
| S5 | `/api/stats/facets`（含联动） | 单测：只返回出现过的值 |
| S6 | 前端页面（筛选行 + 概览卡 + 堆叠柱 + 明细表） | 手工验收 |
| S7 | 保留期清理任务 | 单测：分批删除 |

---

## 9. 待确认

1. **`source` 的分类粒度**：用 `TurnRequest.kind`（chat/proactive/schedule/module），还是要细分到模块名（`llm_memory` / `llm_proactive`）？
2. **`msg_in` 是否统计"未触发 LLM 的消息"**：群里所有消息都记，还是只记进了流水线的？
3. **会话榜隐私**：默认显示后 4 位是否够？
4. **保留期**：12 个月？还是永久（数据量其实不大）？
