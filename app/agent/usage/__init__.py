"""数据统计：单表 `stat_event` + 写入/查询/清理。

## 分层

- **本包属于框架的基础功能**：LLM/provider/消息收发都是框架的，记账点必须在框架里，
  否则每个 provider、每个插件都要重复写一遍。
- 展示层（页面、导出、告警）可以做成模块，通过 `/api/stats/*` 读数据。

## 三类统计同级（`kind`）

| kind | 含义 | 主要度量 |
|---|---|---|
| `llm` | 一次 LLM 请求 | input/output/cache_hit/tokens/reasoning |
| `msg_in` | 一条入站消息 | messages=1，`outcome` 记结局 |
| `msg_out` | 一条出站消息 | messages=1，segments/bytes |

## 归属

`source` = 模块名（`chat` / `llm_proactive` / …）；拿不到上下文时记 `unknown`
（这本身是有用信号：说明该调用绕过了总线）。
"""

from __future__ import annotations

from app.agent.usage.context import current_usage_ctx, usage_scope
from app.agent.usage.labels import session_label
from app.agent.usage.recorder import (
    FLUSH_INTERVAL_SECONDS,
    FLUSH_MAX_ROWS,
    StatRecorder,
    get_recorder,
    set_recorder,
)

__all__ = [
    "FLUSH_INTERVAL_SECONDS",
    "FLUSH_MAX_ROWS",
    "StatRecorder",
    "current_usage_ctx",
    "get_recorder",
    "session_label",
    "set_recorder",
    "usage_scope",
]
