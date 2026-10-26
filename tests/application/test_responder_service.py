"""响应权仲裁服务：候选集合 + 策略 + fallback + 告警（应用层集成）。"""

from __future__ import annotations

from types import SimpleNamespace

from app.application.policy_store import LegacyPolicyStore
from app.application.responder_service import ResponderService, channel_of
from app.domain.events import GroupMessageEvent
from app.plugin.responder import (
    FALLBACK_CLOSED,
    FALLBACK_OPEN,
    POLICY_ALL,
    POLICY_DELEGATE,
    ResponderPolicy,
)

# ==================== 测试替身 ====================


class _Authority:
    def __init__(self, enabled=True, group_mode="blacklist", group_list=None,
                 user_mode="blacklist", user_list=None) -> None:
        self.enabled = enabled
        self.permission = SimpleNamespace(
            group_mode=group_mode,
            group_list=list(group_list or []),
            user_mode=user_mode,
            user_list=list(user_list or []),
        )


class _Module:
    def __init__(self, name="demo", enabled=True, permission="everyone", **authority_kwargs) -> None:
        self.module_name = name
        self.authority = _Authority(enabled, **authority_kwargs)
        self.permission = permission


class _Conn:
    def __init__(self, status="connected", groups=(10,)) -> None:
        self.status = status
        self.all_group_list = list(groups)


class _Gateway:
    def __init__(self, connections: dict[str, _Conn]) -> None:
        self.connections = connections


class _ConfigService:
    def __init__(self, accounts) -> None:
        self._accounts = list(accounts)

    def get_bots(self):
        return [{"id": account} for account in self._accounts]


class _Registry:
    def __init__(self, instances: dict[tuple[str, str], _Module]) -> None:
        self._instances = instances

    def get(self, module_name, account_id=None):
        return self._instances.get((module_name, str(account_id)))


class _Runtime:
    def __init__(self, enabled=True, online=True) -> None:
        self.config = SimpleNamespace(enabled=enabled)
        self.online = online


class _AgentManager:
    def __init__(self, runtimes: dict[str, _Runtime]) -> None:
        self._runtimes = dict(runtimes)

    def runtimes(self):
        return self._runtimes

    def get_runtime(self, account_id):
        return self._runtimes.get(str(account_id))


class _PolicyStore:
    def __init__(self, policies: dict[tuple[str, str], ResponderPolicy] | None = None,
                 error: bool = False) -> None:
        self._policies = dict(policies or {})
        self._error = error

    def get(self, capability_id, channel_id):
        if self._error:
            raise RuntimeError("policy store down")
        return self._policies.get((capability_id, str(channel_id)))

    def list_for(self, capability_id):
        return [p for (cap, _ch), p in self._policies.items() if cap == capability_id]


class _Log:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.debugs: list[str] = []

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def debug(self, message: str) -> None:
        self.debugs.append(message)


def _event(account_id="acc1", group_id=10):
    return GroupMessageEvent(
        event_type="message_group", post_type="message", message_type="group",
        user_id=100, self_id=1, message_id=1, time=0, account_id=account_id,
        user=SimpleNamespace(user_id=100, role="member"),
        group=SimpleNamespace(group_id=group_id) if group_id is not None else None,
        raw={"sender": {"role": "member"}},
    )


def _service(*, accounts=("acc1", "acc2"), instances=None, conns=None,
             policies=None, runtimes=None, policy_error=False, clock=None):
    log = _Log()
    service = ResponderService(
        policy_store=_PolicyStore(policies, policy_error),
        registry=_Registry(instances or {}),
        gateway=_Gateway(conns or {}),
        config_service=_ConfigService(accounts),
        agent_manager=_AgentManager(runtimes or {}),
        log=log,
        warn_interval=300.0,
        clock=clock or (lambda: 1000.0),
    )
    return service, log


def _policy(policy=POLICY_ALL, primary=None, fallback=FALLBACK_OPEN):
    return ResponderPolicy(
        capability_id="demo", channel_id="10", policy=policy,
        primary_account_id=primary, fallback=fallback,
    )


# ==================== 候选集合 ====================


def test_channel_of_returns_group_id_or_none():
    assert channel_of(_event(group_id=123)) == "123"
    assert channel_of(_event(group_id=None)) is None


