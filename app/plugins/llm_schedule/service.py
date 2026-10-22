"""llm_schedule 的服务层：定时任务 = 用基础接口跑一轮并发出。

`TaskScheduler` 的计时与持久化是机制（已搬进本模块），
真正"说话"的动作走模块的 `llm_turn` + `send_to`。
"""

from __future__ import annotations

from typing import Any


def build_scheduler(module: Any) -> Any:
    """构造定时任务调度器；`module` 是插件模块实例（带基础接口）。"""
    from app.plugins.llm_schedule.scheduler import TaskScheduler

    return TaskScheduler(module)
