"""统计查询：一份 SQL 覆盖所有筛选组合。

## 三条铁律

1. **不拼 SQL**：全部命名参数 + `(:x IS NULL OR col = :x)` 空值短路；
2. **时间条件只用 `ts`**（整数比较走索引），`day_local` 只用于分组/索引前缀；
3. **facet 等值匹配**，不做模糊搜索（要搜用 `facets()` 先取精确值）。
"""

from __future__ import annotations

import time
from typing import Any

#: 时间桶表达式（SQLite：本地时区分桶）
BUCKET_EXPR: dict[str, str] = {
    "minute": "strftime('%Y-%m-%dT%H:%M', ts, 'unixepoch', 'localtime')",
    "hour": "strftime('%Y-%m-%dT%H', ts, 'unixepoch', 'localtime')",
    "day": "day_local",
    "week": "strftime('%Y-W%W', ts, 'unixepoch', 'localtime')",
    "month": "strftime('%Y-%m', ts, 'unixepoch', 'localtime')",
}

#: 堆叠维度 → 列
BREAKDOWN_COL: dict[str, str] = {
    "none": "''",
    "model": "model",
    "api": "api",
    "source": "source",
    "bot": "bot_id",
    "session": "session_id",
    "event": "event_type",
    "outcome": "outcome",
}

#: 度量 → (表达式, 单位)
METRIC_EXPR: dict[str, tuple[str, str]] = {
    "tokens": ("SUM(tokens_total)", "tokens"),
    "input": ("SUM(input_tokens)", "tokens"),
    "output": ("SUM(output_tokens)", "tokens"),
    "cache_hit": ("SUM(cache_hit)", "tokens"),
    "reasoning": ("SUM(reasoning_tokens)", "tokens"),
    "requests": ("SUM(requests)", "count"),
    "messages": ("SUM(messages)", "count"),
    "triggered": ("SUM(CASE WHEN outcome <> 'ignored' THEN messages ELSE 0 END)", "count"),
    "bytes": ("SUM(bytes)", "bytes"),
    "events": ("COUNT(*)", "count"),
}

#: 允许的筛选 facet（列名 → 参数名相同）
FILTER_FACETS: tuple[str, ...] = (
    "bot_id",
    "source",
    "api",
    "model",
    "session_id",
    "group_id",
    "user_id",
    "event_type",
    "outcome",
)

#: 一次查询最多返回多少个堆叠序列（超出并入 other）
TOP_SERIES = 6


def _where_sql(extra: tuple[str, ...] = ()) -> str:
    """生成筛选 WHERE（全部空值短路）。"""
    lines = ["ts >= :from_ts", "ts < :to_ts", "(:kind IS NULL OR kind = :kind)"]
    lines += [f"(:{name} IS NULL OR {name} = :{name})" for name in FILTER_FACETS]
    lines.append("(:ok_only = 0 OR ok = 1)")
    lines.append("(:include_estimated = 1 OR estimated = 0)")
    lines.extend(extra)
    return "\n  AND ".join(lines)


def _bucket_axis(granularity: str, from_ts: int, to_ts: int) -> list[str]:
    """生成完整时间轴（空桶补零用）。"""
    axis: list[str] = []
    if granularity == "day":
        day = from_ts
        while day < to_ts:
            axis.append(time.strftime("%Y-%m-%d", time.localtime(day)))
            day += 86400
        return axis
    if granularity == "month":
        tm = time.localtime(from_ts)
        year, month = tm.tm_year, tm.tm_mon
        while True:
            cur = time.mktime((year, month, 1, 0, 0, 0, 0, 0, -1))
            if cur >= to_ts:
                break
            axis.append(f"{year:04d}-{month:02d}")
            month += 1
            if month > 12:
                year, month = year + 1, 1
        return axis
    if granularity == "week":
        week = from_ts
        while week < to_ts:
            axis.append(time.strftime("%Y-W%W", time.localtime(week)))
            week += 7 * 86400
        return axis
    step = 3600 if granularity == "hour" else 60
    fmt = "%Y-%m-%dT%H" if granularity == "hour" else "%Y-%m-%dT%H:%M"
    cur = from_ts
    while cur < to_ts:
        axis.append(time.strftime(fmt, time.localtime(cur)))
        cur += step
    return axis


