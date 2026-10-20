"""群聊记录的事件翻译层：OneBot 事件 → `LogEvent`（纯函数，无 IO、无模块状态）。

## 本模块只负责

把「群里发生了一件事」翻译成一条/一组 `LogEvent`，并在写入前做两件事：

1. **脱敏**：昵称走与会话历史同一套 `safe_sender_label`；
2. **剥离控制标记**：`[reply]` / `<type=...>` 这类词进环境块会与输出侧指令或标签
   语法撞车，甚至变成伪指令。

## 为什么和 module.py 分开

`module.py` 是插件骨架（生命周期 + `@module_hook` 接线 + 存储挂载），它的职责是
「接住事件并交给谁」。翻译口径（哪个字段进 `payload`、私聊只记戳一戳、`by_me`
怎么判）是会反复调整的业务细节，混在一起时每次改口径都要在 300 行里翻。

拆开后可以单测：给定一个假事件，断言产出的 `LogEvent` 字段——不需要起模块、不需要
容器、不需要存储。这也让 `module.py` 回到「看一眼就知道它在接哪些事件」的规模。
"""

from __future__ import annotations

import re
import time
from typing import Any

from app.agent.group_log.events import (
    KIND_EMOJI,
    KIND_MESSAGE,
    KIND_MY_SEND,
    KIND_POKE,
    KIND_RECALL,
    LogEvent,
    make_scope,
)

#: 控制标记剥离：群里有人发 ``[reply]`` / ``<type=...>`` 时，
#: 这些词进环境块会与"输出侧指令"或既有标签语法撞车，甚至变成伪指令
_DIRECTIVE_RE = re.compile(
    r"<type=[^>]{0,32}>|\[reply\]|\[@\s*\d{1,15}\s*\]|\[id\s*\d{1,20}\]|\[↩[^\]]{0,64}\]|\[♡[^\]]{0,32}\]",
    re.IGNORECASE,
)


def strip_directives(text: str) -> str:
    """剥掉会与指令/标签撞车的控制形态，保留正常聊天内容。"""
    return _DIRECTIVE_RE.sub("", str(text or "")).strip()


def text_of(event: Any, *, strip: bool = True) -> str:
    """事件 → 可读正文（复用既有段渲染器：图片/表情/语音都是占位符）。"""
    from app.agent.context.group import extract_msg_text

    from app.core.logger import logger

    try:
        text = extract_msg_text(
            getattr(event, "message", None),
            None,
            False,
            str(getattr(event, "message_id", "") or ""),
            None,
        )
    except Exception as e:  # noqa: BLE001 - 渲染失败退化为空文本，仍记录事件
        logger.debug(f"[GroupLog] 正文渲染失败: {e}")
        text = ""
    text = text or str(getattr(event, "raw_message", "") or "")
    return strip_directives(text) if strip else text


def sender_label(event: Any) -> str:
    """昵称（写入即脱敏，与会话历史的 mask_nickname 同口径）。"""
    from app.agent.context.group import safe_sender_label

    user = getattr(event, "user", None)
    raw = getattr(user, "card", "") or getattr(user, "nickname", "") or ""
    return safe_sender_label(str(raw or "")) if raw else ""


def reply_to(event: Any) -> str:
    """被回复消息的 id（没有回复段时为空串）。"""
    for seg in getattr(event, "message", None) or []:
        stype = seg.get("type") if isinstance(seg, dict) else getattr(seg, "type", "")
        data = (seg.get("data") if isinstance(seg, dict) else getattr(seg, "data", {})) or {}
        if stype == "reply":
            return str(data.get("id", "") or data.get("message_id", "") or "")
    return ""


def outbound_text(params: dict) -> str:
    """发送参数 → 可读文本（兼容 str / 段列表 / Message 对象）。"""
    from app.infrastructure.onebot.client import _message_preview

    try:
        return str(_message_preview(params.get("message")) or "")
    except Exception:  # noqa: BLE001
        return str(params.get("message") or "")


def _is_me(event: Any, user_id: str) -> bool:
    return bool(user_id) and user_id == str(getattr(event, "self_id", "") or "")


