"""定时任务模块的服务层：用框架实现 + 本模块配置组装调度器。"""

from __future__ import annotations

from typing import Any

from app.agent.policy import facade_for


def build_scheduler(runtime: Any, module_config: Any, owned_keys: Any) -> Any:
    """构造定时任务调度器（`TaskScheduler` 是机制，配置来自本模块）。"""
    from app.agent.proactivity.scheduler import TaskScheduler

    return TaskScheduler(facade_for(runtime, "schedule", module_config, owned_keys))


__all__ = ["build_scheduler"]