async def query(
    db: Any,
    *,
    kind: str | None = "llm",
    metric: str = "tokens",
    granularity: str = "day",
    breakdown: str = "model",
    from_ts: int,
    to_ts: int,
    filters: dict[str, Any] | None = None,
    ok_only: bool = False,
    include_estimated: bool = True,
    skip_empty: bool = True,
) -> dict:
    """按时间桶 + 堆叠维度聚合。返回 `{meta, buckets, summary}`。

    Args:
        skip_empty: 是否省略"全零"时间桶。用量表形态（日期做列）默认省略，
            否则整张表会被无数据的 0 撑满；画连续趋势时传 False 保留完整时间轴。
    """
    metric = metric if metric in METRIC_EXPR else "tokens"
    granularity = granularity if granularity in BUCKET_EXPR else "day"
    breakdown = breakdown if breakdown in BREAKDOWN_COL else "model"
    expr, unit = METRIC_EXPR[metric]
    bucket_sql = BUCKET_EXPR[granularity]
    series_sql = BREAKDOWN_COL[breakdown]

    params: dict[str, Any] = {
        "from_ts": int(from_ts),
        "to_ts": int(to_ts),
        "kind": None if kind in (None, "", "all") else str(kind),
        "ok_only": 1 if ok_only else 0,
        "include_estimated": 1 if include_estimated else 0,
    }
    for name in FILTER_FACETS:
        value = (filters or {}).get(name)
        params[name] = str(value) if value not in (None, "") else None

    sql = f"""
        SELECT {bucket_sql} AS bucket,
               {series_sql} AS series,
               {expr}       AS value,
               SUM(messages)        AS messages,
               SUM(CASE WHEN outcome <> 'ignored' THEN messages ELSE 0 END) AS triggered,
               SUM(requests)        AS requests,
               SUM(input_tokens)    AS input_tokens,
               SUM(output_tokens)   AS output_tokens,
               SUM(cache_hit)       AS cache_hit,
               SUM(cache_miss)      AS cache_miss,
               SUM(tokens_total)    AS tokens_total,
               SUM(reasoning_tokens) AS reasoning_tokens,
               SUM(bytes)           AS bytes,
               COUNT(*)             AS events
        FROM stat_event
        WHERE {_where_sql()}
        GROUP BY bucket, series
        ORDER BY bucket
    """
    rows = await db.fetchall(sql, params)

    # ---- 序列排序（按总量取 top-N，其余并入 other）----
    totals: dict[str, int | float] = {}
    for row in rows:
        key = str(row["series"] or "")
        totals[key] = totals.get(key, 0) + (row["value"] or 0)
    ordered = [k for k, _v in sorted(totals.items(), key=lambda kv: -kv[1])]
    keep = ordered[:TOP_SERIES]
    truncated = max(0, len(ordered) - TOP_SERIES)

    # ---- 组装 buckets（补零）----
    axis = _bucket_axis(granularity, int(from_ts), int(to_ts))
    by_bucket: dict[str, dict[str, dict]] = {}
    for row in rows:
        bucket = str(row["bucket"] or "")
        key = str(row["series"] or "")
        if key not in keep:
            key = "other"
        slot = by_bucket.setdefault(bucket, {})
        item = slot.setdefault(
            key,
            {
                "value": 0,
                "messages": 0,
                "triggered": 0,
                "requests": 0,
                "input": 0,
                "output": 0,
                "cache_hit": 0,
                "cache_miss": 0,
                "tokens": 0,
                "reasoning": 0,
                "bytes": 0,
                "events": 0,
            },
        )
        item["value"] += row["value"] or 0
        item["messages"] += row["messages"] or 0
        item["triggered"] += row["triggered"] or 0
        item["requests"] += row["requests"] or 0
        item["input"] += row["input_tokens"] or 0
        item["output"] += row["output_tokens"] or 0
        item["cache_hit"] += row["cache_hit"] or 0
        item["cache_miss"] += row["cache_miss"] or 0
        item["tokens"] += row["tokens_total"] or 0
        item["reasoning"] += row["reasoning_tokens"] or 0
        item["bytes"] += row["bytes"] or 0
        item["events"] += row["events"] or 0

    buckets = []
    hidden = 0
    for bucket in axis:
        series = by_bucket.get(bucket, {})
        total = sum((s.get("value") or 0) for s in series.values())
        if skip_empty and not series:
            hidden += 1
            continue
        buckets.append({"bucket": bucket, "series": series, "total": total})

    # ---- summary：独立聚合（不受 top-N 截断影响）----
    summary_rows = await db.fetchall(
        f"""
        SELECT COUNT(*)             AS events,
               SUM(requests)        AS requests,
               SUM(messages)        AS messages,
               SUM(input_tokens)    AS input_tokens,
               SUM(output_tokens)   AS output_tokens,
               SUM(cache_hit)       AS cache_hit,
               SUM(cache_miss)      AS cache_miss,
               SUM(tokens_total)    AS tokens_total,
               SUM(reasoning_tokens) AS reasoning_tokens,
               SUM(bytes)           AS bytes,
               SUM(CASE WHEN outcome <> 'ignored' THEN messages ELSE 0 END) AS triggered,
               SUM(CASE WHEN outcome = 'command'  THEN messages ELSE 0 END) AS by_command,
               SUM(CASE WHEN outcome = 'module'   THEN messages ELSE 0 END) AS by_module,
               SUM(CASE WHEN outcome = 'llm'      THEN messages ELSE 0 END) AS by_llm,
               SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS fails,
               SUM(CASE WHEN kind = 'llm' AND cache_hit IS NULL THEN 1 ELSE 0 END) AS unknown_cache
        FROM stat_event
        WHERE {_where_sql()}
        """,
        params,
    )
    s = summary_rows[0] if summary_rows else {}
    hit = (s["cache_hit"] or 0) if s else 0
    miss = (s["cache_miss"] or 0) if s else 0
    events = (s["events"] or 0) if s else 0
    fails = (s["fails"] or 0) if s else 0
    summary = {
        "events": events,
        "requests": (s["requests"] or 0) if s else 0,
        "messages": (s["messages"] or 0) if s else 0,
        "triggered": (s["triggered"] or 0) if s else 0,
        "by_command": (s["by_command"] or 0) if s else 0,
        "by_module": (s["by_module"] or 0) if s else 0,
        "by_llm": (s["by_llm"] or 0) if s else 0,
        "input": (s["input_tokens"] or 0) if s else 0,
        "output": (s["output_tokens"] or 0) if s else 0,
        "tokens": (s["tokens_total"] or 0) if s else 0,
        "reasoning": (s["reasoning_tokens"] or 0) if s else 0,
        "cache_hit": hit,
        "cache_miss": miss,
        "cache_rate": round(hit / (hit + miss), 4) if (hit + miss) else None,
        "bytes": (s["bytes"] or 0) if s else 0,
        "fail_rate": round(fails / events, 4) if events else 0,
        "unknown_cache": (s["unknown_cache"] or 0) if s else 0,
    }

    return {
        "meta": {
            "kind": kind or "all",
            "metric": metric,
            "granularity": granularity,
            "breakdown": breakdown,
            "unit": unit,
            "from_ts": int(from_ts),
            "to_ts": int(to_ts),
            "series_order": keep + (["other"] if truncated else []),
            "truncated": truncated,
            "hidden_buckets": hidden,
        },
        "buckets": buckets,
        "summary": summary,
    }


