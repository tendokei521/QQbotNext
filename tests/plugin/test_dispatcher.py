"""事件分发测试：订阅路由 / 账号过滤 / 单一服务 / 权限 / time_core 广播。

账号身份自 v4 起是 **account_id**（字符串，用户设定）：
- 事件用 `event.account_id` 定位账号；
- 模块实例按 `module.bot_id`（值即 account_id）归属；
- 单一服务用 `service_bot_id`（account_id），不再是数组下标。
"""

import pytest

from app.domain.events import GroupMessageEvent
from app.plugin.base import BaseModule, ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess
from app.plugin.dispatcher import ModuleDispatcher

ACC = "acc0"
ACC2 = "acc5"


class FakeConfigService:
    def __init__(self, single_service=None, multi_group=None, agent_config=None):
        self._single = single_service or {}
        self._multi = multi_group or {"groups": {}}
        self._agent_config = agent_config or {}

    def get_webui_config(self):
        return {"single_service": self._single, "multi_group": self._multi}

    def get_module_config(self, module, bot_id=None):
        # 只有 Agent 配置里带名册（agent_owner_list / agent_manager_list）
        return self._agent_config if module == "agent" else {}

    def get_module_authority(self, module, bot_id):
        return {}


class FakeRegistry:
    def __init__(self, modules):
        self._modules = modules

    def loaded(self):
        return self._modules


class FakeConn:
    def __init__(self, account_id, bot_id):
        self.account_id = account_id
        self.account_name = f"账号{account_id}"
        self.bot_id = bot_id
        self.owner_id = None
        self.status = "connected"
        self.all_group_list = [1]  # 默认在群 1


def make_module(subs, mname, account_id, permission="member", calls=None, auth_data=None,
                single_service=True):
    """造一个分发用模块实例。

    ``single_service`` 对应 ``BaseModule.supports_single_service``：单一服务是声明制
    能力，默认 True 让既有的 single-service 用例断言「生效」路径，另有专门用例断言
    未声明者不受影响。
    """
    _permission = permission
    _single_service = single_service

    class M(BaseModule):
        name = mname
        sign = mname
        description = ""
        permission = _permission
        subscribe = subs
        supports_single_service = _single_service

        async def handle(self, event):
            calls.append((mname, event.event_type, event.account_id))

    class _S:
        def get_module_config(self, *a):
            return {}

        def get_module_authority(self, *a):
            return auth_data or {}

    s = _S()
    auth = ModuleAuthority(mname, account_id, s)
    config = ModuleConfig(mname, account_id, {}, s)
    ctx = ModuleContext(module_name=mname, bot_id=account_id, config=config, authority=auth,
                        services=ServiceAccess())
    return M(ctx)


def _event(**kw):
    defaults = dict(
        event_type="message_group", message_type="group",
        user_id=100, self_id=123, bot_id=123, account_id=ACC, time=0,
        user=type("U", (), {"user_id": 100, "role": "member"})(),
        group=type("G", (), {"group_id": 1})(),
        message=[{"type": "text", "data": {"text": "hi"}}],
        raw={"sender": {"role": "member"}},
    )
    defaults.update(kw)
    return GroupMessageEvent(**defaults)


@pytest.fixture
def dispatcher():
    calls = []
    a = make_module(("message_group",), "a", ACC, calls=calls)
    b = make_module(("notice_poke",), "b", ACC, calls=calls)
    registry = FakeRegistry([a, b])
    cfg = FakeConfigService()
    gateway = type("G", (), {"connections": {ACC: FakeConn(ACC, 123)}})()
    d = ModuleDispatcher(registry=registry, config_service=cfg, gateway=gateway, log=None)
    d._calls = calls
    return d


async def test_subscribed_module_only(dispatcher):
    ev = _event()
    await dispatcher.dispatch(ev)
    assert dispatcher._calls == [("a", "message_group", ACC)]


async def test_account_id_filter(dispatcher):
    """事件来自别的账号 → 不属于该模块实例，不得派发。"""
    ev = _event(account_id="other")
    await dispatcher.dispatch(ev)
    assert dispatcher._calls == []


async def test_single_service_skip(dispatcher):
    """双账号同群，服务账号为另一账号 → 当前账号跳过。"""
    dispatcher.config_service._single = {"a": True}
    dispatcher.config_service._multi = {"groups": {"1": {"service_bot_id": ACC2}}}
    dispatcher.gateway.connections = {ACC: FakeConn(ACC, 123), ACC2: FakeConn(ACC2, 999)}
    ev = _event()
    await dispatcher.dispatch(ev)
    assert dispatcher._calls == []


async def test_single_service_allow_when_service_account(dispatcher):
    """双账号同群，服务账号为当前账号 → 响应。"""
    dispatcher.config_service._single = {"a": True}
    dispatcher.config_service._multi = {"groups": {"1": {"service_bot_id": ACC}}}
    dispatcher.gateway.connections = {ACC: FakeConn(ACC, 123), ACC2: FakeConn(ACC2, 999)}
    ev = _event()
    await dispatcher.dispatch(ev)
    assert dispatcher._calls == [("a", "message_group", ACC)]


async def test_single_service_not_triggered_single_bot(dispatcher):
    """单账号群：即使配置了不存在的服务账号也不触发，正常响应。"""
    dispatcher.config_service._single = {"a": True}
    dispatcher.config_service._multi = {"groups": {"1": {"service_bot_id": "ghost"}}}
    ev = _event()
    await dispatcher.dispatch(ev)
    assert dispatcher._calls == [("a", "message_group", ACC)]


