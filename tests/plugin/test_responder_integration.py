"""响应权仲裁接入节点链的集成测试。

验证 PermissionNode 用 ResponderService 做跨账号判定：
- delegate：非服务账号的模块实例不执行，服务账号执行；
- 服务账号被关模块：fallback 到其它候选并告警（不再全群静默）；
- 未配置策略：各账号照常响应。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.application.responder_service import ResponderService
from app.domain.events import GroupMessageEvent
from app.plugin.dispatcher import ModuleDispatcher
from app.plugin.nodes.inbound import ModuleInvokeNode, ModulePermissionNode, ModuleRouterNode
from app.plugin.nodes.registry import NodeRegistry
from app.plugin.responder import POLICY_DELEGATE, ResponderPolicy


class _Authority:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.permission = SimpleNamespace(
            group_mode="blacklist", group_list=[], user_mode="blacklist", user_list=[]
        )


class _Module:
    def __init__(self, account_id, calls, enabled=True):
        self.module_name = "demo"
        self.name = "Demo"
        self.bot_id = account_id
        self.subscribe = ("message_group",)
        self.authority = _Authority(enabled)
        self.permission = "everyone"
        self._calls = calls
        self._enabled = enabled

    async def handle(self, event):
        self._calls.append((self.bot_id, event.account_id))


class _Conn:
    def __init__(self, status="connected", groups=(10,)):
        self.status = status
        self.all_group_list = list(groups)


class _Gateway:
    def __init__(self, connections):
        self.connections = connections


class _ConfigService:
    def __init__(self, accounts):
        self._accounts = list(accounts)

    def get_bots(self):
        return [{"id": account} for account in self._accounts]

    def get_module_config(self, module, bot_id=None):
        return {}

    def get_webui_config(self):
        return {"single_service": {}, "multi_group": {"groups": {}}}


class _Registry:
    def __init__(self, modules):
        self._modules = list(modules)
        self._by_account = {module.bot_id: module for module in modules}

    def loaded(self):
        return list(self._modules)

    def get(self, module_name, account_id=None):
        module = self._by_account.get(str(account_id))
        return module if module is not None and module.module_name == module_name else None


class _PolicyStore:
    def __init__(self, policies=None):
        self._policies = policies or {}

    def get(self, capability_id, channel_id):
        return self._policies.get((capability_id, str(channel_id)))

    def list_for(self, capability_id):
        return []


class _Log:
    def __init__(self):
        self.warnings = []

    def warning(self, message):
        self.warnings.append(message)

    def debug(self, message):
        pass

    def exception(self, message):  # pragma: no cover - 节点链不应抛异常
        raise AssertionError(message)


def _event(account_id, group_id=10):
    return GroupMessageEvent(
        event_type="message_group", post_type="message", message_type="group",
        user_id=100, self_id=1, message_id=1, time=0, account_id=account_id,
        user=SimpleNamespace(user_id=100, role="member"),
        group=SimpleNamespace(group_id=group_id),
        raw={"sender": {"role": "member"}},
    )


def _dispatcher(calls, *, accounts=("acc1", "acc2"), conns=None,
                policies=None, modules=None):
    modules = modules or [
        _Module(account, calls) for account in accounts
    ]
    log = _Log()
    responder = ResponderService(
        policy_store=_PolicyStore(policies),
        registry=_Registry(modules),
        gateway=_Gateway(conns or {account: _Conn() for account in accounts}),
        config_service=_ConfigService(accounts),
        agent_manager=None,
        log=log,
    )
    nodes = NodeRegistry()
    nodes.register(ModuleRouterNode(_Registry(modules), log))
    nodes.register(
        ModulePermissionNode(
            _ConfigService(accounts), _Gateway(conns or {a: _Conn() for a in accounts}),
            log, responder=responder, registry=_Registry(modules),
        )
    )
    nodes.register(ModuleInvokeNode(log))
    return ModuleDispatcher(registry=_Registry(modules), config_service=_ConfigService(accounts),
                            gateway=_Gateway({}), node_registry=nodes), log


async def test_delegate_skips_non_service_account():
    calls: list[tuple[str, str]] = []
    dispatcher, _log = _dispatcher(
        calls,
        policies={("demo", "10"): ResponderPolicy("demo", "10", POLICY_DELEGATE, "acc1")},
    )

    await dispatcher.dispatch(_event("acc2"))

    assert calls == []


async def test_delegate_lets_service_account_respond():
    calls: list[tuple[str, str]] = []
    dispatcher, _log = _dispatcher(
        calls,
        policies={("demo", "10"): ResponderPolicy("demo", "10", POLICY_DELEGATE, "acc1")},
    )

    await dispatcher.dispatch(_event("acc1"))

    assert calls == [("acc1", "acc1")]


async def test_no_policy_keeps_per_account_behavior():
    calls: list[tuple[str, str]] = []
    dispatcher, _log = _dispatcher(calls)

    await dispatcher.dispatch(_event("acc2"))

    assert calls == [("acc2", "acc2")]


async def test_disabled_service_account_falls_back_instead_of_silence():
    """回归：服务账号模块被关时，旧实现会让全群静默；新实现降级到其它候选。"""
    calls: list[tuple[str, str]] = []
    accounts = ("acc1", "acc2")
    modules = [_Module("acc1", calls), _Module("acc2", calls, enabled=False)]
    dispatcher, log = _dispatcher(
        calls,
        accounts=accounts,
        modules=modules,
        policies={("demo", "10"): ResponderPolicy("demo", "10", POLICY_DELEGATE, "acc2")},
    )

    await dispatcher.dispatch(_event("acc1"))

    assert calls == [("acc1", "acc1")]
    assert len(log.warnings) == 1
    assert "primary-unavailable:fallback-open" in log.warnings[0]


async def test_closed_fallback_is_still_visible():
    calls: list[tuple[str, str]] = []
    modules = [_Module("acc1", calls), _Module("acc2", calls, enabled=False)]
    dispatcher, log = _dispatcher(
        calls,
        modules=modules,
        policies={
            ("demo", "10"): ResponderPolicy(
                "demo", "10", POLICY_DELEGATE, "acc2", fallback="closed"
            )
        },
    )

    await dispatcher.dispatch(_event("acc1"))

    assert calls == []
    assert len(log.warnings) == 1
    assert "primary-unavailable:closed" in log.warnings[0]
