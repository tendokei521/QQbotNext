"""统一后台任务管理。

取代散落的 asyncio.create_task：
- 所有后台任务通过 TaskManager 创建，可追踪/取消/统计；
- 模块热卸载时级联取消其名下任务，避免孤儿任务；
- 任务异常统一落日志。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from app.core.logger import task_logger

TASKS = dict[str, list[asyncio.Task]]


@dataclass
class TaskRecord:
    """一次任务创建记录（便于诊断）。"""

    name: str
    owner: str = "system"  # 模块名 / service 名
    created_order: int = 0
    task: asyncio.Task | None = field(default=None, repr=False)


class TaskManager:
    """任务管理器单例（由容器注入，进程内唯一）。"""

    def __init__(self) -> None:
        self._tasks: dict[str, list[asyncio.Task]] = defaultdict(list)
        self._order = 0

    def create_task(
        self,
        coro: Coroutine[Any, Any, Any],
        name: str | None = None,
        owner: str = "system",
    ) -> asyncio.Task:
        """创建并登记一个后台任务。name 用于日志与取消。"""
        self._order += 1
        label = name or coro.__qualname__
        task = asyncio.create_task(coro, name=f"{owner}:{label}")
        self._tasks[owner].append(task)
        task_logger.debug(f"task + [{owner}] {label}")
        task.add_done_callback(lambda t: self._on_done(owner, t, label))
        return task

    def _on_done(self, owner: str, task: asyncio.Task, label: str) -> None:
        tasks = self._tasks.get(owner)
        if tasks and task in tasks:
            tasks.remove(task)
        if not task.cancelled():
            exc = task.exception()
            if exc:
                task_logger.error(f"task ! [{owner}] {label} 异常: {exc}")
        task_logger.debug(f"task - [{owner}] {label}")

    def cancel_owner(self, owner: str) -> int:
        """取消某 owner（如模块）名下的全部任务，返回取消数量。"""
        tasks = self._tasks.pop(owner, [])
        count = 0
        for task in tasks:
            if not task.done():
                task.cancel()
                count += 1
        if count:
            task_logger.info(f"task × [{owner}] 取消 {count} 个后台任务")
        return count

    async def cancel_owner_await(self, owner: str, timeout: float | None = None) -> int:
        """取消某 owner 名下任务并等待其真正结束。

        ``timeout``：最多等多久（None = 一直等）。超时只记警告、不再等待——
        任务已收到取消，退出路径不能被卡住；正常情况必须等它跑完 finally，
        因为那里往往还有最后一次 DB 写入（关库前必须完成）。

        **超时后任务保留登记（不 pop）**：它们还在跑，随后关库会出错——保留登记
        并打印任务名，``pending_names()`` 才能在关库前把"谁还没停"说清楚。
        """
        tasks = list(self._tasks.get(owner, []))
        if not tasks:
            self._tasks.pop(owner, None)
            return 0
        for task in tasks:
            if not task.done():
                task.cancel()
        if timeout is None:
            await asyncio.gather(*tasks, return_exceptions=True)
        else:
            _, pending = await asyncio.wait(tasks, timeout=timeout)
            if pending:
                names = ", ".join(task.get_name() for task in pending)
                task_logger.warning(
                    f"task × [{owner}] 取消超时({timeout}s)，{len(pending)} 个任务仍在运行"
                    f"（保留登记）: {names}"
                )
        remaining = [task for task in tasks if not task.done()]
        if remaining:
            self._tasks[owner] = remaining
        else:
            self._tasks.pop(owner, None)
        return len(tasks)

    async def cancel_all_await(self, timeout: float = 5.0) -> int:
        """取消全部任务并等待其结束（含 finally），返回取消数量。

        供优雅停机用：先等任务把 finally 里最后一次 DB 写做完，再关库。
        超时未结束的任务仍保留登记，调用方可在关库前用 ``pending_names()`` 告警。
        """
        owners = list(self._tasks.keys())
        total = 0
        for owner in owners:
            total += await self.cancel_owner_await(owner, timeout=timeout)
        if total:
            task_logger.info(f"task × [all] 停机取消 {total} 个后台任务")
        return total

    def pending_names(self) -> list[str]:
        """仍在运行的已登记任务名（关停超时后诊断用；已结束的不计入）。"""
        return [
            task.get_name()
            for tasks in list(self._tasks.values())
            for task in list(tasks)
            if not task.done()
        ]

    def cancel_all(self) -> int:
        owners = list(self._tasks.keys())
        return sum(self.cancel_owner(o) for o in owners)

    def count(self, owner: str | None = None) -> int:
        if owner is None:
            return sum(len(v) for v in self._tasks.values())
        return len(self._tasks.get(owner, []))

    def stats(self) -> dict:
        return {owner: len(tasks) for owner, tasks in self._tasks.items() if tasks}

    def wait(self, timeout: float = 10.0) -> Awaitable[None]:
        """等待所有登记任务结束（供优雅停机）。"""
        all_tasks = [t for ts in self._tasks.values() for t in ts]
        return asyncio.wait_for(asyncio.gather(*all_tasks, return_exceptions=True), timeout)


# 便捷入口（容器未初始化时兜底）
_default: TaskManager | None = None


def get_task_manager() -> TaskManager:
    global _default
    if _default is None:
        _default = TaskManager()
    return _default
