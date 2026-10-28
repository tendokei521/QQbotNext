"""网关事件缓冲：有界队列 + 非阻塞入队（队满丢最旧）。

## 为什么单独成文件

``gateway.py`` 已贴近文件长度闸门（800 行），而「接收循环绝不能被事件处理阻塞」
是一条独立的背压策略：一旦 ``await queue.put`` 在队满时停住，同一连接上的 API
响应就没人消费，``send_group_msg`` 等全部 echo 超时（请求/响应自锁，整个账号
10~60s 不可用）。把策略拆出来既让网关主循环保持可读，也便于直接单测「丢最旧」。

## 取舍

队列满 = 模块/Agent 的处理速度跟不上事件洪峰。这里选择丢**最旧**事件并汇总告警：
丢旧消息的代价远小于整条连接卡死；告警按 ``EVENT_DROP_WARN_EVERY`` 汇总，避免
洪峰期间把日志刷爆。
"""

from __future__ import annotations

import asyncio
import contextlib
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
) -> bool:
    """非阻塞入队；队满时丢最旧事件并汇总告警。返回是否发生了丢弃。

    接收循环**绝不能**等待事件队列：一旦 ``await queue.put`` 在队满时停住，
    同一连接上一条 API 响应就没人消费，``send_group_msg`` 等全部 echo 超时。
    因此这里用 ``put_nowait``，把背压代价换成「丢弃最旧事件」。
    """
    try:
        queue.put_nowait(event)
        return False
    except asyncio.QueueFull:
        pass

    # 队列满：腾出最旧的一条再放入新事件。消费者只有一个（_dispatch_worker），
    # 它只会让队列更空，因此这里的第二次 put_nowait 不会被拒绝。
    with contextlib.suppress(asyncio.QueueEmpty):
        queue.get_nowait()
    queue.put_nowait(event)

    count = drops.get(account_id, 0) + 1
    drops[account_id] = count
    if logger is not None and (count == 1 or count % EVENT_DROP_WARN_EVERY == 0):
        logger.warning(
            f"[Gateway] 事件队列已满（{EVENT_QUEUE_MAXSIZE}），已累计丢弃 {count} 条最旧事件；"
            "事件处理能力不足，请检查模块/Agent 是否阻塞"
        )
    return True
