"""节点框架测试：NodeRunner / NodeRegistry / 出站管道（拦截、顺序、替换）。"""

from app.nodes.base import MessageContext, MessageNode, NodeRunner
from app.nodes.registry import NodeRegistry
from app.nodes.outbound import OutboundPipeline, SendNode


class _Probe(MessageNode):
    """记录是否执行、是否放行下游的探针节点。"""

    def __init__(self, name, order, calls, passthrough=True):
        self.name = name
        self.order = order
        self.calls = calls
        self.passthrough = passthrough

    async def process(self, ctx, next_):
        self.calls.append(self.name)
        if self.passthrough:
            await next_()


async def test_node_runner_orders_by_order():
    calls = []
    nodes = [_Probe("b", 200, calls), _Probe("a", 10, calls), _Probe("c", 100, calls)]
    await NodeRunner(nodes).run(MessageContext())
    assert calls == ["a", "c", "b"]


async def test_node_intercept_short_circuits():
    calls = []
    blocker = _Probe("block", 10, calls, passthrough=False)
    tail = _Probe("tail", 100, calls)
    await NodeRunner([blocker, tail]).run(MessageContext())
    assert calls == ["block"]  # tail 未被调用


async def test_node_can_branch():
    calls = []

    class Branch(MessageNode):
        name = "branch"
        order = 10

        async def process(self, ctx, next_):
            await next_()   # 分支1
            await next_()   # 分支2

    await NodeRunner([Branch(), _Probe("leaf", 100, calls)]).run(MessageContext())
    assert calls == ["leaf", "leaf"]  # 下游被执行两次


async def test_registry_insert_replace_get():
    reg = NodeRegistry(log=None)
    reg.register_inbound(_Probe("a", 10, [], passthrough=False))
    assert reg.get("a") is not None
    new = _Probe("a2", 10, [], passthrough=False)
    assert reg.replace("a", new) is True
    assert reg.get("a").name == "a2"
    assert reg.remove("a") is True
    assert reg.get("a") is None


# ---------- 出站管道 ----------

class _FakeBot:
    def __init__(self, result):
        self._result = result
        self.sent = []

    async def _direct_send(self, action, params):
        self.sent.append((action, params))
        return self._result


async def test_outbound_pipeline_passthrough():
    bot = _FakeBot({"status": "ok"})
    pipe = OutboundPipeline([SendNode()])
    resp = await pipe.run(bot, "send_group_msg", {"group_id": 1, "message": "hi"})
    assert resp == {"status": "ok"}
    assert bot.sent == [("send_group_msg", {"group_id": 1, "message": "hi"})]


async def test_outbound_pipeline_intercept():
    bot = _FakeBot({"status": "ok"})

    class BlockSend(MessageNode):
        name = "block_send"
        order = 10

        async def process(self, ctx, next_):
            pass  # 不调用 next_ → 发送被吞掉

    pipe = OutboundPipeline([BlockSend(), SendNode()])
    resp = await pipe.run(bot, "send_group_msg", {"group_id": 1})
    assert resp is None
    assert bot.sent == []  # 未真正发送


async def test_outbound_pipeline_rewrite_params():
    bot = _FakeBot({"status": "ok"})

    class Rewrite(MessageNode):
        name = "rewrite"
        order = 10

        async def process(self, ctx, next_):
            ctx.params["message"] = "[改写]" + ctx.params.get("message", "")
            await next_()

    pipe = OutboundPipeline([Rewrite(), SendNode()])
    await pipe.run(bot, "send_private_msg", {"user_id": 1, "message": "hi"})
    assert bot.sent == [("send_private_msg", {"user_id": 1, "message": "[改写]hi"})]
