"""出站消息管道：Bot 发送消息前经过的节点链（拦截 / 改写 / 记录）。

- 由 NodeRegistry 的出站节点构成，末端是内置 SendNode（实际发送）；
- 任意节点不调用 next_() → 发送被吞掉（拦截）；
- 节点可改写 ctx.params（如加签名、敏感词替换）。

## 两种构造方式（为什么需要 provider）

历史上这里在生产装配处是 ``OutboundPipeline([SendNode()])`` 的**字面量快照** ——
``NodeRegistry.register_outbound()`` 写了却没人读，插件注册出站节点静默无效。

现在支持传一个 ``provider``：每次 ``run()`` 现取节点，于是插件注册后**下一条消息即刻生效**，
既不需要重建管道，也不需要重启。

```python
OutboundPipeline(provider=node_registry.outbound_nodes)   # 生产用（插件可插拔）
OutboundPipeline([MyNode(), SendNode()])                  # 测试 / 一次性固定链
```
"""

from __future__ import annotations

from collections.abc import Callable

from app.plugin.nodes.base import MessageContext, MessageNode, Next, NodeRunner


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
        ctx.state["response"] = await direct(ctx.action, ctx.params, ctx.timeout)
        await next_()


class OutboundPipeline:
    """出站拦截链。``run(bot, action, params)`` → 返回发送结果（被拦截则 None）。

    Args:
        nodes: 固定节点快照（测试与一次性链路用）。
        provider: 无参可调用，每次 ``run()`` 时现取节点（生产用：
            插件注册出站节点后即时生效）。两者都给时 ``provider`` 优先。

    说明：``NodeRunner`` 内部按 ``order`` 升序执行，``SendNode.order = 100``，
    因此自定义节点把 ``order`` 取 < 100 即排在真正发送之前。
    """

    def __init__(
        self,
        nodes: list[MessageNode] | None = None,
        *,
        provider: Callable[[], list[MessageNode]] | None = None,
    ) -> None:
        self._nodes: list[MessageNode] = list(nodes or [])
        self._provider = provider

    def nodes(self) -> list[MessageNode]:
        """当前生效的节点（有 provider 时每次现取，并保证末端存在 SendNode）。"""
        raw = self._provider() if self._provider is not None else self._nodes
        nodes = list(raw or [])
        if not any(isinstance(n, SendNode) for n in nodes):
            # 终端必须存在：没有节点负责真正发送时，整条链等于永远吞消息
            nodes.append(SendNode())
        return nodes

    async def run(self, bot, action: str, params: dict,
                  timeout: float | int | None = None) -> dict | None:
        ctx = MessageContext(
            bot=bot, action=action, params=dict(params or {}), state={}, timeout=timeout
        )
        await NodeRunner(self.nodes()).run(ctx)
        return ctx.state.get("response")