def test_candidates_require_instance_enabled_online_and_in_group():
    service, _log = _service(
        instances={
            ("demo", "acc1"): _Module(),                       # 正常
            ("demo", "acc2"): _Module(enabled=False),          # 模块被关
            # acc3 没有实例
        },
        conns={
            "acc1": _Conn(),
            "acc2": _Conn(),
            "acc3": _Conn(),                                   # 有连接但无实例
            "acc4": _Conn(status="disconnected"),              # 掉线
        },
    )
    service.config_service = _ConfigService(["acc1", "acc2", "acc3", "acc4", "acc5"])

    decision = service.decide_module("demo", _event("acc1"))

    assert decision.candidates == ("acc1",)
    assert decision.responders == ("acc1",)


def test_candidate_must_be_member_of_the_group():
    service, _log = _service(
        instances={("demo", "acc1"): _Module(), ("demo", "acc2"): _Module()},
        conns={"acc1": _Conn(groups=(10,)), "acc2": _Conn(groups=(99,))},
    )

    decision = service.decide_module("demo", _event("acc1"))

    assert decision.candidates == ("acc1",)


def test_candidate_permission_is_checked_per_account():
    """候选账号自己的黑白名单也要生效（否则会选中一个根本不会响应的服务号）。"""
    service, _log = _service(
        instances={
            ("demo", "acc1"): _Module(),
            ("demo", "acc2"): _Module(group_mode="whitelist", group_list=[]),  # 白名单为空=拒绝
        },
        conns={"acc1": _Conn(), "acc2": _Conn()},
    )

    decision = service.decide_module("demo", _event("acc1"))

    assert decision.candidates == ("acc1",)


# ==================== 策略 ====================


def test_no_policy_means_all_candidates_respond():
    service, _log = _service(
        instances={("demo", "acc1"): _Module(), ("demo", "acc2"): _Module()},
        conns={"acc1": _Conn(), "acc2": _Conn()},
    )

    decision = service.decide_module("demo", _event("acc1"))

    assert decision.policy == POLICY_ALL
    assert decision.responders == ("acc1", "acc2")
    assert decision.degraded is False


def test_delegate_primary_responds():
    service, _log = _service(
        instances={("demo", "acc1"): _Module(), ("demo", "acc2"): _Module()},
        conns={"acc1": _Conn(), "acc2": _Conn()},
        policies={("demo", "10"): _policy(POLICY_DELEGATE, primary="acc2")},
    )

    decision = service.decide_module("demo", _event("acc1"))

    assert decision.responders == ("acc2",)
    assert decision.reason == "primary"


def test_delegate_primary_disabled_falls_back_and_warns():
    """核心缺陷修复：服务号自己的模块被关 → 不能全群静默，应降级到其它候选并告警。"""
    service, log = _service(
        instances={("demo", "acc1"): _Module(), ("demo", "acc2"): _Module(enabled=False)},
        conns={"acc1": _Conn(), "acc2": _Conn()},
        policies={("demo", "10"): _policy(POLICY_DELEGATE, primary="acc2")},
    )

    decision = service.decide_module("demo", _event("acc1"))

    assert decision.candidates == ("acc1",)
    assert decision.responders == ("acc1",)
    assert decision.degraded is True
    assert decision.reason == "primary-unavailable:fallback-open"
    assert service.last_degraded is decision
    assert len(log.warnings) == 1


def test_delegate_primary_deleted_falls_back():
    service, _log = _service(
        instances={("demo", "acc1"): _Module()},
        conns={"acc1": _Conn()},
        policies={("demo", "10"): _policy(POLICY_DELEGATE, primary="ghost")},
    )

    decision = service.decide_module("demo", _event("acc1"))

    assert decision.responders == ("acc1",)
    assert decision.degraded is True


def test_delegate_closed_keeps_silence_but_is_visible():
    service, log = _service(
        instances={("demo", "acc1"): _Module(), ("demo", "acc2"): _Module(enabled=False)},
        conns={"acc1": _Conn(), "acc2": _Conn()},
        policies={("demo", "10"): _policy(POLICY_DELEGATE, primary="acc2", fallback=FALLBACK_CLOSED)},
    )

    decision = service.decide_module("demo", _event("acc1"))

    assert decision.responders == ()
    assert decision.degraded is True
    assert decision.reason == "primary-unavailable:closed"
    assert len(log.warnings) == 1


def test_degraded_warning_is_rate_limited():
    service, log = _service(
        instances={("demo", "acc1"): _Module()},
        conns={"acc1": _Conn()},
        policies={("demo", "10"): _policy(POLICY_DELEGATE, primary="ghost")},
    )
    event = _event("acc1")

    for _ in range(3):
        service.decide_module("demo", event)

    assert len(log.warnings) == 1


