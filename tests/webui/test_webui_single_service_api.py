"""``POST /api/webui/single-service``：单一服务是**声明制**能力。

规则（`BaseModule.supports_single_service`）：

- 声明支持 → 接受写入；
- 已加载但未声明 → 丢弃 + 在 `rejected` 里回报（不静默保存一个永不生效的 true）；
- 未加载 / 查不到 → 原样保留（避免热重载或加载失败时误删配置）；
- 框架级虚拟模块 `agent` → 恒接受（它没有 `BaseModule` 实例，走代理）。
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.webui.api.deps import get_module_registry
from app.webui.app import create_app


class _FakeModule:
    def __init__(self, name: str, declared: bool) -> None:
        self.module_name = name
        self.supports_single_service = declared


class _FakeRegistry:
    """只实现 `get(module_name)`：测试只关心「能否回答是否声明支持」。"""

    def __init__(self, modules: dict[str, _FakeModule]) -> None:
        self._modules = modules

    def get(self, module_name, bot_id=None):
        return self._modules.get(module_name)


@pytest.fixture
def client(container):
    app = create_app(container)
    app.dependency_overrides[get_module_registry] = lambda: _FakeRegistry({
        "declared_mod": _FakeModule("declared_mod", True),
        "plain_mod": _FakeModule("plain_mod", False),
    })
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_module_registry, None)


def test_declared_module_accepted(client, config_service):
    resp = client.post("/api/webui/single-service",
                       json={"single_service": {"declared_mod": True}})

    assert resp.status_code == 200
    assert resp.json()["rejected"] == []
    assert config_service.get_webui_config()["single_service"] == {"declared_mod": True}


def test_undeclared_module_rejected(client, config_service):
    resp = client.post("/api/webui/single-service",
                       json={"single_service": {"plain_mod": True}})

    body = resp.json()
    assert body["rejected"] == ["plain_mod"]
    assert config_service.get_webui_config()["single_service"] == {}


def test_undeclared_stale_key_is_dropped(client, config_service):
    """历史配置里未声明模块的 true 会被清掉（否则用户以为开了却永不生效）。"""
    resp = client.post("/api/webui/single-service",
                       json={"single_service": {"plain_mod": True, "declared_mod": True}})

    assert resp.json()["rejected"] == ["plain_mod"]
    assert config_service.get_webui_config()["single_service"] == {"declared_mod": True}


def test_virtual_agent_always_accepted(client, config_service):
    resp = client.post("/api/webui/single-service",
                       json={"single_service": {"agent": True}})

    assert resp.json()["rejected"] == []
    assert config_service.get_webui_config()["single_service"] == {"agent": True}


def test_unloaded_module_is_preserved(client, config_service):
    """查不到的模块名原样保留：模块暂时未加载不该导致配置被清空。"""
    resp = client.post("/api/webui/single-service",
                       json={"single_service": {"not_loaded_yet": True}})

    assert resp.json()["rejected"] == []
    assert config_service.get_webui_config()["single_service"] == {"not_loaded_yet": True}


def test_off_switch_also_filtered(client, config_service):
    """关掉也一样要过声明检查：未声明模块的键不该被写进配置（关 = 清空策略/标记）。"""
    resp = client.post("/api/webui/single-service",
                       json={"single_service": {"plain_mod": False, "declared_mod": False}})

    assert resp.json()["rejected"] == ["plain_mod"]
    assert config_service.get_webui_config()["single_service"] == {}
    assert config_service.list_channel_policies("declared_mod") == []