async def facets(
    db: Any,
    *,
    kind: str | None = "llm",
    from_ts: int,
    to_ts: int,
    filters: dict[str, Any] | None = None,
) -> dict[str, list[dict]]:
    """下拉选项：只返回该时间范围内**真的出现过**的 facet 取值。"""
    out: dict[str, list[dict]] = {}
    for name in FILTER_FACETS:
        # 求 facets 时排除自身筛选（否则下拉只能看到当前值）
        scoped = {k: v for k, v in (filters or {}).items() if k != name}
        params: dict[str, Any] = {
            "from_ts": int(from_ts),
            "to_ts": int(to_ts),
            "kind": None if kind in (None, "", "all") else str(kind),
            "ok_only": 0,
            "include_estimated": 1,
        }
        for facet in FILTER_FACETS:
            value = scoped.get(facet)
            params[facet] = str(value) if value not in (None, "") else None
        rows = await db.fetchall(
            f"""
            SELECT {name} AS value, COUNT(*) AS events
            FROM stat_event
            WHERE {_where_sql((f"({name} <> '')",))}
            GROUP BY {name}
            ORDER BY events DESC
            """,
            params,
        )
        out[name] = [
            {"value": row["value"], "label": row["value"], "events": row["events"]}
            for row in rows
        ]
    return out


__all__ = [
    "BUCKET_EXPR",
    "BREAKDOWN_COL",
    "FILTER_FACETS",
    "METRIC_EXPR",
    "TOP_SERIES",
    "facets",
    "query",
]
