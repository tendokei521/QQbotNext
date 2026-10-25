"""在线不变式（P4 第 5 步）：会话日志与内存历史的**一致性自检**。

## 为什么要有

P4 把"会话历史"变成事件日志 + 派生视图，但派生物一旦漂移，症状是"模型看到的上下文
和日志里记的不一样"——这种问题不会报错，只会让回复变得奇怪，且事后极难复原。

所以要一条**可开关的不变式**：每次请求前把 `log.derive_messages()` 与内存里的
`session.data.history` 比一遍，不等就落盘 diff + 留痕。默认关闭（每轮都做投影有开销），
排查/迁移期打开就是探测器。

## 比什么（口径）

只比**事实层**：`(role, content)` 序列。条目里还有 time / message_id / base 等元信息，
它们在不同路径上补充时机不同（句柄是发送后才回填的），拿它们逐字段比会天天假红。
工具结果、轮次事件本来就不进 `messages`，也不参与比较（`SURFACE_ELIGIBLE` 已定义）。

## 不炸业务

- 存储层没有事件（老库、旧替身）→ 跳过，不算不一致；
- 任何异常只留痕，绝不影响本轮请求。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

#: 比较时只看这两个字段
FACT_KEYS = ("role", "content")


def _facts(messages) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        out.append((
            str(message.get("role") or ""),
            str(message.get("content") or ""),
        ))
    return out


def _desync_dir(runtime) -> Path:
    from app.agent import llm_data_dir, safe_bot_id

    bot = safe_bot_id(getattr(runtime, "bot_id", "") or "")
    return Path(llm_data_dir()) / bot / "prompt_dump"


def verify_history_projection(
    runtime,
    session_mgr,
    session,
    *,
    where: str = "",
) -> dict | None:
    """核对事件日志派生物与内存历史；不一致时落盘并返回 ``{"missing", "extra", "path"}``。

    返回 ``None`` 表示：没开开关 / 没有事件（老数据）/ 一致。
    """
    config = getattr(runtime, "config", None)
    try:
        enabled = bool(config.get("prompt_dump_verify", False)) if config is not None else False
    except Exception:  # noqa: BLE001 —— 配置读不出来按关闭处理（保守）
        return None
    if not enabled or session is None:
        return None

    store = getattr(session_mgr, "history", None)
    task_id = str(getattr(session, "task_id", "") or "")
    if store is None or not task_id or not hasattr(store, "load_events"):
        return None

    from app.agent.session.session_log import SessionLog

    try:
        events = store.load_events(task_id)
        if not events:
            return None       # 存量数据还没迁移：无从比较，不算不一致
        derived = _facts(SessionLog(store, task_id).derive_messages())
        actual = _facts(getattr(getattr(session, "data", None), "history", None) or [])
    except Exception as e:  # noqa: BLE001 —— 自检失败不该影响请求
        _log(runtime).warning(f"[Invariant] 历史自检失败（已忽略）: {e}")
        return None

    if derived == actual:
        return None

    # 逐位置找第一处差异，给出可操作的信息而不是"两者不同"
    index = 0
    while index < min(len(derived), len(actual)) and derived[index] == actual[index]:
        index += 1
    report = {
        "where": where,
        "session_id": str(getattr(session, "id", "") or getattr(session, "session_id", "") or ""),
        "task_id": task_id,
        "first_diff_index": index,
        "derived": derived,
        "actual": actual,
        "ts": int(time.time()),
    }
    path = ""
    try:
        target = _desync_dir(runtime)
        target.mkdir(parents=True, exist_ok=True)
        file = target / f"desync-{time.strftime('%Y%m%d-%H%M%S')}-{index}.json"
        file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        path = str(file)
    except Exception as e:  # noqa: BLE001 —— 落盘失败也要留痕
        _log(runtime).warning(f"[Invariant] 落盘 desync 失败（已忽略）: {e}")

    _log(runtime).error(
        f"[Invariant] 历史派生不一致 -> {report['session_id']}（{where or '未知位置'}），"
        f"第 {index} 条起不同，derived={len(derived)} actual={len(actual)}，"
        f"dump={path or '未落盘'}"
    )
    return {
        "derived": derived,
        "actual": actual,
        "first_diff_index": index,
        "path": path,
    }


def _log(runtime):
    from app.agent import logger
    from app.core.logger import account_label

    return logger.add_info(account_label(getattr(runtime, "bot_id", "?")))
