"""精确定时任务管理器（替代旧 time_core 轮询广播）。

- 每个定时任务独立，精确到秒触发（monotonic deadline，不漂移）；
- 模块通过 `SCHEDULES = {"05:00:00": "handler_method"}` 声明式注册，
  模块加载时自动注册、卸载时自动注销；
- 只对真实 Bot 实例注册（bot_id 非 None），handler 通过 self.ctx.bot 发消息。
"""

from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from app.core.logger import logger

Handler = Callable[[], Awaitable[None]]


class ScheduledTask:
    """单个定时任务：每天在 time_str 指定的 HH:MM[:SS] 触发一次 handler。"""

    def __init__(self, key: str, time_str: str, handler: Handler, log=None) -> None:
        self.key = key
        self.time_str = time_str
        self.handler = handler
        self.log = log or logger
        self._task: asyncio.Task | None = None
        self.running = False
        self._cron_target: datetime | None = None  # 最近一次 cron 计算出的目标时刻（命中后复用）

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop(), name=f"sched:{self.key}")

    async def stop(self) -> None:
        self.running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    # ---------- 时间计算 ----------
    def _is_cron(self) -> bool:
        """5 字段 cron（分 时 日 月 周） vs 每日 HH:MM[:SS] 简写。"""
        return len(self.time_str.split()) >= 5

    def _parse(self) -> tuple[int, int, int]:
        parts = self.time_str.split(":")
        h = int(parts[0])
        m = int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0
        if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60):
            raise ValueError(f"非法时间: {self.time_str}")
        return h, m, s

    @staticmethod
    def _match_cron_field(field: str, value: int) -> bool:
        """匹配单个 cron 字段：* / */n / a-b / a,b,c / 精确值。"""
        field = field.strip()
        if field == "*":
            return True
        for part in field.split(","):
            part = part.strip()
            if not part:
                continue
            if "/" in part:
                base, step = part.split("/")
                try:
                    step = int(step)
                except ValueError:
                    continue
                low, high = 0, 59
                if base != "*" and "-" in base:
                    lo, hi = base.split("-")
                    low, high = int(lo), int(hi)
                elif base != "*":
                    low = high = int(base)
                if low <= value <= high and (value - low) % step == 0:
                    return True
            elif "-" in part:
                lo, hi = part.split("-")
                if int(lo) <= value <= int(hi):
                    return True
            else:
                try:
                    if int(part) == value:
                        return True
                except ValueError:
                    continue
        return False

    def _next_cron(self) -> float:
        """计算 5 字段 cron 距下次触发的秒数（缓存目标时刻，避免每次触发后全量重扫）。"""
        now = datetime.now()
        if self._cron_target is not None:
            delta = (self._cron_target - now).total_seconds()
            if delta >= 1.0:
                return delta
            self._cron_target = None  # 已过 → 重新计算

        minute, hour, dom, month, dow = self.time_str.split()[:5]
        for day_offset in range(0, 366):
            d = now + timedelta(days=day_offset)
            if not self._match_cron_field(month, d.month):
                continue
            if not self._match_cron_field(dom, d.day):
                continue
            # 周：0/7=周日 … 6=周六
            weekday = (d.weekday() + 1) % 7
            if not self._match_cron_field(dow, weekday):
                continue
            for minute_in_day in range(0, 1440):
                hh, mm = divmod(minute_in_day, 60)
                if self._match_cron_field(hour, hh) and self._match_cron_field(minute, mm):
                    target = d.replace(hour=hh, minute=mm, second=0, microsecond=0)
                    delta = (target - now).total_seconds()
                    if delta >= 1.0:
                        self._cron_target = target
                        return delta
        return 365 * 86400

    def _next(self) -> float:
        """距下次触发的秒数（monotonic）。目标不足 1s（或已过）→ 推到下一周期，避免重复触发。"""
        if self._is_cron():
            return self._next_cron()
        h, m, s = self._parse()
        now = datetime.now()
        target = now.replace(hour=h, minute=m, second=s, microsecond=0)
        if (target - now).total_seconds() < 1.0:
            target += timedelta(days=1)
        return max(0, (target - now).total_seconds())

    async def _wait(self, deadline: float) -> None:
        while self.running:
            left = deadline - _time.monotonic()
            if left <= 0:
                return
            if left > 0.01:
                await asyncio.sleep(left - 0.005)
            else:
                await asyncio.sleep(0)

    async def _loop(self) -> None:
        while self.running:
            try:
                await self._wait(_time.monotonic() + self._next())
            except ValueError as e:
                self.log.error(f"[Scheduler] {self.key} 时间解析错误: {e}")
                self.running = False
                return
            if not self.running:
                break
            #self.log.debug(f"[Scheduler] 触发 {self.key}@{self.time_str}")
            try:
                await self.handler()
            except Exception as e:
                self.log.error(f"[Scheduler] {self.key} 回调异常: {e}")


class SchedulerService:
    """定时任务管理器（进程内单例）。"""

    def __init__(self, log=None) -> None:
        self._tasks: dict[str, ScheduledTask] = {}
        self.log = log or logger

    async def register_module(self, module) -> int:
        """从模块 SCHEDULES 声明注册定时任务。仅真实 Bot 实例生效。"""
        if getattr(module, "bot_id", None) is None:
            return 0
        schedules = getattr(module, "SCHEDULES", {}) or {}
        prefix = f"{module.module_name}:{module.bot_id}"
        count = 0
        for time_str, method_name in schedules.items():
            handler = getattr(module, method_name, None)
            if handler is None:
                self.log.warning(f"[Scheduler] 模块 {module.module_name} 缺少方法 {method_name}")
                continue
            # 先停止同 key 旧任务（防止重复注册残留）
            key = f"{prefix}:{time_str}"
            old = self._tasks.pop(key, None)
            if old:
                await old.stop()
            task = ScheduledTask(key, time_str, handler, self.log)
            self._tasks[key] = task
            await task.start()
            count += 1
        #if count:
            #self.log.info(f"[Scheduler] {module.module_name}(bot {module.bot_id}) 注册 {count} 个定时任务")
        return count

    async def register(self, key: str, time_str: str, handler: Handler) -> Optional[ScheduledTask]:
        """注册任意定时任务（供模块动态时间注册；key 建议含 `<module>:<bot_id>:` 前缀以便 unload 统一清理）。"""
        old = self._tasks.pop(key, None)
        if old:
            await old.stop()
        task = ScheduledTask(key, time_str, handler, self.log)
        self._tasks[key] = task
        await task.start()
        return task

    async def unload(self, module_name: str, bot_id: Any, time_str: str | None = None) -> None:
        """注销某模块某 bot 的定时任务（可指定时间）。"""
        prefix = f"{module_name}:{bot_id}"
        if time_str:
            keys = [f"{prefix}:{time_str}"]
        else:
            keys = [k for k in self._tasks if k.startswith(prefix + ":")]
        for k in keys:
            task = self._tasks.pop(k, None)
            if task:
                await task.stop()

    async def unload_module(self, module_name: str, bot_id: Any) -> None:
        await self.unload(module_name, bot_id)

    def count(self) -> int:
        return len(self._tasks)

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            await task.stop()
        self._tasks.clear()
