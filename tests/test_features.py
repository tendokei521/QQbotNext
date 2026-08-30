"""FeatureRegistry 测试：能力接管、多租约、恢复、插件声明式 supersedes。"""

from app.modules.base import BaseModule, ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess
from app.modules.features import (
    ConfigToggleFeature,
    FeatureRegistry,
    ProactiveFeatureController,
)


class _FakeProactive:
    def __init__(self) -> None:
        self.stopped = 0
        self.resumed = 0

    def stop(self) -> None:
        self.stopped += 1

    def resume(self) -> None:
        self.resumed += 1


class _FakeScheduler:
    def __init__(self) -> None:
        self.stopped = 0
        self.resumed = 0

    def stop(self) -> None:
        self.stopped += 1

    def resume(self) -> None:
        self.resumed += 1


class _FakeConfig:
    def __init__(self, data=None, enabled=True) -> None:
        self._data = dict(data or {})
        self.enabled = enabled

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value, auto_save=True) -> None:
        self._data[key] = value

    def set_enabled(self, value) -> None:
        self.enabled = bool(value)


class _FakeRuntime:
    def __init__(self, data=None, enabled=True) -> None:
        self.config = _FakeConfig(data, enabled)
        self.proactive = _FakeProactive()
        self.scheduler = _FakeScheduler()


class _FakeAgentManager:
    def __init__(self, runtime) -> None:
        self._runtime = runtime

    def get_runtime(self, bot_id):
        return self._runtime


class _DummyModule(BaseModule):
    name = "Dummy"
    sign = "Dummy"
    subscribe = ()
    supersedes = ("proactive",)


class _FakeAuthorityService:
    def get_module_authority(self, module, bot_id):
        return {}

    def set_module_authority(self, module, bot_id, authority):
        return None


def _make_module(registry=None, features=None):
    cfg = _FakeConfig({"x": 1})
    auth = ModuleAuthority("dummy", 123, _FakeAuthorityService())
    services = ServiceAccess(features=features)
    ctx = ModuleContext(module_name="dummy", bot_id=123, config=cfg, authority=auth, services=services)
    return _DummyModule(ctx)


def test_suppress_release_restores():
    runtime = _FakeRuntime({"proactive_friend_enable": True, "proactive_group_enable": False})
    reg = FeatureRegistry()
    reg.register(ProactiveFeatureController(_FakeAgentManager(runtime)))

    owner = _make_module()
    reg.suppress("proactive", owner, 123)
    assert runtime.config.get("proactive_friend_enable") is False
    assert runtime.config.get("proactive_group_enable") is False
    assert runtime.proactive.stopped == 1
    assert reg.is_suppressed("proactive", 123)

    reg.release("proactive", owner, 123)
    assert runtime.config.get("proactive_friend_enable") is True
    assert runtime.config.get("proactive_group_enable") is False
    assert runtime.proactive.resumed == 1
    assert reg.is_suppressed("proactive", 123) is False


def test_multiple_owners_last_release_restores_once():
    runtime = _FakeRuntime({"proactive_friend_enable": True, "proactive_group_enable": True})
    reg = FeatureRegistry()
    reg.register(ProactiveFeatureController(_FakeAgentManager(runtime)))
    owner_a = _make_module()
    owner_b = _make_module()

    reg.suppress("proactive", owner_a, 123)
    reg.suppress("proactive", owner_b, 123)
    assert runtime.config.get("proactive_friend_enable") is False

    # A 释放后 B 仍持有租约，不应恢复
    reg.release("proactive", owner_a, 123)
    assert runtime.config.get("proactive_friend_enable") is False
    assert runtime.proactive.resumed == 0

    # B 释放后最后一个租约离开，恢复
    reg.release("proactive", owner_b, 123)
    assert runtime.config.get("proactive_friend_enable") is True
    assert runtime.config.get("proactive_group_enable") is True
    assert runtime.proactive.resumed == 1


def test_acquire_module_declares_supersedes():
    runtime = _FakeRuntime({"proactive_friend_enable": True, "proactive_group_enable": False})
    reg = FeatureRegistry()
    reg.register(ProactiveFeatureController(_FakeAgentManager(runtime)))
    module = _make_module(features=reg)

    assert reg.acquire_module(module) == 1
    assert runtime.config.get("proactive_friend_enable") is False
    assert reg.is_suppressed("proactive", 123)

    assert reg.release_module(module) == 1
    assert runtime.config.get("proactive_friend_enable") is True


def test_acquire_module_skips_disabled_module():
    runtime = _FakeRuntime({"proactive_friend_enable": True, "proactive_group_enable": False})
    reg = FeatureRegistry()
    reg.register(ProactiveFeatureController(_FakeAgentManager(runtime)))
    module = _make_module(features=reg)
    module.authority.set_enabled(False)

    assert reg.acquire_module(module) == 0
    assert runtime.config.get("proactive_friend_enable") is True
    assert reg.is_suppressed("proactive", 123) is False


def test_release_owner_releases_all_features():
    runtime = _FakeRuntime({"some_enable": True, "other_enable": True})
    am = _FakeAgentManager(runtime)
    reg = FeatureRegistry()
    reg.register(ConfigToggleFeature(am, feature_id="some", label="Some", keys=("some_enable",)))
    reg.register(ConfigToggleFeature(am, feature_id="other", label="Other", keys=("other_enable",)))
    module = _make_module(features=reg)

    reg.suppress("some", module, 123)
    reg.suppress("other", module, 123)
    assert reg.release_owner(module) == 2
    assert runtime.config.get("some_enable") is True
    assert runtime.config.get("other_enable") is True


def test_status_contains_owners():
    runtime = _FakeRuntime({"some_enable": True})
    reg = FeatureRegistry()
    reg.register(ConfigToggleFeature(_FakeAgentManager(runtime), feature_id="some", label="Some", keys=("some_enable",)))
    module = _make_module(features=reg)
    reg.suppress("some", module, 123)

    status = reg.query("some", 123)
    assert status["suppressed"] is True
    assert status["owners"][0]["module"] == "dummy"
    assert len(reg.status(123)) == 1
