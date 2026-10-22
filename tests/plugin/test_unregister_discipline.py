"""全局注册表的卸载纪律：注册过的必须在卸载时清干净。

审计发现四类"只注册、无反注册"的全局注册：

| 注册 | 缺陷 |
|---|---|
| `PROVIDERS`（provider 适配器） | 只有 `register_provider` |
| `NodeRegistry` | 有 `remove(name)`，无按归属清理 |
| `ProviderRegistry`（数据源 list/dynamic） | 只有 `register_*` |
| `SessionManager._instances` | 无 evict；且单例 key 没归一（int vs str 拿到两个实例） |

它们共同造成"热重载/换号后积累幽灵"：模块卸载了，工具清单里还有它、
下拉框还能点到它、历史写到上一个账号里。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

# ---------- PROVIDERS ----------


def test_register_and_unregister_provider_roundtrip():
    from app.agent.providers import (
        PROVIDERS,
        get_provider_class,
        register_provider,
        unregister_provider,
    )

    class _Typhoon:
        pass

    register_provider("typhoon-test", _Typhoon, aliases=("tph",))
    assert get_provider_class("typhoon-test") is _Typhoon
    assert get_provider_class("tph") is _Typhoon

    assert unregister_provider("typhoon-test") == 1
    assert "typhoon-test" not in PROVIDERS
    # 别名也要一起清掉，否则解析还会命中已删除的类型
    assert get_provider_class("tph") is not _Typhoon


def test_unregister_provider_by_owner():
    from app.agent.providers import PROVIDERS, register_provider, unregister_provider

    class _A:
        pass

    class _B:
        pass

    owner = object()
    register_provider("owner-test-a", _A, owner=owner)
    register_provider("owner-test-b", _B)  # 无 owner，不该被连带删掉

    assert unregister_provider(owner=owner) == 1
    assert "owner-test-a" not in PROVIDERS
    assert "owner-test-b" in PROVIDERS
    unregister_provider("owner-test-b")


def test_unregister_provider_requires_target():
    from app.agent.providers import unregister_provider

    with pytest.raises(KeyError):
        unregister_provider()


# ---------- NodeRegistry ----------


def test_node_registry_remove_owner():
    from app.plugin.nodes.base import MessageNode
    from app.plugin.nodes.registry import NodeRegistry

    class _Node(MessageNode):
        def __init__(self, name):
            self.name = name
            self.order = 10

        async def process(self, ctx, next_):
            await next_()

    registry = NodeRegistry()
    owner = object()
    registry.register_outbound(_Node("mine"), owner=owner)
    registry.register_inbound(_Node("inner"), owner=owner)
    registry.register_inbound(_Node("framework"))  # 无 owner

    assert registry.remove_owner(owner) == 2
    assert registry.outbound_nodes() == []
    assert [n.name for n in registry.inbound_nodes()] == ["framework"]


def test_node_registry_remove_clears_owner_bookkeeping():
    from app.plugin.nodes.base import MessageNode
    from app.plugin.nodes.registry import NodeRegistry

    class _Node(MessageNode):
        name = "x"
        order = 10

        async def process(self, ctx, next_):
            await next_()

    registry = NodeRegistry()
    owner = object()
    registry.register(_Node(), "inbound", owner=owner)
    assert registry.remove("x") is True
    assert registry.remove_owner(owner) == 0  # 已经摘掉的不会再被算一次


# ---------- ProviderRegistry（数据源） ----------


def test_data_source_registry_unregister_module():
    from app.application.provider_service import ProviderRegistry

    registry = ProviderRegistry()
    module = SimpleNamespace(module_name="demo")

    class _M:
        module_name = "demo"
        LIST_PROVIDERS = {"demo_list": "_groups"}
        DYNAMIC_PROVIDERS = {"demo_detail": "_detail"}

        def _groups(self, *a, **k):
            return []

        def _detail(self, *a, **k):
            return {}

    mod = _M()
    mod.module_name = "demo"
    assert registry.register_module(mod) == 2
    assert registry.get("demo", "demo_list") is not None

    assert registry.unregister_module(module) == 2
    # 用非内置端点名断言：`groups` 这类内置数据源在注册被摘掉后仍会回退到 _BUILTIN_LIST
    assert registry.get("demo", "demo_list") is None
    assert registry.get("demo", "demo_detail", kind="dynamic") is None


# ---------- SessionManager 单例 ----------


def test_session_manager_singleton_normalises_bot_id_type(tmp_path, monkeypatch):
    """`SessionManager(123)` 与 `SessionManager("123")` 必须是同一个实例。

    否则模块（bot_id 是 int）与框架（str）各拿一份，历史互不可见。
    """
    from app.agent.session.manager import SessionManager

    a = SessionManager(123)
    b = SessionManager("123")
    assert a is b
    assert a.bot_id == "123"


def test_session_manager_evict_removes_instance(tmp_path):
    from app.agent.session.manager import SessionManager

    first = SessionManager("999001")
    assert SessionManager.evict("999001") is True
    second = SessionManager("999001")
    assert second is not first, "evict 之后应当拿到全新实例"
    SessionManager.evict("999001")


def test_session_manager_evict_unknown_returns_false():
    from app.agent.session.manager import SessionManager

    assert SessionManager.evict("never-existed") is False