def group_message_event(event: Any, *, strip: bool = True) -> list[LogEvent]:
    """群消息 → 一条 `KIND_MESSAGE`。"""
    group = getattr(event, "group", None)
    group_id = str(getattr(group, "group_id", "") or getattr(event, "group_id", "") or "")
    scope = make_scope(True, group_id=group_id)
    if not scope:
        return []
    user_id = str(getattr(event, "user_id", "") or "")
    return [LogEvent(
        ts=int(getattr(event, "time", 0) or 0),
        kind=KIND_MESSAGE,
        scope=scope,
        group_id=group_id,
        message_id=str(getattr(event, "message_id", "") or ""),
        user_id=user_id,
        nickname=sender_label(event),
        text=text_of(event, strip=strip),
        reply_to=reply_to(event),
        by_me=_is_me(event, user_id),
    )]


def emoji_event(event: Any) -> list[LogEvent]:
    """表情贴/取消 → 每个表情一条 `KIND_EMOJI`（无明细时记一条空回应，避免整段互动丢失）。"""
    group_id = str(getattr(event, "group_id", "") or "")
    scope = make_scope(True, group_id=group_id)
    if not scope:
        return []
    user_id = str(getattr(event, "user_id", "") or "")
    likes = getattr(event, "emoji_likes", None) or [{"emoji_id": ""}]
    events = []
    for item in likes:
        emoji_id = str((item or {}).get("emoji_id", "") or "") if isinstance(item, dict) else ""
        events.append(LogEvent(
            ts=int(getattr(event, "time", 0) or 0),
            kind=KIND_EMOJI,
            scope=scope,
            group_id=group_id,
            message_id=str(getattr(event, "message_id", "") or ""),
            user_id=user_id,
            nickname=sender_label(event),
            payload={
                "emoji_id": emoji_id,
                "is_add": bool(getattr(event, "emoji_is_add", True)),
                "count": (item or {}).get("count", 1) if isinstance(item, dict) else 1,
            },
            by_me=_is_me(event, user_id),
        ))
    return events


def poke_event(event: Any) -> list[LogEvent]:
    """戳一戳 → 一条 `KIND_POKE`。**群聊与私聊都记**（私聊唯一入账的互动）。"""
    group_id = str(getattr(event, "group_id", "") or "")
    is_group = bool(group_id)
    operator = str(getattr(event, "operator_id", "") or getattr(event, "user_id", "") or "")
    target = str(getattr(event, "target_id", "") or "")
    scope = make_scope(
        is_group,
        group_id=group_id,
        user_id=operator if not is_group else None,
    )
    if not scope:
        return []
    return [LogEvent(
        ts=int(getattr(event, "time", 0) or 0),
        kind=KIND_POKE,
        scope=scope,
        group_id=group_id,
        user_id=operator,
        nickname=sender_label(event),
        payload={"target_id": target},
        by_me=_is_me(event, operator),
    )]


def recall_event(event: Any) -> list[LogEvent]:
    """群撤回 → 一条 `KIND_RECALL`。"""
    group_id = str(getattr(event, "group_id", "") or "")
    scope = make_scope(True, group_id=group_id)
    if not scope:
        return []
    operator = str(getattr(event, "operator_id", "") or getattr(event, "user_id", "") or "")
    return [LogEvent(
        ts=int(getattr(event, "time", 0) or 0),
        kind=KIND_RECALL,
        scope=scope,
        group_id=group_id,
        message_id=str(getattr(event, "message_id", "") or ""),
        user_id=operator,
        nickname=sender_label(event),
        by_me=_is_me(event, operator),
    )]


def my_send_event(ctx: Any, *, bot_id: Any, strip: bool = True) -> list[LogEvent]:
    """机器人自己发出的群消息 → 一条 `KIND_MY_SEND`（"我做过什么"与去重的依据）。"""
    group_id = str(getattr(ctx, "group_id", "") or "")
    scope = make_scope(True, group_id=group_id)
    if not scope:
        return []
    text = outbound_text(getattr(ctx, "params", {}) or {})
    if strip:
        text = strip_directives(text)
    return [LogEvent(
        ts=int(time.time()),
        kind=KIND_MY_SEND,
        scope=scope,
        group_id=group_id,
        message_id=str(getattr(ctx, "message_id", "") or ""),
        user_id=str(bot_id or ""),
        nickname="我",
        text=text,
        by_me=True,
    )]
