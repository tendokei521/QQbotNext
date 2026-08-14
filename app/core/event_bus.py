"""类型化事件总线（框架级发布订阅）。

模块事件的分发由 app/modules/dispatcher.py 负责（含权限/启停过滤）；
此处仅承载跨切面事件：配置变更、Bot 上线/下线、模块热重载等，
供 WebUI 推送、日志联动等框架组件订阅。
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable, Type

from app.core.logger import logger

Handler = Callable[[Any], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[Type, list[Handler]] = {}

    def subscribe(self, event_cls: Type, handler: Handler) -> None:
        """订阅指定事件类型的处理器（async 函数）。"""
        if not inspect.iscoroutinefunction(handler):
            raise TypeError(f"handler 必须为 async 函数: {handler}")
        self._subscribers.setdefault(event_cls, []).append(handler)

    def unsubscribe(self, event_cls: Type, handler: Handler) -> None:
        handlers = self._subscribers.get(event_cls)
        if handlers and handler in handlers:
            handlers.remove(handler)

    async def publish(self, event: Any) -> None:
        """发布事件：依事件实际类型（含子类）分发给订阅者。"""
        cls = type(event)
        dispatched = 0
        for event_cls, handlers in self._subscribers.items():
            if not issubclass(cls, event_cls):
                continue
            for handler in list(handlers):
                try:
                    await handler(event)
                    dispatched += 1
                except Exception as e:  # 订阅者异常不影响其余订阅者
                    logger.error(f"[EventBus] {handler.__qualname__} 处理 {cls.__name__} 失败: {e}")
        return dispatched


# 进程内默认实例（框架组件可直接订阅）
event_bus = EventBus()


class ConfigChangedEvent:
    """配置变更事件。scope: 'bots' | 'modules' | 'webui' | 'module_config' | 'authority'"""

    def __init__(self, scope: str, payload: Any = None) -> None:
        self.scope = scope
        self.payload = payload


class BotLifecycleEvent:
    """Bot 连接状态变化。state: 'connected' | 'disconnected' | 'error'"""

    def __init__(self, bot_id: int | None, bot_index: int, state: str, detail: str = "") -> None:
        self.bot_id = bot_id
        self.bot_index = bot_index
        self.state = state
        self.detail = detail


class ModulesReloadedEvent:
    def __init__(self, bot_id: int | None) -> None:
        self.bot_id = bot_id
