"""入站消息记账（`msg_in`）：把"这条消息的结局"写进 `stat_event`。

## 为什么要单独一个模块

结局只有**做决定的地方**才知道：

- `pipeline._run` 里才知道「是不是指令 / 触发没通过 / 真的进了 LLM」；
- 入站节点只知道「我有没有把它交给流水线」。

两边都要写同一份字段（会话、群、展示名、字节数），所以抽到这里，
避免两处各写一遍导致口径漂移。

## `outcome` 四分类

| outcome | 何时 |
|---|---|
| `command` | 正文是 `#` 指令 |
| `llm` | 真的进入了 LLM 请求 |
| `module` | 模块处理并终止了链（`event._stopped` / `_llm_stop`） |
| `ignored` | 其余（未触发 / 权限不足 / 门控拦截 / 无运行时） |
"""

from __future__ import annotations

from typing import Any

from app.agent.usage.labels import session_label

#: 事件上的归属标记：`"pipeline"` 表示流水线会负责记账，入站节点不要再记
STAT_OWNER_ATTR = "_stat_owner"
#: 事件上的去重标记：已经记过一条
STAT_RECORDED_ATTR = "_stat_recorded"


def inbound_fields(event: Any) -> dict[str, Any]:
    """从事件里抽出入站记账要用的 facet（会话 / 群 / 展示名 / 字节数）。"""
    is_private = getattr(event, "event_type", "") == "message_private"
    if is_private:
        ident = str(getattr(event, "user_id", "") or "")
        session_id = f"private_{ident}"
        name = getattr(getattr(event, "user", None), "nickname", "") or ""
        label = session_label(name, "private", ident)
        group_id = ""
    else:
        group = getattr(event, "group", None)
        ident = str(getattr(group, "group_id", "") or "")
        session_id = f"group_{ident}"
        name = getattr(group, "group_name", "") or ""
        label = session_label(name, "group", ident)
        group_id = ident
    return {
        "bot_id": str(getattr(event, "account_id", "") or getattr(event, "bot_id", "") or ""),
        "source": "inbound",
        "session_id": session_id,
        "session_label": label,
        "group_id": group_id,
        "user_id": str(getattr(event, "user_id", "") or ""),
        "event_type": str(getattr(event, "event_type", "") or ""),
        "bytes": len(str(getattr(event, "message", "") or "")),
    }


def record_inbound(event: Any, outcome: str) -> bool:
    """记一条 `msg_in`；同一个事件只会记一次。返回是否真的写了。"""
    if event is None:
        return False
    if getattr(event, STAT_RECORDED_ATTR, False):
        return False
    try:
        from app.agent.usage.recorder import get_recorder

        recorder = get_recorder()
        if recorder is None:
            return False
        if getattr(event, "event_type", "") not in ("message_group", "message_private"):
            return False
        recorder.record_msg_in(outcome=str(outcome or "ignored"), **inbound_fields(event))
        setattr(event, STAT_RECORDED_ATTR, True)
        return True
    except Exception:  # noqa: BLE001 —— 统计是旁路，绝不影响消息处理
        return False


__all__ = [
    "STAT_OWNER_ATTR",
    "STAT_RECORDED_ATTR",
    "inbound_fields",
    "record_inbound",
]
