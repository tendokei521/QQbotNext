"""会话历史的**追加式事件日志**（P4：单一真相）。

## 为什么要有这个文件

改造前"会话历史"是 `conversations.messages` 这一整包 JSON 数组，写入方式是
`INSERT OR REPLACE` **全量覆盖**：12 个 `add_message` 调用点各自"读出来、改列表、
写回去"，谁都可以改别人写的行；流式发言靠"按对象身份原地改"，`message_id` 回填
靠"找最近一条 assistant"——于是同一份事实有三个真相：内存列表、覆盖写、位置语义。

本模块把"这一轮到底发生了什么"变成**只追加**的事件流，`messages` 退化成它的
**派生缓存**（WebUI / 统计 / 蒸馏 / 导出继续读缓存，不改任何读侧）。

## 投影规则（关键决策：日志记全部，投影只取一部分）

- `user/message` / `assistant/message` → 进 `messages`（surface-eligible）；
- `assistant/delta` → 按 `entry_id` 折叠到那一条（流式逐句更新的语义）；
- `assistant/message_id` → 给某条（默认最近一条）assistant 补句柄，**不改正文**；
- `turn/start` `turn/end` `tool/call` `tool/result` `request/header` `usage`
  → 只入日志、**不进** `messages`。工具结果是否跨轮持久化是另一个产品决策（P5 之后再谈），
  这一步只保证"日志里有、模型看不到"。

## 边界

- 只负责"记事件 + 派生"，不负责什么时候该记（那是调用方/turn 生命周期的事）。
- 与 P2 的 `TurnReason` 对齐：`turn/end` 的 payload 里就有 reason，供观测/蒸馏消费。
"""

from __future__ import annotations

from typing import Any

#: 会进入 `messages`（模型可见历史）的事件类型
SURFACE_ELIGIBLE = frozenset({"user/message", "assistant/message"})

EVENT_USER = "user/message"
EVENT_ASSISTANT = "assistant/message"
EVENT_ASSISTANT_DELTA = "assistant/delta"
EVENT_ASSISTANT_MESSAGE_ID = "assistant/message_id"
EVENT_MESSAGE_EDITED = "message/edited"
EVENT_MESSAGE_DELETED = "message/deleted"
EVENT_TURN_START = "turn/start"
EVENT_TURN_END = "turn/end"
EVENT_TOOL_CALL = "tool/call"
EVENT_TOOL_RESULT = "tool/result"
EVENT_REQUEST_HEADER = "request/header"
EVENT_COMPACTION_START = "compaction/start"
EVENT_COMPACTION_SUMMARY = "compaction/summary"
EVENT_COMPACTION_END = "compaction/end"
EVENT_USAGE = "usage"

#: log-only 事件（永远不进 messages）
LOG_ONLY = frozenset({
    EVENT_TURN_START,
    EVENT_TURN_END,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_REQUEST_HEADER,
    EVENT_COMPACTION_START,
    EVENT_COMPACTION_SUMMARY,
    EVENT_COMPACTION_END,
    EVENT_USAGE,
})


class SessionLog:
    """一个会话（task）的事件日志：追加 + 派生 `messages`。

    存储层只加了一张 `history_events` 表（`conversations.messages` 保留为派生缓存），
    所以所有既有读取方无需改动。
    """

    def __init__(self, store: Any, task_id: str) -> None:
        self.store = store
        self.task_id = str(task_id or "")

    # ── 写 ────────────────────────────────────────────

    def append(
        self,
        type_: str,
        *,
        entry: dict | None = None,
        entry_id: str = "",
        message_id: Any = None,
        **payload: Any,
    ) -> dict:
        """追加一条事件（seq 由存储层分配），返回落库后的事件行。

        ``entry`` 是**今天会写进 messages 的那个 dict**（role/time/content/base…），
        原样存下来：派生时直接把它交出去，所以格式/脱敏都不受本层影响。
        """
        data: dict[str, Any] = dict(payload)
        if entry is not None:
            data["entry"] = dict(entry)
        if entry_id:
            data["entry_id"] = str(entry_id)
        if message_id is not None:
            data["message_id"] = message_id
        return self.store.append_event(self.task_id, type_, data)

    # ── 读 ────────────────────────────────────────────

    def events(self, *, since: int = 0) -> list[dict]:
        return self.store.load_events(self.task_id, since=since)

    def derive_messages(self) -> list[dict]:
        """把事件流投影成"今天的 `get_history()` 同形"的列表。

        - 只投影 surface-eligible；
        - `assistant/delta` 折叠到同一 `entry_id`（流式边发边更新的语义）；
        - `message/edited` / `message/deleted` 按 `entry_id` 折叠（事件本身仍保留）。
        """
        derived: list[dict] = []
        #: entry_id → 在 derived 里的下标
        index_by_id: dict[str, int] = {}
        #: 最近一条 assistant 的下标（`assistant/message_id` 的默认目标）
        last_assistant: int | None = None

        for event in self.events():
            type_ = str(event.get("type") or "")
            data = event.get("payload") or {}
            entry = data.get("entry")
            entry_id = str(data.get("entry_id") or "")

            if type_ == EVENT_ASSISTANT_DELTA:
                if isinstance(entry, dict):
                    if entry_id and entry_id in index_by_id:
                        derived[index_by_id[entry_id]] = dict(entry)
                    else:
                        if entry_id:
                            index_by_id[entry_id] = len(derived)
                        derived.append(dict(entry))
                        if str(entry.get("role") or "") == "assistant":
                            last_assistant = len(derived) - 1
                continue

            if type_ == EVENT_ASSISTANT_MESSAGE_ID:
                target = index_by_id.get(entry_id) if entry_id else last_assistant
                if target is not None and 0 <= target < len(derived):
                    derived[target] = {**derived[target], "message_id": data.get("message_id")}
                continue

            if type_ == EVENT_MESSAGE_EDITED:
                target = index_by_id.get(entry_id)
                if target is not None and 0 <= target < len(derived) and isinstance(entry, dict):
                    derived[target] = dict(entry)
                continue

            if type_ == EVENT_MESSAGE_DELETED:
                target = index_by_id.get(entry_id)
                if target is not None and 0 <= target < len(derived):
                    derived.pop(target)
                    index_by_id.clear()   # 下标失效，后续按 id 的折叠需要重建
                continue

            if type_ in SURFACE_ELIGIBLE and isinstance(entry, dict):
                if entry_id:
                    index_by_id[entry_id] = len(derived)
                derived.append(dict(entry))
                if type_ == EVENT_ASSISTANT:
                    last_assistant = len(derived) - 1

        return derived


