# 数据统计：当前行为

侧栏「数据统计」（`/#/stats`）统计**token 用量**与**消息收发量**，不含金额/花费。
本文写的是现在实际怎么记、怎么算、怎么显示；代码位置与口径以下列文件为准。

| 环节 | 位置 |
|---|---|
| 页面 | `app/webui/dashboard/src/views/StatsPage.vue` |
| 接口 | `app/webui/api/stats.py`（`/api/stats/query`、`/facets`、`/purge`） |
| 查询/聚合 | `app/agent/usage/query.py` |
| 记账器 | `app/agent/usage/recorder.py`（单例 `get_recorder()`） |
| 计量与归属 | `app/agent/usage/metered.py`、`context.py` |
| 入站结局 | `app/agent/usage/messages.py` + `app/agent/loop/pipeline.py` + `app/plugin/nodes/inbound.py` |
| 出站 | `app/agent/delivery/channel.py::send_item` |
| 建表 | `app/infrastructure/persistence/database.py`（`stat_event`） |
| 实时推送 | `app/webui/stats_notify.py` → `/ws/logs` 上的 `stats_updated` 事件 |

## 1. 统计什么

一张表 `stat_event`，`kind` 三选一，三类同级：

| kind | 一行代表 | 主要度量 |
|---|---|---|
| `llm` | **一次上游 provider 调用** | `requests=1`、`input_tokens`、`output_tokens`、`cache_hit`、`cache_miss`、`reasoning_tokens`、`tokens_total`、`latency_ms`、`ok` |
| `msg_in` | **一条入站群聊/私聊消息**（含最终结局） | `messages=1`、`bytes`、`outcome`、`event_type`、`session_*` |
| `msg_out` | **一条真正发出去的消息** | `messages=1`、`segments`、`bytes`、`ok` |

`notice` / `request` 类事件不记 `msg_in`（只认 `message_group` / `message_private`）。

## 2. 口径（最容易搞错的几条）

| 事实 | 口径 |
|---|---|
| 记账点 | provider 工厂 `get_provider()` 外套 `MeteredProvider`，**每次上游调用一行**；回退链、重试、工具轮都各记一行（真实消耗） |
| 失败调用 | `ok=0` 照样写一行（`err_kind` 记异常类名），`tokens_total` 可能为 0 |
| 流式 | usage 只在末帧；中断后拿不到 usage 时 `estimated=1`、`input/output` 记 0，不假装精确 |
| `tokens_total` | 上游 `usage.total_tokens`；缺失时退回 `input + output` |
| `cache_hit` / `cache_miss` | 来自 `cache_hit_miss()`。**NULL = 上游没报，不等于 0**；命中率 = `hit / (hit + miss)`，分母为 0 时为 `null`（页面显示 `—`） |
| `reasoning_tokens` | 上游 `completion_tokens_details.reasoning_tokens`；页面把输出 token 说明为「含思考 token」 |
| `api` / `model` | `api` 由 `api_base` 主机名映射服务商短名（deepseek / openai / gemini / moonshot / zhipu …），`model` 记实际请求的模型名 |
| `source` | 用 contextvar 传的归属：`chat`（主对话与 `#` 指令）、模块名（如 `llm_trigger`）、`compress`、`nickname`；没设上下文则为 `unknown` |
| `session_id` / `session_label` | `group_<群号>` / `private_<QQ>`；`label` 是**写入时快照**：`名字前 5 字…group<群号>`，群改名后历史不变 |
| `day_local` / `ts` | `ts` 是 UTC 秒；`day_local` 是按**本机时区**算的 `YYYY-MM-DD`，面板分桶也用本机时区 |
| `outcome` | 只有 `msg_in` 有值，四类：`command`（`#` 指令）、`llm`（真的请求了 LLM）、`module`（模块处理并终止）、`ignored`（其余：未触发/权限不足/门控拦截） |
| `triggered` | 派生量，不是列：`SUM(CASE WHEN outcome <> 'ignored' THEN messages END)` |
| `bytes` | `msg_in` 用**字符数**（`len(文本)`），`msg_out` 用 **UTF-8 字节数**（`len(text.encode())`）；两者不可横向比 |
| `segments` | 出站消息段数，`max(1, 段数)` |

`msg_in` 的结局只有**做决定的地方**才知道，所以写两个点、同一事件只写一次（`_stat_recorded` 去重）：
入站节点在「没交给流水线」时写，流水线 `finally` 在拿到终局时写。

## 3. 存储与保留

- `stat_event` 单表 28 个业务列 + 自增 `id`，6 个索引（`day_local+kind`、`+model`、`+source`、`+api`、`+session_id`、`ts`）。
- 攒批写入：**满 20 条或距上次落库 1 秒**触发 `flush()`；`start()` 另起 1 秒 ticker；`close()` 强制 flush，不用 fire-and-forget。
- 写失败：整批回填缓冲（上限 500 条），只 `logger.debug`，绝不影响消息主流程。
- 保留策略：**永久**。`POST /api/stats/purge` 手动清理，按 1000 行分批删；`before_ts` 与 `account_id`（别名 `bot_id`）至少给一个，否则 400。

## 4. 查询口径

