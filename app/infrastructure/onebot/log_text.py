"""OneBot 事件的日志文本渲染。

## 为什么单独成文件

`gateway.py` 的主类是连接管理（715 行：连接、去重、重连、僵尸扫描），末尾这三个函数
只负责「把一条消息/事件渲染成一行日志」。日志格式会反复调（截断长度、敏感字段过滤），
混在连接管理里每次都要拉到文件底部。

## 渲染口径

- `_message_log_text`：群/私聊消息一行（发送者 + 正文，超长截断）；
- `_raw_payload_text`：原始 payload 的去敏感摘要（debug 视图用）；
- `_event_text`：通知 / 申请 / 其它事件的通用一行。
"""

from __future__ import annotations

import json

from app.domain.events import BaseEvent, MessageEvent, NoticeEvent, RequestEvent
from app.infrastructure.onebot.codec import event_name


def _message_log_text(event: MessageEvent) -> str:
    """消息的可读日志摘要（仅影响日志显示，不影响传输/派发）。

    - text 段：原样拼接；
    - at / reply 短字段：带值 [at:qq] [reply:id]；
    - image / video 等长字段：仅保留类型 [image] [video]。
    """
    parts = []
    for seg in event.message:
        if seg.type == "text":
            parts.append(seg.data.get("text", ""))
        elif seg.type in ("at", "reply"):
            key = "qq" if seg.type == "at" else "id"
            parts.append(f"[{seg.type}:{seg.data.get(key, '')}]")
        else:
            parts.append(f"[{seg.type}]")
    return "".join(parts)


def _raw_payload_text(event: BaseEvent, limit: int = 500) -> str:
    """未知事件的原始 payload（截断防刷屏）：只在未收录类型上打，正常流量零开销。"""
    raw = getattr(event, "raw", None) or {}
    try:
        text = json.dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(raw)
    if len(text) > limit:
        return f"{text[:limit]}…(共 {len(text)} 字符)"
    return text


def _event_text(event: BaseEvent) -> str:
    """通知/申请类事件的动作描述（用于日志；群 id 由调用方拼进前缀）。"""
    t = event.event_type
    uid = event.user_id or 0
    if isinstance(event, NoticeEvent):
        if t == "notice_input_status":
            return f"{uid}正在输入"
        if t == "notice_poke":
            operator = getattr(event, "operator_id", 0) or uid
            target = getattr(event, "target_id", 0) or 0
            return f"{operator}戳了{target}" if target else f"{operator}戳了"
        if t in ("notice_group_recall", "notice_private_recall"):
            op = getattr(event, "operator_id", 0) or uid
            return f"{op}撤回了一条消息"
        if t == "notice_group_emoji":
            emoji_ids = [
                str(e.get("emoji_id", ""))
                for e in (getattr(event, "emoji_likes", []) or [])
                if isinstance(e, dict) and e.get("emoji_id")
            ]
            suffix = f" id:{','.join(emoji_ids)}" if emoji_ids else ""
            return f"{uid}回应了表情{suffix}"
        if t == "notice_group_increase":
            return f"{uid}入群"
        if t == "notice_group_decrease":
            return f"{uid}退群"
        return f"{event_name(t)} {uid}"
    if isinstance(event, RequestEvent):
        if t == "request_group":
            return f"{uid}申请加群"
        if t == "request_private":
            return f"{uid}申请加好友"
        return f"{event_name(t)} {uid}"
    return f"{event_name(t)} {uid}"
