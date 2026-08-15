"""LLM 流水线上下文与任务模型。

模块 LLM 钩子统一接收 ``LlmContext``，通过 ``ctx.llm`` / ``ctx.job``
控制暂停、继续、跳过。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.domain.bot import IBot
from app.domain.events import BaseEvent


@dataclass
class LlmContext:
    """一次 LLM 流水线处理的上下文。"""

    event: BaseEvent
    runtime: Any
    bot: IBot
    session_id: str
    user_text: str = ""
    response_text: str = ""
    response_messages: list = field(default_factory=list)
    state: dict = field(default_factory=dict)
    job: "LlmJob | None" = None

    @property
    def llm(self):
        """直接访问 event.llm，语义与模块流水线一致。"""
        return self.event.llm


@dataclass
class LlmJob:
    """一个后台 LLM 处理任务。"""

    id: str
    group_key: str
    ctx: LlmContext | None = None
    go: asyncio.Event = field(default_factory=asyncio.Event)
    skip: bool = False
    superseded: bool = False
    generation: int = 0
    module: Any = None
