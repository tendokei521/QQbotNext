"""LLM 流水线 Hook 注册表。

按 ``stage + event_type`` 保存模块注册的钩子，执行时按 ``order`` 排序。
同时提供 LLM 工具调用后钩子（``ToolCallHookRegistry``）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.logger import logger


@dataclass
class RegisteredLlmHook:
    stage: str
    event_type: str
    order: int
    handler: Callable
    module: Any = None


class LlmHookRegistry:
    def __init__(self) -> None:
        self._hooks: list[RegisteredLlmHook] = []

    def register(
        self,
        *,
        stage: str,
        event_type: str = "*",
        order: int = 100,
        handler: Callable,
        module: Any = None,
    ) -> None:
        self._hooks.append(
            RegisteredLlmHook(
                stage=stage,
                event_type=event_type,
                order=order,
                handler=handler,
                module=module,
            )
        )

    def unregister_module(self, module: Any) -> None:
        self._hooks = [h for h in self._hooks if h.module is not module]

    def get(self, stage: str, event_type: str) -> list[RegisteredLlmHook]:
        hooks = [
            h
            for h in self._hooks
            if h.stage == stage and (h.event_type == "*" or h.event_type == event_type)
        ]
        return sorted(hooks, key=lambda h: h.order)

    def all(self) -> list[RegisteredLlmHook]:
        return list(self._hooks)


@dataclass
class ToolCallContext:
    """一次 LLM 工具调用后的上下文。"""

    name: str
    args: dict
    result: str
    success: bool
    duration_ms: float
    tool_ctx: Any = None
    module: Any = None
    bot_id: Any = None
    event_type: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class RegisteredToolCallHook:
    event_type: str
    order: int
    handler: Callable
    module: Any = None


class ToolCallHookRegistry:
    """LLM 工具调用后钩子注册表，挂在 AgentRuntime 上。"""

    def __init__(self, log=None) -> None:
        self._hooks: list[RegisteredToolCallHook] = []
        self.log = log or logger

    def register(
        self,
        *,
        event_type: str = "*",
        order: int = 100,
        handler: Callable,
        module: Any = None,
    ) -> None:
        self._hooks.append(
            RegisteredToolCallHook(
                event_type=event_type,
                order=order,
                handler=handler,
                module=module,
            )
        )

    def unregister_module(self, module: Any) -> None:
        self._hooks = [h for h in self._hooks if h.module is not module]

    def match(self, event_type: str) -> list[RegisteredToolCallHook]:
        hooks = [
            h for h in self._hooks
            if h.event_type == "*" or h.event_type == event_type
        ]
        return sorted(hooks, key=lambda h: h.order)

    async def run(self, ctx: ToolCallContext) -> None:
        for hook in self.match(ctx.event_type):
            try:
                await hook.handler(ctx)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log.add_info(f"#{ctx.bot_id}").exception(
                    f"[ToolCallHook] {getattr(hook.module, 'module_name', '?')} 处理异常: {e}"
                )