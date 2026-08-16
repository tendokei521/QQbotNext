"""event.stop() 全链终止机制测试（对齐 astrbot stop_event）。"""

from app.domain.events import PrivateMessageEvent
from app.modules.nodes import AgentNode, ModuleInvokeNode
from app.nodes.base import MessageContext


def _priv_msg():
    return PrivateMessageEvent(
        event_type="message_private", message_type="private", time=1,
        user_id=100, self_id=1, message=[], bot=None, bot_id=123,
    )


def test_event_stop_sets_flag():
    """event.stop() 设置终止标记。"""
    event = _priv_msg()
    assert event.stopped is False
    event.stop()
    assert event.stopped is True
    assert event._stopped is True


def test_event_stop_cancels_context():
    """event.stop() 同时短路节点链上下文（ctx.cancelled）。"""
    event = _priv_msg()
    ctx = MessageContext(event=event, bot=None, state={})
    event._ctx = ctx
    assert ctx.cancelled is False
    event.stop()
    assert ctx.cancelled is True


class _FakeModule:
    """模拟业务模块。handler 为 async (module, event)。"""

    def __init__(self, name, handler):
        self.module_name = name
        self.bot_id = 123
        self._handler = handler

    async def handle(self, event):
        await self._handler(self, event)


async def test_invoke_node_breaks_on_stop():
    """某模块调用 event.stop() 后，后续模块不再执行。"""
    called = []

    async def handler_a(module, event):
        called.append("A")
        event.stop()

    async def handler_b(module, event):
        called.append("B")

    node = ModuleInvokeNode()
    event = _priv_msg()
    ctx = MessageContext(event=event, bot=None, state={})
    event._ctx = ctx
    ctx.state["allowed"] = [_FakeModule("a", handler_a), _FakeModule("b", handler_b)]

    ran_next = []

    async def next_():
        ran_next.append(True)

    await node.process(ctx, next_)
    assert called == ["A"], f"模块 B 不应执行: {called}"
    assert ctx.cancelled is True
    assert ran_next == [True], "仍应放行到下游（NodeRunner 会因 cancelled 短路）"


async def test_invoke_node_runs_all_without_stop():
    """不调用 stop 时所有模块照常执行。"""
    called = []

    async def handler_a(module, event):
        called.append("A")

    async def handler_b(module, event):
        called.append("B")

    node = ModuleInvokeNode()
    event = _priv_msg()
    ctx = MessageContext(event=event, bot=None, state={})
    ctx.state["allowed"] = [_FakeModule("a", handler_a), _FakeModule("b", handler_b)]

    async def next_():
        pass

    await node.process(ctx, next_)
    assert called == ["A", "B"]


async def test_agent_node_skips_when_event_stopped(monkeypatch):
    """event.stop() 后 LLM 兜底不执行。"""
    from app.modules.base import ModulePermission

    called = []

    async def fake_handle(module, event):
        called.append(event)

    monkeypatch.setattr("app.llm.handle", fake_handle)

    class _FakeConfig:
        def __init__(self):
            self.enabled = True
            self.permission = ModulePermission()

        def get(self, key, default=None):
            return {"permission": "everyone"}.get(key, default)

    class _FakeRuntime:
        def __init__(self):
            self.config = _FakeConfig()

    class _AM:
        def get_runtime(self, bot_id):
            return _FakeRuntime()

    class _Cfg:
        def get_webui_config(self):
            return {}

    class _Gw:
        connections = {}

    node = AgentNode(_AM(), _Cfg(), _Gw())
    event = _priv_msg()
    event.stop()
    ctx = MessageContext(event=event, bot=None, state={})
    ran_next = []

    async def next_():
        ran_next.append(True)

    await node.process(ctx, next_)
    assert called == [], "事件已终止时 LLM 不应执行"
    assert ran_next == [True]
