"""会话焦点表（Referent Focus Registry）。

用途（设计基准见 ``docs/referent-resolution-design.md``）：
- 记录"最近被讨论过"的对象（消息 / 用户），供指代消解与消息组装使用——
  用户说"那个/那条/刚才说的/那个样子"这类**纯回指**时，唯一可靠的依据就是它；
- 承载"已展开"登记：本会话取回过的 id 附带一句话摘要，于是

  ====================  ==================================================
  状态                  渲染形态
  ====================  ==================================================
  待读取（没取过）      ``【未展开:合并转发576048059】``
  读取过（取过）        ``【已展开:合并转发576048059 → B站《学校的8种违法行为》】``
  ====================  ==================================================

  ——重复请求直接命中登记（零 API 调用），杜绝"重读取"；
- 供每轮请求注入一行「当前对话焦点」，让模型知道"刚才在聊什么"。

设计约束：
- 进程内、有界（每会话条数上限 + TTL），与 ``nicknames.py`` 同形态，不落盘；
- **只存摘要，不存原文**：原文按需取回，避免上下文与隐私双重膨胀；
- 显著性 = 来源权重 + 时间衰减，公式写死（不做参数玄学）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

# 每会话默认上限与 TTL（调用方可传 config 覆盖）
DEFAULT_MAX_ITEMS = 12
DEFAULT_TTL_SECONDS = 1800

# 来源权重：显式指向 > 文本提及 > 本轮取回 > @ > 窗口弱候选
SOURCE_WEIGHT: dict[str, float] = {
    "reply": 5.0,
    "text": 4.0,
    "expand": 3.0,
    "at": 2.0,
    "window": 0.5,
}

# 来源 → 人话说明（渲染焦点行时用）
SOURCE_PHRASE: dict[str, str] = {
    "reply": "你回复的那条",
    "text": "你提到的",
    "expand": "上一轮讨论过",
    "at": "你 @ 的",
    "window": "群里最近一条",
}


@dataclass
class FocusItem:
    """一个焦点对象：消息（``kind="message"``）或用户（``kind="user"``）。"""

    ref: str
    kind: str = "message"
    label: str = ""
    summary: str = ""
    source: str = "window"
    ts: float = 0.0
    turn: int = 0
    ttl: float = DEFAULT_TTL_SECONDS

    def display(self) -> str:
        return self.label or f"{'用户' if self.kind == 'user' else '消息'}{self.ref}"

    def expired(self, now: float | None = None) -> bool:
        return (time.time() if now is None else now) - self.ts > self.ttl


@dataclass
class _SessionFocus:
    items: dict[str, FocusItem] = field(default_factory=dict)
    turn: int = 0


_STORE: dict[str, _SessionFocus] = {}


def _key(bot_id: Any, session_id: Any) -> str:
    return f"{bot_id}:{session_id}"


def _prune(bucket: _SessionFocus, max_items: int) -> None:
    now = time.time()
    stale = [ref for ref, item in bucket.items.items() if item.expired(now)]
    for ref in stale:
        bucket.items.pop(ref, None)
    if len(bucket.items) > max_items:
        # 按显著性淘汰最弱项
        ranked = sorted(bucket.items.values(), key=lambda i: score(i, bucket.turn))
        for item in ranked[: len(bucket.items) - max_items]:
            bucket.items.pop(item.ref, None)


def score(item: FocusItem, current_turn: int) -> float:
    """显著性 = 来源权重 + 2.0 × 0.5^(距今轮数)。"""
    base = SOURCE_WEIGHT.get(item.source, 0.5)
    age = max(0, current_turn - item.turn)
    return base + 2.0 * (0.5 ** age)


def begin_turn(bot_id: Any, session_id: Any) -> int:
    """进入新一轮（每次 LLM 请求调一次），返回当前轮序号。"""
    bucket = _STORE.setdefault(_key(bot_id, session_id), _SessionFocus())
    bucket.turn += 1
    return bucket.turn


def current_turn(bot_id: Any, session_id: Any) -> int:
    bucket = _STORE.get(_key(bot_id, session_id))
    return bucket.turn if bucket else 0


def note(
    bot_id: Any,
    session_id: Any,
    ref: Any,
    *,
    kind: str = "message",
    label: str = "",
    summary: str = "",
    source: str = "window",
    ttl: float = DEFAULT_TTL_SECONDS,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> FocusItem | None:
    """登记/刷新一个焦点对象。

    - 已存在时：``summary`` 仅在非空时覆盖（取回过的事实比猜测可靠）；
      ``source`` 取权重更高者（显式指向不该被窗口弱候选降级）；
    - ``turn``/``ts`` 每次调用都刷新（代表"最近一次被关注"）。
    """
    text = str(ref or "").strip()
    if not text:
        return None
    bucket = _STORE.setdefault(_key(bot_id, session_id), _SessionFocus())
    item = bucket.items.get(text)
    if item is None:
        item = FocusItem(ref=text, kind=kind, label=label, summary=summary, source=source)
        bucket.items[text] = item
    else:
        if kind and kind != item.kind:
            # 同一 ref 先以消息身份登记、后又作为用户出现：保留更强来源的语义
            item.kind = item.kind if SOURCE_WEIGHT.get(item.source, 0) >= SOURCE_WEIGHT.get(source, 0) else kind
        if label:
            item.label = label
        if summary:
            item.summary = summary
        if SOURCE_WEIGHT.get(source, 0.5) > SOURCE_WEIGHT.get(item.source, 0.5):
            item.source = source
    item.ts = time.time()
    item.turn = bucket.turn
    item.ttl = ttl
    _prune(bucket, max_items)
    return item if item.ref in bucket.items else None


def get(bot_id: Any, session_id: Any, ref: Any) -> FocusItem | None:
    bucket = _STORE.get(_key(bot_id, session_id))
    if not bucket:
        return None
    _prune(bucket, DEFAULT_MAX_ITEMS)
    return bucket.items.get(str(ref or "").strip())


def summary_of(bot_id: Any, session_id: Any, ref: Any) -> str:
    """已登记摘要（空串表示本会话还没取过）。"""
    item = get(bot_id, session_id, ref)
    return item.summary if item else ""


def items(bot_id: Any, session_id: Any, *, kind: str | None = None) -> list[FocusItem]:
    """按显著性降序返回焦点项（读取时顺带做过期淘汰）。"""
    bucket = _STORE.get(_key(bot_id, session_id))
    if not bucket:
        return []
    _prune(bucket, DEFAULT_MAX_ITEMS)
    pool = [i for i in bucket.items.values() if kind is None or i.kind == kind]
    return sorted(pool, key=lambda i: score(i, bucket.turn), reverse=True)


def expanded_refs(bot_id: Any, session_id: Any) -> dict[str, dict[str, str]]:
    """已展开映射，供渲染层把标记从"未展开"升级为"已展开"。

    返回 ``{"messages": {ref: 摘要}, "users": {qq: 昵称}}``。
    """
    out: dict[str, dict[str, str]] = {"messages": {}, "users": {}}
    for item in items(bot_id, session_id):
        if not item.summary:
            continue
        bucket = "users" if item.kind == "user" else "messages"
        out[bucket][item.ref] = item.summary
    return out


def _relative(item: FocusItem, turn: int) -> str:
    age = max(0, turn - item.turn)
    if age <= 0:
        return "本轮"
    if age == 1:
        return "上一轮"
    return f"{age} 轮前"


def focus_lines(
    bot_id: Any,
    session_id: Any,
    *,
    limit: int = 3,
    max_chars: int = 400,
) -> str:
    """渲染「当前对话焦点」块；没有焦点时返回空串。"""
    picked = items(bot_id, session_id)[: max(0, limit)]
    if not picked:
        return ""
    turn = current_turn(bot_id, session_id)
    lines = ["当前对话焦点（最近被讨论的优先）："]
    used = 0
    for index, item in enumerate(picked, start=1):
        phrase = SOURCE_PHRASE.get(item.source, "相关")
        detail = item.summary or "（内容还没取回来）"
        line = f"{index}. {item.display()} —— {_relative(item, turn)}{phrase}：{detail}"
        if used + len(line) > max_chars and index > 1:
            break
        used += len(line)
        lines.append(line)
    return "\n".join(lines)


def clear(bot_id: Any = None, session_id: Any = None) -> None:
    """清理指定会话（或全部）焦点。"""
    if bot_id is None:
        _STORE.clear()
        return
    key = _key(bot_id, session_id)
    if session_id is None:
        for existing in [k for k in _STORE if k.startswith(f"{bot_id}:")]:
            _STORE.pop(existing, None)
    else:
        _STORE.pop(key, None)


def clear_all() -> None:
    _STORE.clear()