def log_of(session_mgr: Any, session: Any = None, task_id: str = "") -> SessionLog | None:
    """从 SessionManager（和 Session）取该会话的事件日志；拿不到就 None。

    记账是旁路：没有 store（旧替身）、没有 task_id 都只是"记不了"，不影响一轮。
    """
    store = getattr(session_mgr, "history", None)
    tid = str(task_id or getattr(session, "task_id", "") or "")
    if store is None or not tid or not hasattr(store, "append_event"):
        return None
    try:
        return SessionLog(store, tid)
    except Exception:  # noqa: BLE001 —— 构造失败按"记不了"处理
        return None


def safe_append(log: SessionLog | None, type_: str, **payload: Any) -> int:
    """向日志追加一条事件，返回 seq（失败返回 0）。记账失败绝不影响业务。"""
    if log is None:
        return 0
    try:
        return int(log.append(type_, **payload).get("seq") or 0)
    except Exception:  # noqa: BLE001 —— 旁路
        return 0


def surface_eligible(type_: str) -> bool:
    """这个事件类型会不会进入模型可见的历史。"""
    return type_ in SURFACE_ELIGIBLE

def request_header(
    messages: list,
    tools: list | None = None,
    *,
    model: str = "",
    chain: list | None = None,
    params: dict | None = None,
) -> dict:
    """把**最终请求体**算成一份可复现的指纹（写进 `request/header` 事件）。

    - `system_hash`：所有 system 段按序拼接后的 sha256 前 16 位——system 改一个字就变；
    - `tools`：工具名 + （描述+参数 schema）的 hash——工具定义漂移可检出；
    - `params`：只记真正影响生成的键（temperature / max_tokens / tools 数量）；
    - `chain`：provider 链（回退时实际用哪个，由 `usage`/遥测记）。

    为什么不在事件里存整份 messages：messages 全量已在 `prompt_dump`（需要时开），
    事件里只存**指纹 + 组装输入**，既能比对又不把库撑大。
    """
    import hashlib
    import json as _json

    def _digest(text: str) -> str:
        return hashlib.sha256(str(text or "").encode("utf-8", "replace")).hexdigest()[:16]

    system_text = "\n\n".join(
        str(m.get("content") or "")
        for m in (messages or [])
        if isinstance(m, dict) and str(m.get("role") or "") == "system"
    )
    tool_fingerprints = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        tool_fingerprints.append({
            "name": str(fn.get("name") or ""),
            "hash": _digest(
                _json.dumps(
                    [fn.get("description") or "", fn.get("parameters") or {}],
                    ensure_ascii=False, sort_keys=True,
                )
            ),
        })
    safe_params = {}
    for key in ("temperature", "max_tokens", "max_tool_rounds"):
        if isinstance(params, dict) and params.get(key) is not None:
            safe_params[key] = params[key]
    return {
        "model": str(model or ""),
        "system_hash": _digest(system_text),
        "system_chars": len(system_text),
        "messages": len(messages or []),
        "tools": tool_fingerprints,
        "params": safe_params,
        "chain": [
            str((cfg or {}).get("provider") or "") + "/" + str((cfg or {}).get("model") or "")
            for cfg in (chain or [])
            if isinstance(cfg, dict)
        ],
    }