`GET /api/stats/query`（参数全是封闭白名单，缺省值如下）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `kind` | `llm` | `llm` / `msg_in` / `msg_out` / `all` |
| `metric` | `tokens` | `tokens,input,output,cache_hit,reasoning,requests,messages,triggered,bytes,events`；非法值回落 `tokens` |
| `granularity` | `day` | `minute,hour,day,week,month`；非法值回落 `day` |
| `breakdown` | `model` | `none,model,api,source,bot,session,event,outcome`；非法值回落 `model` |
| `from_ts` / `to_ts` | 近 30 天 / 现在 | UTC 整秒，左闭右开；`from_ts >= to_ts` 返回 400 |
| `account_id`（别名 `bot_id`）`source` `api` `model` `session_id` `group_id` `user_id` `event_type` `outcome` | 空=不限 | 等值匹配，不做模糊搜索 |
| `ok_only` | `false` | 只要成功行 |
| `include_estimated` | `true` | 是否计入估算行 |
| `skip_empty` | `true` | 省略全零时间桶；页面传 `0` 保留完整时间轴 |

- 序列取前 6 名（`TOP_SERIES`），其余并入 `other`，`meta.truncated` 说明合并了几个。
- `summary` **单独聚合**，不受 top-N 与空桶省略影响；含 `events/requests/messages/triggered/by_command/by_module/by_llm/input/output/tokens/reasoning/cache_hit/cache_miss/cache_rate/bytes/fail_rate/unknown_cache`。
- `fail_rate = ok=0 的行数 / 总行数`；`unknown_cache = kind='llm' 且 cache_hit IS NULL 的行数`。
- 返回**原始整数**，`K/M` 由前端格式化。
- `GET /api/stats/facets`：九个 facet 只列该时间范围内真的出现过的值（带 `events`、按条数降序），且**排除自身筛选**；`label` 目前等于 `value`，没有中文映射。
- 三个接口在记账器未装配时返回 503 `{"status":"error","message":"统计未启用"}`。

## 5. 页面怎么呈现

自上而下：

| 区块 | 内容 |
|---|---|
| 大 KPI | 「当前范围用量」（`summary.tokens`）+「当前范围请求次数」（`summary.requests`） |
| 筛选条 | 数据维度、账号、范围（今天/7/30/90/365 天）、指标、粒度，来源 / 模型 / 会话 / 结局（有候选值才出现）、清除筛选条件 |
| 小 KPI | `llm`：输入、输出、缓存命中率；`msg_in`：收到、响应、指令/插件/LLM；`msg_out`：发出、字节、失败率 |
| 柱图 | 默认不拆分单色；选项随数据维度变（按模型 / 按服务 / 按结局 / 按会话 / 按模块），最多 6 段 + other，底部图例与「已合并 N 个较小序列」 |
| 明细表 | 「按指标」（行=指标、列=时间、末列合计）或「按维度」（行=序列）；只显示有数据的列 |
| 其它 | 导出 CSV（前端由当前 `buckets` 生成）、刷新、跳「会话数据」 |

实时性：`StatRecorder` 落库成功 → 广播 `stats_updated`（推送侧节流 1 秒）→ 页面去抖 400ms 重查；
只在标签页可见时重查，断线自动重连并补一次。`范围` 的时间窗按本机时区取「今天 0 点到明天 0 点」再换成 UTC 秒。

## 6. 常见误读

| 误读 | 事实 |
|---|---|
| 「请求次数 = 回复条数」 | 一次回复可能多次上游调用（工具轮、回退、重试），每次一行，所以请求数常大于回复数 |
| 「缓存命中率 0%」 | 显示 `—` 是上游没报（`cache_hit IS NULL`），那是未知不是 0；真 0 才说明缓存没生效 |
| 「失败请求不花钱」 | 失败也计 1 次请求，`ok=0`、token 可能 0，`fail_rate` 就是按行数算的 |
| 「群名变了历史也变」 | `session_label` 是写入时的快照 |
| 「`source=unknown` 是某个模型/服务」 | 它表示这次调用**绕过了总线**、没设归属上下文 |
| 「统计里有花费」 | 只有 token / 次数 / 字节，没有金额 |
| 「运行诊断里的 LLM 遥测就是这里」 | 遥测（`/api/agent/telemetry`）是内存里最近 2000 条、重启清零、按账号隔离，与落库的 `stat_event` 无关 |
| 「两个 bytes 可比」 | 入站是字符数、出站是 UTF-8 字节数 |
| 「切到消息维度，大 KPI 为什么是 0」 | 大 KPI 只读 `tokens` / `requests`，消息行这两个字段为 0；看小 KPI 与明细表 |
| 「合计 = 表格各列相加」 | 合计来自 `summary`，不受空列省略与 top-N 截断影响 |
| 「删一行统计能改历史」 | 只能整体清理：`POST /api/stats/purge?before_ts=…` 或 `?account_id=…` |

## 7. 测试位置

`tests/agent/usage/`（记录器、归属、入站结局、出站、查询）与 `tests/webui/test_stats_api.py`、`test_stats_push.py`、`test_stats_triggered.py`。测试口径见 [TEST_POLICY.md](TEST_POLICY.md)。
