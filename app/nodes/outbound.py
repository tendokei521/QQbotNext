"""出站消息管道：Bot 发送消息前经过的节点链（拦截 / 改写 / 记录）。

- 由 NodeRegistry 的出站节点构成，末端是内置 SendNode（实际发送）；
- 任意节点不调用 next_() → 发送被吞掉（拦截）；
- 节点可改写 ctx.params（如加签名、敏感词替换）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.nodes.base import MessageContext, MessageNode, Next, NodeRunner


class SendNode(MessageNode):
    """出站链终端：实际发送消息。拦截它的节点不调用 next_ 即吞掉发送。"""

    name = "send"
    order = 100

    async def process(self, ctx: MessageContext, next_: Next) -> None:
        bot = ctx.bot
        if bot is None:
            ctx.state["response"] = None
            return
        # direct_send 由 BotConnection 提供（绕过 outbound_hook，避免死循环）
        direct = getattr(bot, "_direct_send", None)
        if direct is None:
            ctx.state["response"] = None
            return
        ctx.state["response"] = await direct(ctx.action, ctx.params)
        await next_()


class OutboundPipeline:
    """出站拦截链。run(bot, action, params) → 返回发送结果（被拦截则为 None）。"""

    def __init__(self, nodes: List[MessageNode]) -> None:
        self._runner = NodeRunner(nodes)

    async def run(self, bot, action: str, params: dict) -> Optional[dict]:
        ctx = MessageContext(bot=bot, action=action, params=dict(params or {}), state={})
        await self._runner.run(ctx)
        return ctx.state.get("response")