async def test_single_service_no_service_account(dispatcher):
    """双账号但群未配置服务账号 → 不限制，所有账号均可响应。"""
    dispatcher.config_service._single = {"a": True}
    dispatcher.config_service._multi = {"groups": {"1": {}}}  # 未配置 service_bot_id
    dispatcher.gateway.connections = {ACC: FakeConn(ACC, 123), ACC2: FakeConn(ACC2, 999)}
    ev = _event()
    await dispatcher.dispatch(ev)
    assert dispatcher._calls == [("a", "message_group", ACC)]


async def test_single_service_unconfigured_group(dispatcher):
    """双账号但群不在多群管理配置中 → 不触发，正常响应。"""
    dispatcher.config_service._single = {"a": True}
    dispatcher.config_service._multi = {"groups": {}}  # 群 1 未配置
    dispatcher.gateway.connections = {ACC: FakeConn(ACC, 123), ACC2: FakeConn(ACC2, 999)}
    ev = _event()
    await dispatcher.dispatch(ev)
    assert dispatcher._calls == [("a", "message_group", ACC)]


async def test_single_service_ignored_for_undeclared_module():
    """未声明 supports_single_service 的模块：配置里残留 true 也不得被跳过。"""
    calls = []
    mod = make_module(("message_group",), "plain", ACC, calls=calls, single_service=False)
    registry = FakeRegistry([mod])
    cfg = FakeConfigService(single_service={"plain": True},
                            multi_group={"groups": {"1": {"service_bot_id": ACC2}}})
    gateway = type("G", (), {"connections": {ACC: FakeConn(ACC, 123), ACC2: FakeConn(ACC2, 999)}})()
    d = ModuleDispatcher(registry=registry, config_service=cfg, gateway=gateway, log=None)
    await d.dispatch(_event())
    assert calls == [("plain", "message_group", ACC)]


async def test_single_service_key_override():
    """single_service_key 覆写配置键（虚拟模块用它对齐 WebUI 存的键）。"""
    calls = []
    mod = make_module(("message_group",), "engine", ACC, calls=calls)
    mod.module_name = "engine_internal"
    mod.single_service_key = "engine"
    registry = FakeRegistry([mod])
    cfg = FakeConfigService(single_service={"engine": True},
                            multi_group={"groups": {"1": {"service_bot_id": ACC2}}})
    gateway = type("G", (), {"connections": {ACC: FakeConn(ACC, 123), ACC2: FakeConn(ACC2, 999)}})()
    d = ModuleDispatcher(registry=registry, config_service=cfg, gateway=gateway, log=None)
    await d.dispatch(_event())
    assert calls == []


async def test_owner_only_module_blocked_for_member():
    calls = []
    refuse = make_module(("message_group",), "owner_m", ACC, permission="owner", calls=calls)
    registry = FakeRegistry([refuse])
    d = ModuleDispatcher(registry=registry, config_service=FakeConfigService(),
                         gateway=type("G", (), {"connections": {}})())
    await d.dispatch(_event())
    assert calls == []


# ---------- 名册身份（拥有者 / 管理者）必须对模块生效 ----------
# 起因：ModulePermissionNode 此前调 compute_event_permission(event) 时不传名册，
# 而 AgentNode 传了 → permission="owner"/"manager" 的模块在本机（owner_id 未配置、
# 只靠 agent_owner_list）永远被静默拦掉，表现为"模块不响应、LLM 却出来搭话"。

async def test_owner_permission_passes_for_roster_owner():
    calls = []
    mod = make_module(("message_group",), "owner_m", ACC, permission="owner", calls=calls)
    cfg = FakeConfigService(agent_config={"agent_owner_list": ["100"]})
    d = ModuleDispatcher(registry=FakeRegistry([mod]), config_service=cfg,
                         gateway=type("G", (), {"connections": {}})())
    await d.dispatch(_event())
    assert calls == [("owner_m", "message_group", ACC)]


async def test_manager_permission_passes_for_roster_manager():
    calls = []
    mod = make_module(("message_group",), "mgr_m", ACC, permission="manager", calls=calls)
    cfg = FakeConfigService(agent_config={"agent_manager_list": ["100"]})
    d = ModuleDispatcher(registry=FakeRegistry([mod]), config_service=cfg,
                         gateway=type("G", (), {"connections": {}})())
    await d.dispatch(_event())
    assert calls == [("mgr_m", "message_group", ACC)]


async def test_owner_permission_ignores_other_users():
    """名册里没有这个人 → 仍然拦住（不是"名册一配就全放行"）。"""
    calls = []
    mod = make_module(("message_group",), "owner_m", ACC, permission="owner", calls=calls)
    cfg = FakeConfigService(agent_config={"agent_owner_list": ["999"]})
    d = ModuleDispatcher(registry=FakeRegistry([mod]), config_service=cfg,
                         gateway=type("G", (), {"connections": {}})())
    await d.dispatch(_event())
    assert calls == []


async def test_rosters_prefer_agent_manager_runtime():
    """注入 agent_manager 时用运行时的配置（与 AgentNode 同源）。"""
    from types import SimpleNamespace

    calls = []
    mod = make_module(("message_group",), "owner_m", ACC, permission="owner", calls=calls)

    class FakeManager:
        def get_runtime(self, account_id):
            return SimpleNamespace(config={"agent_owner_list": ["100"]})

    d = ModuleDispatcher(registry=FakeRegistry([mod]), config_service=FakeConfigService(),
                         gateway=type("G", (), {"connections": {}})(), agent_manager=FakeManager())
    await d.dispatch(_event())
    assert calls == [("owner_m", "message_group", ACC)]
