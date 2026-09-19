"""历史补全登记：让「信息查看」的结果长期留在会话历史里。

## 为什么要单独一个模块

``focus`` 是**进程内**的注意力表（每会话有界 + TTL），设计上不落盘——它服务"指代消解"，
丢了只影响猜测质量。但"补全"是事实：既然已经花了一次 API 调用读回被引用的消息/合并转发，
下一轮就必须还能看到它，否则模型会反复重读、或者对同一问题改口。

两者要的东西也不同：

=================  =========================  ============================
                   focus（注意力）            本模块（补全登记）
=================  =========================  ============================
生命周期            有界 + TTL，可丢弃        持久，跟随会话历史
存放内容            一句话摘要                 摘要 + 完整内容
归属                会话                       会话 + **触发它的 message_id**
=================  =========================  ============================

## 回写链路

```
工具取回内容（expand_message / expand_recent / expand_user / get_forward_msg / expand_image）
   → ExpansionLedger.record(...)                       本轮记账
   → 请求收尾 history_enrich.commit(session_id, message_id, ledger)
        ├─ 写 turn.expansions（内存，随会话保存落库）
        └─ 写持久登记（本模块 JSON，跨重启仍在）
   → 下一轮渲染历史时 replace_markers() 把占位升级为内容
```

## 标记替换约定（与意图一致）

- ``[引用456]`` / ``【未展开:引用456】`` → ``【已展开:引用456 → 摘要】``（替换占位）
- ``[合并转发456]`` / ``【未展开:合并转发456】`` → ``【已展开:合并转发456 → 摘要】``
- 结构化 ``@123`` → ``@昵称(123)``
- 工具取回的单条消息不进正文（那是别人说的话，塞进用户行会伪造发言），
  由 ``supplementary_blocks()`` 作为附加块排在该条历史之后。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Iterable

from app.core.logger import logger

# 每会话登记的条数上限（超过后淘汰最旧，避免长会话无限增长）
MAX_REFS_PER_SESSION = 40
# 持久登记里单个 ref 的内容上限（摘要之外再存一份完整内容，供"附加块"用）
MAX_CONTENT_CHARS = 1200


def _data_dir() -> str:
    try:
        from app.llm import llm_data_dir

        return llm_data_dir()
    except Exception:  # noqa: BLE001
        return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "llm")


def _path() -> str:
    return os.path.join(_data_dir(), "history_enrich.json")


_LOCK = threading.RLock()
_STORE: dict[str, dict[str, dict]] | None = None


def _key(bot_id: Any, session_id: Any) -> str:
    return f"{bot_id}:{session_id}"


def _load() -> dict[str, dict[str, dict]]:
    global _STORE
    with _LOCK:
        if _STORE is not None:
            return _STORE
        data: dict[str, dict[str, dict]] = {}
        path = _path()
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    data = {
                        str(k): {str(ref): dict(item or {}) for ref, item in (v or {}).items()}
                        for k, v in raw.items()
                        if isinstance(v, dict)
                    }
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[HistoryEnrich] 读取登记失败（按空处理）: {e}")
        _STORE = data
        return _STORE


def _save() -> None:
    with _LOCK:
        store = _STORE or {}
        path = _path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(store, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[HistoryEnrich] 写登记失败（已忽略）: {e}")


def load_for(session_id: Any, bot_id: Any) -> dict[str, dict]:
    """本会话的登记：``{ref: {"kind", "summary", "content", "source", "at"}}``。"""
    if session_id in (None, ""):
        return {}
    return dict(_load().get(_key(bot_id, session_id), {}))


def lookup(session_id: Any, bot_id: Any, ref: Any) -> dict | None:
    item = load_for(session_id, bot_id).get(str(ref or "").strip())
    return dict(item) if isinstance(item, dict) else None


def remember(
    session_id: Any,
    bot_id: Any,
    *,
    items: Iterable[dict],
    trigger_message_id: Any = None,
) -> int:
    """登记一批补全结果（持久）。返回写入条数。"""
    records: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref", "") or "").strip()
        kind = str(item.get("kind", "") or "")
        if not ref or not kind:
            continue
        records.append({
            "kind": kind,
            "ref": ref,
            "summary": str(item.get("summary", "") or "")[:200],
            "content": str(item.get("content", "") or "")[:MAX_CONTENT_CHARS],
            "source": str(item.get("source", "") or ""),
            "at": int(item.get("at") or time.time()),
            "trigger_message_id": str(trigger_message_id or item.get("trigger_message_id", "") or ""),
        })
    if not records or session_id in (None, ""):
        return 0

    with _LOCK:
        store = _load()
        bucket = store.setdefault(_key(bot_id, session_id), {})
        for record in records:
            bucket[record["ref"]] = record
        if len(bucket) > MAX_REFS_PER_SESSION:
            # 淘汰最旧（按登记时间）
            for ref, _item in sorted(bucket.items(), key=lambda kv: kv[1].get("at", 0))[
                : len(bucket) - MAX_REFS_PER_SESSION
            ]:
                bucket.pop(ref, None)
        _save()
    return len(records)


def to_expanded_refs(session_id: Any, bot_id: Any) -> dict[str, dict[str, str]]:
    """转成渲染层要的 ``{"messages": {...}, "users": {...}}`` 形态。"""
    out: dict[str, dict[str, str]] = {"messages": {}, "users": {}}
    for ref, item in load_for(session_id, bot_id).items():
        summary = str(item.get("summary", "") or "")
        if not summary:
            continue
        bucket = "users" if item.get("kind") == "user" else "messages"
        out[bucket][ref] = summary
    return out


def supplementary_blocks(
    session_id: Any, bot_id: Any, entry: dict | Any, *, text: str = ""
) -> list[dict]:
    """工具取回的单条消息 → 附加块（排在该条历史之后，不塞进它的正文）。

    ``text`` 为该条**已渲染**的正文：若正文里已经通过占位替换体现了这份内容
    （``【已展开:… → …】``），就不再重复附加。
    """
    refs = entry_refs(entry)
    if not refs:
        return []
    body = str(text or "")
    blocks: list[dict] = []
    seen: set[str] = set()
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        if ref and ref in body:
            continue  # 正文里已体现（占位已替换）
        item = lookup(session_id, bot_id, ref)
        if not item:
            continue
        content = str(item.get("content") or item.get("summary") or "").strip()
        if not content:
            continue
        blocks.append({"role": "user", "content": f"【已取回:消息{ref}】{content}"})
    return blocks


def clear(session_id: Any = None, bot_id: Any = None) -> None:
    """清理登记（测试与 "#chat new" 用）。"""
    with _LOCK:
        store = _load()
        if session_id is None:
            store.clear()
        else:
            store.pop(_key(bot_id, session_id), None)
        _save()


# ==================== 标记替换 ====================


def _summary(session_id: Any, bot_id: Any, ref: str, fallback: str = "") -> str:
    item = lookup(session_id, bot_id, ref) or {}
    text = str(item.get("summary") or "").strip()
    return text or fallback


def replace_markers(text: str, session_id: Any, bot_id: Any, *, max_marks: int = 3) -> str:
    """把历史正文里的占位/未展开标记升级为已展开内容（有登记时）。

    - ``[引用456]`` / ``【未展开:引用456】`` → ``【已展开:引用456 → 摘要】``
    - ``[合并转发456]`` / ``【未展开:合并转发456】`` → ``【已展开:合并转发456 → 摘要】``
    - ``@123`` → ``@昵称(123)``（用户登记）
    未登记的一律保持原样（不能凭空编内容）。
    """
    raw = str(text or "")
    if not raw:
        return raw
    registered = load_for(session_id, bot_id)
    if not registered:
        return raw

    replaced = 0
    for ref, item in registered.items():
        if replaced >= max_marks:
            break
        kind = str(item.get("kind") or "")
        summary = str(item.get("summary") or "").strip()
        if kind == "user":
            name = summary
            if name and f"@{ref}" in raw:
                raw = raw.replace(f"@{ref}", f"@{name}({ref})")
                replaced += 1
            continue
        if not summary:
            continue
        label = "合并转发" if kind == "forward" else "引用"
        for pattern in (f"[{label}{ref}]", f"[{label} {ref}]", f"【未展开:{label}{ref}】"):
            if pattern in raw:
                raw = raw.replace(pattern, f"【已展开:{label}{ref} → {summary}】")
                replaced += 1
                break
    return raw


# ==================== 引用/转发锚点 ====================


def entry_refs(entry: dict | Any) -> list[str]:
    """从历史条目里取出"可能已被工具取回"的 ref 列表。

    包含：条目内 ``reply`` 段的被引用消息 id、``forward`` 段的承载消息 id，
    以及条目自身 ``message_id``（``expand_message`` / ``expand_recent`` 会以它为 ref）。
    """
    data = entry if isinstance(entry, dict) else getattr(entry, "__dict__", {}) or {}
    base = data.get("base") if isinstance(data.get("base"), dict) else {}
    segments = base.get("segments") or data.get("segments") or []
    refs: list[str] = []
    for seg in segments:
        stype = seg.get("type") if isinstance(seg, dict) else getattr(seg, "type", "")
        sdata = (seg.get("data") if isinstance(seg, dict) else getattr(seg, "data", {})) or {}
        ref = str(sdata.get("id", "") or "").strip()
        if ref and stype in ("reply", "forward"):
            refs.append(ref)
    # 本条自己的 message_id：工具按"这条消息"取回时的 ref
    mid = str(data.get("message_id", "") or "").strip()
    if mid:
        refs.append(mid)
    return refs


def rendering_for(session_id: Any, bot_id: Any) -> tuple[Any, Any] | tuple[None, None]:
    """给渲染器准备 ``(render_content, entry_blocks)`` 两个钩子；无登记时返回 (None, None)。

    - ``render_content(entry)``：把该条正文里的占位/未展开标记升级为已展开内容；
    - ``entry_blocks(entry)``：工具取回的单条消息作为附加块（不塞进该条正文）。
    """
    if session_id in (None, "") or not load_for(session_id, bot_id):
        return None, None

    def render_content(entry) -> str | None:
        text = _entry_text(entry)
        if text is None:
            return None
        return replace_markers(text, session_id, bot_id)

    def entry_blocks(entry, *, text: str = "") -> list[dict]:
        return supplementary_blocks(session_id, bot_id, entry, text=text)

    return render_content, entry_blocks


def _entry_text(entry: Any) -> str | None:
    """取条目当前正文：结构化用 base.text，旧数据用 legacy_text。"""
    from app.llm.history_model import HistoryEntry

    if isinstance(entry, HistoryEntry):
        base = entry.base
        return base.text if base.text else (entry.legacy_text or None)
    if isinstance(entry, dict):
        base = entry.get("base") if isinstance(entry.get("base"), dict) else {}
        text = str(base.get("text", "") or "")
        if text:
            return text
        legacy = str(entry.get("content", "") or "")
        return legacy or None
    return None


# ==================== 本轮记账 ====================


class ExpansionLedger:
    """一次请求内工具取回内容的记账本；请求收尾时统一提交。"""

    def __init__(self, session_id: Any = "", bot_id: Any = "") -> None:
        self.session_id = session_id
        self.bot_id = bot_id
        self._items: list[dict] = []

    def record(self, kind: str, ref: Any, summary: str = "", *, content: str = "", source: str = "") -> None:
        ref = str(ref or "").strip()
        if not ref or (not summary and not content):
            return
        self._items.append({
            "kind": str(kind),
            "ref": ref,
            "summary": summary,
            "content": content,
            "source": source,
            "at": int(time.time()),
        })

    @property
    def items(self) -> list[dict]:
        return list(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def drain(self) -> list[dict]:
        items, self._items = self._items, []
        return items


_LEDGERS: dict[str, ExpansionLedger] = {}


def ledger_for(bot_id: Any, session_id: Any) -> ExpansionLedger:
    """取（或建）本会话本轮的记账本。"""
    key = _key(bot_id, session_id)
    with _LOCK:
        ledger = _LEDGERS.get(key)
        if ledger is None:
            ledger = ExpansionLedger(session_id=session_id, bot_id=bot_id)
            _LEDGERS[key] = ledger
        return ledger


def commit(bot_id: Any, session_id: Any, trigger_message_id: Any = None) -> int:
    """请求收尾：把本轮记账写进持久登记（并清空记账本）。"""
    if session_id in (None, ""):
        return 0
    key = _key(bot_id, session_id)
    with _LOCK:
        ledger = _LEDGERS.get(key)
    if ledger is None or not ledger:
        return 0
    items = ledger.drain()
    count = remember(session_id, bot_id, items=items, trigger_message_id=trigger_message_id)
    if count:
        logger.add_info(f"#{bot_id}").debug(f"[HistoryEnrich] 补全登记 {count} 项 -> {session_id}")
    return count
