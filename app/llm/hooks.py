"""LLM 流水线 Hook 注册表。

按 ``stage + event_type`` 保存模块注册的钩子，执行时按 ``order`` 排序。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


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
