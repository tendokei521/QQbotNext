"""消息节点核心概念。

对应 Linux「一切皆文件」：框架只认一种消息处理概念 `MessageNode`。
任意会触碰消息的东西（权限、限流、业务模块、发送拦截）都实现同一个
`process(ctx, next_)` 接口，因此可以自由插入、替换、包装、递归组合。

- MessageContext：节点间传递的统一载体（入站 event / 出站 action+params / 共享 state）
- MessageNode：一个节点。不调用 next_ = 短路拦截；调用多次 = 分支
- NodeRunner：按 order 排序执行一条节点链
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.domain.bot import IBot
from app.domain.events import BaseEvent

Next = Callable[[], Awaitable[None]]


@dataclass
class MessageContext:
    """节点链的共享上下文。入站与出站复用同一结构。"""

    event: Optional[BaseEvent] = None
    bot: Optional[IBot] = None
    state: Dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False
    # 出站用：要执行的动作与参数
    action: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


class MessageNode(ABC):
    """一切皆节点：任何处理消息的组件实现本接口。"""

    name: str = ""
    order: int = 100

    @abstractmethod
    async def process(self, ctx: MessageContext, next_: Next) -> None:
        """处理消息。调用 next_() 放行到下游；不调用 = 拦截/短路。"""
        raise NotImplementedError


class NodeRunner:
    """按 order 升序执行一条节点链。"""

    def __init__(self, nodes: List[MessageNode]) -> None:
        self._nodes = sorted(nodes, key=lambda n: n.order)

    @property
    def nodes(self) -> List[MessageNode]:
        return list(self._nodes)

    async def run(self, ctx: MessageContext) -> None:
        await self._run(0, ctx)

    async def _run(self, index: int, ctx: MessageContext) -> None:
        if ctx.cancelled or index >= len(self._nodes):
            return
        node = self._nodes[index]

        async def next_() -> None:
            await self._run(index + 1, ctx)

        await node.process(ctx, next_)
