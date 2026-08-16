"""事件分发测试：订阅路由 / bot 过滤 / 单一服务 / 权限 / time_core 广播。"""

import pytest

from app.domain.events import GroupMessageEvent
from app.modules.base import BaseModule, ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess
from app.modules.dispatcher import ModuleDispatcher


class FakeConfigService:
    def __init__(self, single_service=None, multi_group=None):
        self._single = single_service or {}
        self._multi = multi_group or {"groups": {}}

    def get_webui_config(self):
        return {"single_service": self._single, "multi_group": self._multi}

    def get_module_config(self, *a):
        return {}

    def get_module_authority(self, module, bot_id):
        return {}


class FakeRegistry:
    def __init__(self, modules):
        self._modules = modules

    def loaded(self):
        return self._modules


class FakeConn:
    def __init__(self, index, bot_id):
        self.index = index
        self.bot_id = bot_id
        self.owner_id = None
        self.status = "connected"
        self.all_group_list = [1]  # 默认在群 1


def make_module(subs, mname, bot_id, permission="member", calls=None, auth_data=None):
    _permission = permission

    class M(BaseModule):
        name = mname
        sign = mname
        description = ""
        permission = _permission
        subscribe = subs

        async def handle(self, event):
            calls.append((mname, event.event_type, event.bot_id))

    svc = FakeConfigService()

    class _S:
        def get_module_config(self, *a):
            return {}

        def get_module_authority(self, *a):
            return auth_data or {}

    s = _S()
    auth = ModuleAuthority(mname, bot_id, s)
    config = ModuleConfig(mname, bot_id, {}, s)
    ctx = ModuleContext(module_name=mname, bot_id=bot_id, config=config, authority=auth, services=ServiceAccess())
    return M(ctx)


def _event(**kw):
    defaults = dict(
        event_type="message_group", message_type="group",
        user_id=100, self_id=123, bot_id=123, bot_index=0, time=0,
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
    a = make_module(("message_group",), "a", 123, calls=calls)
    b = make_module(("notice_poke",), "b", 123, calls=calls)
    registry = FakeRegistry([a, b])
    cfg = FakeConfigService()
    gateway = type("G", (), {"connections": {0: FakeConn(0, 123)}})()
    d = ModuleDispatcher(registry=registry, config_service=cfg, gateway=gateway, log=None)
    d._calls = calls
    return d


async def test_subscribed_module_only(dispatcher):
    ev = _event()
    await dispatcher.dispatch(ev)
    assert dispatcher._calls == [("a", "message_group", 123)]


async def test_bot_id_filter(dispatcher):
    ev = _event(bot_id=999, bot_index=0)
    await dispatcher.dispatch(ev)
    assert dispatcher._calls == []


async def test_single_service_skip(dispatcher):
    """双账号同群，服务账号为另一账号（index=5）→ 当前 bot(index 0) 跳过。"""
    dispatcher.config_service._single = {"a": True}
    dispatcher.config_service._multi = {"groups": {"1": {"service_bot_index": 5}}}
    dispatcher.gateway.connections = {0: FakeConn(0, 123), 5: FakeConn(5, 999)}
    ev = _event()
    await dispatcher.dispatch(ev)
    assert dispatcher._calls == []


async def test_single_service_allow_when_service_account(dispatcher):
    """双账号同群，服务账号为当前 bot（index=0）→ 响应。"""
    dispatcher.config_service._single = {"a": True}
    dispatcher.config_service._multi = {"groups": {"1": {"service_bot_index": 0}}}
    dispatcher.gateway.connections = {0: FakeConn(0, 123), 5: FakeConn(5, 999)}
    ev = _event()
    await dispatcher.dispatch(ev)
    assert dispatcher._calls == [("a", "message_group", 123)]


async def test_single_service_not_triggered_single_bot(dispatcher):
    """单账号群：即使配置了不存在的服务账号也不触发，正常响应。"""
    dispatcher.config_service._single = {"a": True}
    dispatcher.config_service._multi = {"groups": {"1": {"service_bot_index": 5}}}  # 指向不存在的 index
    ev = _event()
    await dispatcher.dispatch(ev)
    assert dispatcher._calls == [("a", "message_group", 123)]


async def test_single_service_no_service_account(dispatcher):
    """双账号但群未配置服务账号 → 不限制，所有账号均可响应。"""
    dispatcher.config_service._single = {"a": True}
    dispatcher.config_service._multi = {"groups": {"1": {}}}  # 未配置 service_bot_index
    dispatcher.gateway.connections = {0: FakeConn(0, 123), 5: FakeConn(5, 999)}
    ev = _event()
    await dispatcher.dispatch(ev)
    assert dispatcher._calls == [("a", "message_group", 123)]


async def test_single_service_unconfigured_group(dispatcher):
    """双账号但群不在多群管理配置中 → 不触发，正常响应。"""
    dispatcher.config_service._single = {"a": True}
    dispatcher.config_service._multi = {"groups": {}}  # 群 1 未配置
    dispatcher.gateway.connections = {0: FakeConn(0, 123), 5: FakeConn(5, 999)}
    ev = _event()
    await dispatcher.dispatch(ev)
    assert dispatcher._calls == [("a", "message_group", 123)]


async def test_owner_only_module_blocked_for_member():
    calls = []
    refuse = make_module(("message_group",), "owner_m", 123, permission="owner", calls=calls)
    registry = FakeRegistry([refuse])
    d = ModuleDispatcher(registry=registry, config_service=FakeConfigService(),
                         gateway=type("G", (), {"connections": {}})())
    await d.dispatch(_event())
    assert calls == []
