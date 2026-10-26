"""OneBot 事件的日志文本渲染。

## 为什么单独成文件

`gateway.py` 的主类是连接管理（连接、重连、僵尸扫描），末尾这几个函数只负责
「把一条消息/事件渲染成一行日志」。日志格式会反复调（截断长度、敏感字段过滤），
混在连接管理里每次都要拉到文件底部。

## 渲染口径

- `_message_log_text`：群/私聊消息一行（发送者 + 正文，超长截断）；
- `_raw_payload_text`：原始 payload 的去敏感摘要（debug 视图用）；
- `_event_text`：通知 / 申请 / 其它事件的通用一行；
- `received_log_lines`：把上面三者拼成「前缀 + 正文」，网关只管发出去。
"""

from __future__ import annotations

import json
from collections.abc import Callable

from app.core.logger import sanitize_control
from app.domain.events import BaseEvent, MessageEvent, NoticeEvent, RequestEvent
from app.infrastructure.onebot.codec import event_name


def _conn_label(conn) -> str:
    """日志里用的账号名（id 只做判断，日志一律打名字）。"""
    from app.domain.bot import bot_label

    return bot_label(conn)


def _one_line(text: str) -> str:
    """把名字类字段压成一行并消毒。

    群名片/昵称可以带换行、制表符和控制字符：换行会把一条日志拆成两行（后面那行
    看起来像另一条日志），``\\x07`` 会让终端响铃。日志只做展示，压平不丢信息。
    """
    return sanitize_control(str(text or "").replace("\r", " ").replace("\n", " ")).strip()


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


def received_log_lines(
    conn,
    event: BaseEvent,
    group_name_of: Callable[[int], str] | None = None,
) -> list[tuple[str, str]]:
    """把一条事件渲染成待输出的 (日志前缀, 正文) 列表；空列表 = 这条不用打日志。

    前缀里带 ``[#索引]`` 与目标 id（群名 + 群号 / 私聊无群名），正文里带发送者。
    渲染与写出分开：网关只管循环 ``recv_logger.prefix(prefix).info(text)``，
    格式改动（加时间、改缩略规则）只动这里。
    """
    if isinstance(event, MessageEvent) and event.event_type in ("message_group", "message_private"):
        text = _message_log_text(event).strip()
        if not text:
            return []
        name = _one_line(event.user.card or event.user.nickname or "")
        prefix = f"接收<-] [{_conn_label(conn)}"
        if event.event_type == "message_group":
            group_name = _one_line(group_name_of(event.group.group_id) if group_name_of else "")
            prefix += (f"] [{group_name}({event.group.group_id})" if group_name
                       else f"[{event.group.group_id}]")
        name_info = f"{name}({event.user_id})" if name else f"{event.user_id}"
        return [(prefix, f"{name_info}: {text}")]

    if isinstance(event, MessageEvent):
        # 机器人自己发的消息不进入 recv：已由 API 发送侧 [发送->] 覆盖
        return []

    if event.event_type == "bot_heartbeat":
        return []
    text = _event_text(event)
    if not text:
        return []
    if event_name(event.event_type) == "未知类型":
        # 未收录的类型只打得出「未知类型 <uid>」这种没信息量的文本；端侧排查
        # （线上那条「未知类型 3437542570」实为端重启前推的 session-closed 通知）
        # 必须能看到原始 payload，否则只能靠时间猜。
        text = f"{text} | raw={_raw_payload_text(event)}"
    prefix = f"通知<-] [{_conn_label(conn)}"
    group_id = getattr(event, "group_id", 0) or 0
    if group_id:
        prefix += f"] [{group_id}"
    return [(prefix, text)]


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
