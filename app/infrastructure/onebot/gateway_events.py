"""网关事件缓冲：有界队列 + 非阻塞入队（队满丢最旧）+ 诊断台事件历史。

## 为什么单独成文件

``gateway.py`` 已贴近文件长度闸门（800 行），而「接收循环绝不能被事件处理阻塞」
是一条独立的背压策略：一旦 ``await queue.put`` 在队满时停住，同一连接上的 API
响应就没人消费，``send_group_msg`` 等全部 echo 超时（请求/响应自锁，整个账号
10~60s 不可用）。把策略拆出来既让网关主循环保持可读，也便于直接单测「丢最旧」。

诊断台的「最近收包事件」环形缓冲也在这里：它同样是纯缓冲，不参与派发逻辑。

## 取舍

队列满 = 模块/Agent 的处理速度跟不上事件洪峰。这里选择丢**最旧**事件并汇总告警：
丢旧消息的代价远小于整条连接卡死；告警按 ``EVENT_DROP_WARN_EVERY`` 汇总，避免
洪峰期间把日志刷爆。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

#: 单账号事件队列上限。超过它说明事件处理能力不足，开始丢最旧事件。
EVENT_QUEUE_MAXSIZE = 1024
#: 队列丢弃告警的汇总间隔：每丢这么多条打一次 warning，避免洪峰时刷屏。
EVENT_DROP_WARN_EVERY = 100


def new_event_queue(account_id: str, drops: dict[str, int]) -> asyncio.Queue:
    """创建该账号的有界事件队列，并把丢弃计数清零（新连接重新计数）。"""
    drops[account_id] = 0
    return asyncio.Queue(maxsize=EVENT_QUEUE_MAXSIZE)


def enqueue_event(
    queue: asyncio.Queue,
    event: Any,
    *,
    account_id: str,
    drops: dict[str, int],
    logger: Any = None,
) -> Any:
    """非阻塞入队；队满时丢最旧事件并汇总告警。

    返回**被丢弃的最旧事件**（未丢弃返回 None），供调用方给诊断历史补
    ``dropped`` 标记——否则洪峰下被丢的事件在诊断台完全看不到。

    接收循环**绝不能**等待事件队列：一旦 ``await queue.put`` 在队满时停住，
    同一连接上一条 API 响应就没人消费，``send_group_msg`` 等全部 echo 超时。
    因此这里用 ``put_nowait``，把背压代价换成「丢弃最旧事件」。
    """
    try:
        queue.put_nowait(event)
        return None
    except asyncio.QueueFull:
        pass

    # 队列满：腾出最旧的一条再放入新事件。消费者只有一个（_dispatch_worker），
    # 它只会让队列更空，因此这里的第二次 put_nowait 不会被拒绝。
    dropped = None
    if not queue.empty():
        dropped = queue.get_nowait()
    queue.put_nowait(event)

    count = drops.get(account_id, 0) + 1
    drops[account_id] = count
    if logger is not None and (count == 1 or count % EVENT_DROP_WARN_EVERY == 0):
        logger.warning(
            f"[Gateway] 事件队列已满（{EVENT_QUEUE_MAXSIZE}），已累计丢弃 {count} 条最旧事件；"
            "事件处理能力不足，请检查模块/Agent 是否阻塞"
        )
    return dropped


# ==================== 诊断台：最近收包事件 ====================

#: 每账号保留的最近收包事件条数（WebUI 诊断台只展示最近若干条）
HISTORY_MAXLEN = 100
#: 事件摘要的最大字符数（列表里只显示一行）
SUMMARY_MAXLEN = 80

#: 非文本消息段的中文占位（诊断台摘要用）
_SEGMENT_PLACEHOLDERS = {
    "image": "[图片]",
    "record": "[语音]",
    "video": "[视频]",
    "file": "[文件]",
    "json": "[卡片]",
    "forward": "[合并转发]",
    "node": "[合并转发]",
    "face": "[表情]",
    "mface": "[表情]",
}


def _event_type_name(raw: dict) -> str:
    """把 post_type 与子类型拼成可读事件类型（如 ``message.group``）。"""
    post_type = str(raw.get("post_type") or "")
    if post_type in ("message", "message_sent"):
        return f"message.{raw.get('message_type') or 'group'}"
    if post_type == "notice":
        return f"notice.{raw.get('notice_type') or 'unknown'}"
    if post_type == "request":
        return f"request.{raw.get('request_type') or 'unknown'}"
    if post_type == "meta_event":
        return f"meta_event.{raw.get('meta_event_type') or 'unknown'}"
    return post_type or "unknown"


def _message_summary(raw: dict) -> str:
    """从原始 message 段提取 ≤80 字纯文本摘要（图片记 ``[图片]``）。"""
    message = raw.get("message")
    parts: list[str] = []
    if isinstance(message, str):
        parts.append(message)
    elif isinstance(message, list):
        for seg in message:
            if not isinstance(seg, dict):
                continue
            seg_type = str(seg.get("type") or "")
            data = seg.get("data") or {}
            if seg_type == "text":
                parts.append(str(data.get("text") or ""))
            elif seg_type == "at":
                qq = str(data.get("qq") or "")
                parts.append("@全体成员" if qq in ("", "all") else f"@{qq}")
            else:
                parts.append(_SEGMENT_PLACEHOLDERS.get(seg_type, f"[{seg_type}]"))
    if not parts and raw.get("raw_message"):
        parts.append(str(raw["raw_message"]))
    return "".join(parts).strip()[:SUMMARY_MAXLEN]


class EventHistory:
    """每账号最近若干条收包事件的环形缓冲（WebUI 诊断台用）。

    只做 append / 读取，不参与派发主路径；所有读操作都不抛异常
    （账号不存在 / limit 非法一律降级为空列表或默认值），避免诊断台拖垮主流程。
    """

    def __init__(self, maxlen: int = HISTORY_MAXLEN) -> None:
        self._maxlen = maxlen
        self._buffers: dict[str, deque] = {}
        self._seq: dict[str, int] = {}

    def record(self, account_id: str | None, event: Any) -> dict:
        """记一条事件（环形缓冲满了自动挤掉最旧的），返回该条记录。

        返回的记录对象与缓冲里的是同一个 dict：调用方可在入队被背压丢弃时给它补
        ``dropped=True`` 标记（供诊断台显示「收到了但没处理」）。
        """
        key = str(account_id or "")
        if not key:
            return {}
        raw = getattr(event, "raw", None)
        raw = raw if isinstance(raw, dict) else {}
        seq = self._seq.get(key, 0) + 1
        self._seq[key] = seq
        buffer = self._buffers.get(key)
        if buffer is None:
            buffer = deque(maxlen=self._maxlen)
            self._buffers[key] = buffer
        event_time = raw.get("time") or getattr(event, "time", 0) or time.time()
        user_id = raw.get("user_id")
        if user_id is None:
            user_id = getattr(event, "user_id", None)
        record = {
            "seq": seq,
            "time": int(event_time),
            "event_type": _event_type_name(raw) if raw else str(getattr(event, "event_type", "") or "unknown"),
            "user_id": user_id,
            "group_id": raw.get("group_id"),
            "summary": _message_summary(raw) if raw else "",
            "raw": raw,
        }
        buffer.append(record)
        return record

    def recent(self, account_id: str, limit: int = 50) -> list[dict]:
        """最近 ``limit`` 条事件（按时间正序返回，最新的在末尾）。"""
        count = limit if isinstance(limit, int) and not isinstance(limit, bool) else 50
        count = max(1, min(count, self._maxlen))
        buffer = self._buffers.get(str(account_id))
        if not buffer:
            return []
        return list(buffer)[-count:]

    def latest_group_id(self, account_id: str) -> int | None:
        """该账号最近一条群消息的群号（诊断台「用最近的群」/测试消息缺省群号）。"""
        buffer = self._buffers.get(str(account_id))
        if not buffer:
            return None
        for record in reversed(buffer):
            group_id = record.get("group_id")
            if isinstance(group_id, bool):
                continue
            if isinstance(group_id, int) and group_id > 0:
                return group_id
            if isinstance(group_id, str) and group_id.isdigit():
                return int(group_id)
        return None

    def clear(self, account_id: str) -> None:
        """清空该账号的事件历史与序号（下一次从 #1 重新计数）。"""
        key = str(account_id)
        self._buffers.pop(key, None)
        self._seq.pop(key, None)