def test_policy_read_failure_degrades_to_all_with_warning():
    service, log = _service(
        instances={("demo", "acc1"): _Module(), ("demo", "acc2"): _Module()},
        conns={"acc1": _Conn(), "acc2": _Conn()},
        policy_error=True,
    )

    decision = service.decide_module("demo", _event("acc1"))

    assert decision.responders == ("acc1", "acc2")
    assert any("读取策略失败" in message for message in log.warnings)


# ==================== 私聊 ====================


def test_private_chat_only_concerns_current_account():
    """私聊没有"同群多账号"概念：候选只可能是本账号，群级策略不参与。"""
    service, _log = _service(
        instances={("demo", "acc1"): _Module(), ("demo", "acc2"): _Module()},
        conns={"acc1": _Conn(groups=(10,)), "acc2": _Conn(groups=(10,))},
    )

    decision = service.decide_module("demo", _event("acc1", group_id=None))

    assert decision.candidates == ("acc1",)
    assert decision.responders == ("acc1",)
    assert decision.reason == "all"


# ==================== Agent ====================


def test_agent_candidates_follow_runtime_state():
    service, _log = _service(
        runtimes={
            "acc1": _Runtime(online=True),
            "acc2": _Runtime(enabled=False, online=True),
            "acc3": _Runtime(online=False),
        },
    )
    service.config_service = _ConfigService(["acc1", "acc2", "acc3"])
    service.gateway = _Gateway({"acc1": _Conn(), "acc2": _Conn(), "acc3": _Conn()})

    decision = service.decide_agent(_event("acc1"))

    assert decision.candidates == ("acc1",)
    assert decision.responders == ("acc1",)


def test_agent_delegate_falls_back_when_primary_offline():
    service, _log = _service(
        runtimes={"acc1": _Runtime(online=True), "acc2": _Runtime(online=False)},
        policies={("agent", "10"): ResponderPolicy("agent", "10", POLICY_DELEGATE, "acc2")},
    )
    service.config_service = _ConfigService(["acc1", "acc2"])
    service.gateway = _Gateway({"acc1": _Conn(), "acc2": _Conn()})

    decision = service.decide_agent(_event("acc1"))

    assert decision.responders == ("acc1",)
    assert decision.degraded is True


# ==================== 预览 ====================


def test_preview_does_not_record_or_warn():
    service, log = _service(
        instances={("demo", "acc1"): _Module(), ("demo", "acc2"): _Module()},
        conns={"acc1": _Conn(), "acc2": _Conn()},
        policies={("demo", "10"): _policy(POLICY_DELEGATE, primary="ghost")},
    )

    decision = service.preview("demo", "10", ["acc1", "acc2"])

    assert decision.degraded is True
    assert log.warnings == []
    assert service.last_decision("demo", "10") is None


def test_last_decision_exposes_reason():
    service, _log = _service(
        instances={("demo", "acc1"): _Module(), ("demo", "acc2"): _Module()},
        conns={"acc1": _Conn(), "acc2": _Conn()},
        policies={("demo", "10"): _policy(POLICY_DELEGATE, primary="acc2")},
    )

    service.decide_module("demo", _event("acc1"))

    assert service.last_decision("demo", "10") == {
        "capability_id": "demo",
        "channel_id": "10",
        "policy": "delegate",
        "candidates": ["acc1", "acc2"],
        "responders": ["acc2"],
        "reason": "primary",
        "degraded": False,
    }


# ==================== 旧配置派生（过渡期） ====================


class _WebuiConfigService:
    def __init__(self, webui) -> None:
        self._webui = webui

    def get_webui_config(self):
        return self._webui


def test_legacy_policy_store_maps_single_service_to_delegate():
    store = LegacyPolicyStore(_WebuiConfigService({
        "single_service": {"demo": True},
        "multi_group": {"groups": {"10": {"service_bot_id": "acc2"}}},
    }))

    policy = store.get("demo", "10")

    assert policy is not None
    assert policy.policy == POLICY_DELEGATE
    assert policy.primary_account_id == "acc2"


def test_legacy_policy_store_keeps_old_no_filter_behavior():
    """旧配置：开关开、但群没指定服务号 → 不过滤（保持既有部署行为）。"""
    store = LegacyPolicyStore(_WebuiConfigService({
        "single_service": {"demo": True},
        "multi_group": {"groups": {"10": {}}},
    }))

    assert store.get("demo", "10") is None
    assert store.get("demo", "11") is None
    assert LegacyPolicyStore(_WebuiConfigService({"single_service": {}})).get("demo", "10") is None
