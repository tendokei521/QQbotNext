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

    #: 主动性/定时路径没有事件对象，此处为 None（钩子里访问 ctx.event 需自行兜底）
    event: BaseEvent | None
    runtime: Any
    bot: IBot
    session_id: str
    user_text: str = ""
    response_text: str = ""
    response_messages: list = field(default_factory=list)
    #: 本轮组装后的请求 messages（``post_assemble`` 阶段可读、可整体改写；置空 = 跳过本轮请求）
    messages: list = field(default_factory=list)
    state: dict = field(default_factory=dict)
    job: "LlmJob | None" = None
    #: 本轮协作式取消句柄（`turn_runner.AbortScope`）：流水线在"新消息取代旧任务"时
    #: 调它的 `cancel()`，工具循环在每个 await 边界检查它。None = 本轮不可被取消。
    abort: Any = None

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
