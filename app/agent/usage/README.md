> 本文只负责：把「花了多少 token / 收发了多少消息」记进一张表 `stat_event`，并提供查询与清理。

# app/agent/usage —— 数据统计（框架基础功能）

## 为什么在框架里（而不是做成模块）

记账点分布在 provider 与消息收发路径上，这些**都是框架的**：

```
BaseModule.llm_turn   → run_turn → invoker.chat ─┐
BaseModule.llm_chat   → client.chat ─────────────┤
模块 resolve_invoker()→ invoker.chat ─────────────┼→ chat_with_fallback → get_provider()
模块 get_provider()   → provider.chat ───────────┘
```

如果放到模块里，每个 provider / 每条路径都要重复实现一遍。
展示层（页面、导出、告警）才适合做成模块，通过 `/api/stats/*` 读数据。

## 文件

| 文件 | 职责 |
|---|---|
| `recorder.py` | `StatRecorder`：攒批写入（20 条 / 2 秒）、退出强制 flush、分批 purge |
| `context.py` | `usage_scope`：contextvar 传「这笔是谁花的」（`source` = 模块名） |
| `metered.py` | `MeteredProvider`：套在 provider 上的计量代理，一次覆盖四条调用路径 |
| `labels.py` | 会话展示名：`名字前5字…group123456` |
| `query.py` | 查询：时间桶 + 堆叠维度 + facet 筛选（一份 SQL 覆盖所有组合） |

## 不变量

1. `cache_hit` / `cache_miss` **必须可空**：`NULL` = 上游没报（未知），`0` = 报了但没命中；
2. 一轮多次请求（工具轮/回退/重试）在 provider 层**各记一行**（真实消耗）；
3. 拿不到归属上下文时记 `source='unknown'` —— 这本身是"有模块绕过总线"的信号；
4. 退出必须 `await recorder.close()`，禁止 fire-and-forget。

## 相关文档

- 设计：`docs/DATA_STATS_DESIGN.md`
- 表结构：`app/infrastructure/persistence/database.py`（`stat_event`）
