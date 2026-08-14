"""统一后台任务管理。

取代散落的 asyncio.create_task：
- 所有后台任务通过 TaskManager 创建，可追踪/取消/统计；
- 模块热卸载时级联取消其名下任务，避免孤儿任务；
- 任务异常统一落日志。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Coroutine

from app.core.logger import task_logger

TASKS = dict[str, list[asyncio.Task]]


@dataclass
class TaskRecord:
    """一次任务创建记录（便于诊断）。"""

    name: str
    owner: str = "system"  # 模块名 / service 名
    created_order: int = 0
    task: "asyncio.Task" | None = field(default=None, repr=False)


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

    async def cancel_owner_await(self, owner: str) -> int:
        """取消某 owner 名下任务并等待其真正结束。"""
        tasks = self._tasks.pop(owner, [])
        if not tasks:
            return 0
        for task in tasks:
            if not task.done():
                task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

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
