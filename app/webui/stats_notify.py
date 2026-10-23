"""后端主动推送统计更新（替代前端 10 秒轮询）。

## 链路

```
StatRecorder.flush() 落库成功
  → on_written(count) 回调（bootstrap 装配）
  → ws.notify_stats_updated()
  → ConnectionManager.broadcast({"type": "stats_updated", ...})
  → 前端 onSocketMessage('stats_updated') → 立刻重查（去抖 400ms）
```

## 为什么复用它

WebUI 已有一条 `/ws/logs` 长连接，且前端已按 `payload.type` 分发广播事件
（数组 = 日志快照，对象带 type = 事件）。统计只要发一个 `stats_updated`
事件即可，不必新开连接。

## 节流

落库可能很频繁（每 1~2 秒一批）；推送本身也要花钱（序列化 + 广播）。
所以在**推送侧**再节流 1 秒：一个时间窗内多次落库只推一次，前端重查本来就是全量。
"""

from __future__ import annotations

import asyncio
import json
import time

from app.core.logger import webui_logger

#: 两次推送之间的最小间隔（秒）
_NOTIFY_MIN_INTERVAL = 1.0
_last_notify = 0.0


async def _broadcast_stats(count: int) -> None:
    from app.webui.ws import manager

    payload = json.dumps({"type": "stats_updated", "count": int(count), "ts": int(time.time())})
    try:
        await manager.broadcast(payload)
    except Exception as e:  # noqa: BLE001 —— 推送失败绝不影响记账
        webui_logger.debug(f"[Stats] 推送失败（已忽略）: {e}")


def notify_stats_updated(count: int = 0) -> None:
    """落库后调用：异步广播一条 `stats_updated`（同步调用、不阻塞）。"""
    global _last_notify
    now = time.monotonic()
    if now - _last_notify < _NOTIFY_MIN_INTERVAL:
        return
    _last_notify = now
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 没有事件循环（纯单元测试 / 关库阶段）：静默跳过
        return
    try:
        loop.create_task(_broadcast_stats(count))
    except Exception as e:  # noqa: BLE001
        webui_logger.debug(f"[Stats] 调度推送失败（已忽略）: {e}")


def reset_throttle() -> None:
    """测试用：清掉节流窗口。"""
    global _last_notify
    _last_notify = 0.0


__all__ = ["notify_stats_updated", "reset_throttle"]
