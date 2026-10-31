"""llm_trigger 的服务层：主动消息与定时任务的构建函数（合并自两个旧 service.py）。

- 主动消息 = 订阅事实 + 用基础接口干活：框架只广播 `message_observed` /
  `bot_sent`，本模块用 `spawn` 起计时器、`llm_turn` 跑一轮、`send_to` 发出去；
- 定时任务 = 到点用基础接口跑一轮并发出：`TaskScheduler` 的计时与持久化是机制
  （已随本模块搬进 `src/schedule`），"说话"的动作走模块的 `llm_turn` + `send_to`。

签名与返回类型与旧模块保持一致。
"""

from __future__ import annotations

from typing import Any


def build_manager(module: Any) -> Any:
    """构造主动消息管理器。

    `module` 就是**插件模块实例**（`BaseModule`）：它有 `config`（本模块配置）、
    `bot_id`、`llm_turn` / `send_to` / `spawn` 等基础接口，以及 `ctx.services`。
    """
    from .src.proactive.manager import ProactiveManager

    return ProactiveManager(module)


def build_scheduler(module: Any) -> Any:
    """构造定时任务调度器；`module` 是插件模块实例（带基础接口）。"""
    from .src.schedule.scheduler import TaskScheduler

    return TaskScheduler(module)


class ScheduleToolSource:
    """工具源：`schedule` 工具（到点让 Bot 自然开口）。"""

    def __init__(self, module: Any) -> None:
        self.module = module
        self.name = "schedule"
        self.order = 70
        self.owner = module

    async def collect(self, scope: Any) -> list:
        runtime = getattr(scope, "runtime", None)
        if runtime is None or not getattr(self.module, "enabled", False):
            return []
        from .src.schedule.schedule_tool import build_schedule_tool

        return [
            build_schedule_tool(
                runtime,
                getattr(scope, "session_id", ""),
                bool(getattr(scope, "is_private", False)),
            )
        ]
