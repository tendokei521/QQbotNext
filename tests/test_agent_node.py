"""AgentNode 框架级默认响应测试。"""

from app.domain.events import PrivateMessageEvent, NoticeEvent
from app.modules.base import ModulePermission
from app.modules.nodes import AgentNode
from app.nodes.base import MessageContext


class _FakeConfig:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.permission = ModulePermission()

    def get(self, key, default=None):
        return {"permission": "everyone"}.get(key, default)


class _FakeRuntime:
    def __init__(self, enabled=True):
        self.config = _FakeConfig(enabled)


class _AM:
    def __init__(self, rt):
        self.rt = rt

    def get_runtime(self, bot_id):
        return self.rt


class _Cfg:
    def get_webui_config(self):
        return {}


class _Gw:
    connections = {}


def _priv_msg():
    return PrivateMessageEvent(
        event_type="message_private", message_type="private", time=1,
        user_id=100, self_id=1, message=[], bot=None, bot_id=123,
    )


async def test_agent_node_runs_agent(monkeypatch):
    called = []

    async def fake_handle(module, event):
        called.append(event)

    monkeypatch.setattr("app.llm.handle", fake_handle)
    node = AgentNode(_AM(_FakeRuntime()), _Cfg(), _Gw())

    ctx = MessageContext(event=_priv_msg(), bot=None, state={})
    ran_next = []

    async def next_():
        ran_next.append(True)

    await node.process(ctx, next_)
    assert len(called) == 1, "私聊消息应交给 Agent"
    assert ran_next == [True], "应继续走模块链"


async def test_agent_node_skips_when_disabled(monkeypatch):
    called = []

    async def fake_handle(module, event):
        called.append(event)

    monkeypatch.setattr("app.llm.handle", fake_handle)
    node = AgentNode(_AM(_FakeRuntime(enabled=False)), _Cfg(), _Gw())

    ctx = MessageContext(event=_priv_msg(), bot=None, state={})
    ran_next = []

    async def next_():
        ran_next.append(True)

    await node.process(ctx, next_)
    assert called == [], "Agent 禁用时不应响应"
    assert ran_next == [True]


async def test_agent_node_skips_no_runtime(monkeypatch):
    called = []

    async def fake_handle(module, event):
        called.append(event)

    monkeypatch.setattr("app.llm.handle", fake_handle)
    node = AgentNode(_AM(None), _Cfg(), _Gw())  # 无运行时

    ctx = MessageContext(event=_priv_msg(), bot=None, state={})
    ran_next = []

    async def next_():
        ran_next.append(True)

    await node.process(ctx, next_)
    assert called == []
    assert ran_next == [True]


async def test_agent_node_skips_non_chat_event(monkeypatch):
    called = []

    async def fake_handle(module, event):
        called.append(event)

    monkeypatch.setattr("app.llm.handle", fake_handle)
    node = AgentNode(_AM(_FakeRuntime()), _Cfg(), _Gw())

    notice = NoticeEvent(
        event_type="notice_poke", post_type="notice", time=1,
        user_id=100, self_id=1, bot=None, bot_id=123,
    )
    ctx = MessageContext(event=notice, bot=None, state={})
    ran_next = []

    async def next_():
        ran_next.append(True)

    await node.process(ctx, next_)
    assert called == [], "非聊天事件不应交给 Agent"
    assert ran_next == [True]


async def test_agent_node_skips_when_module_stopped(monkeypatch):
    """模块调用 event.llm.stop() 后，LLM 不应响应但模块链照常继续。"""
    called = []

    async def fake_handle(module, event):
        called.append(event)

    monkeypatch.setattr("app.llm.handle", fake_handle)
    node = AgentNode(_AM(_FakeRuntime()), _Cfg(), _Gw())

    event = _priv_msg()
    event.llm.stop()  # 模块已处理，声明跳过 LLM
    ctx = MessageContext(event=event, bot=None, state={})
    ran_next = []

    async def next_():
        ran_next.append(True)

    await node.process(ctx, next_)
    assert called == [], "模块 stop 后不应交给 Agent"
    assert ran_next == [True], "仍应继续走后续节点"


async def test_agent_node_order_after_modules():
    """AgentNode 应排在模块链（Router/Permission/Invoke）之后。"""
    from app.modules.nodes import ModuleInvokeNode, ModulePermissionNode, ModuleRouterNode

    nodes = sorted(
        [AgentNode(None, None, None), ModuleRouterNode(None), ModulePermissionNode(None, None),
         ModuleInvokeNode()],
        key=lambda n: n.order,
    )
    names = [n.name for n in nodes]
    assert names == ["router", "permission", "invoke", "agent"], f"节点顺序错误: {names}"


def test_llm_gate_stop_flag():
    """event.llm.stop() 设置跳过标记。"""
    event = _priv_msg()
    assert event.llm.stopped is False
    event.llm.stop()
    assert event.llm.stopped is True
    assert getattr(event, "_llm_stop", False) is True
