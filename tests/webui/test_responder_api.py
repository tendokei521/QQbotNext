"""响应权策略 API：声明校验 + 策略持久化 + 兼容语义。"""

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


def test_undeclared_module_reports_not_declared(client):
    resp = client.get("/api/modules/plain_mod/responder")

    assert resp.status_code == 200
    assert resp.json() == {"module": "plain_mod", "declared": False, "groups": []}


def test_declared_module_lists_groups(client):
    resp = client.get("/api/modules/declared_mod/responder")

    assert resp.status_code == 200
    body = resp.json()
    assert body["declared"] is True
    assert body["groups"] == []


def test_missing_module_returns_404(client):
    resp = client.get("/api/modules/ghost/responder")

    assert resp.status_code == 404


def test_post_rejects_undeclared_module(client):
    resp = client.post("/api/modules/plain_mod/responder", json={"policies": []})

    assert resp.status_code == 403


def test_post_persists_channel_policies(client, config_service):
    resp = client.post("/api/modules/declared_mod/responder", json={
        "policies": [
            {"group_id": "10", "policy": "dedupe", "fallback": "closed"},
            {"group_id": "11", "policy": "leader", "primary_account_id": "acc1", "fallback": "open"},
        ],
    })

    assert resp.status_code == 200
    body = resp.json()
    # 容器里没有配置账号 → 指定账号会被视为未知（安全降级为 fallback）
    assert body["warnings"]
    assert [row["channel_id"] for row in body["policies"]] == ["10", "11"]
    saved = {row["channel_id"]: row for row in config_service.list_channel_policies("declared_mod")}
    assert saved["10"]["policy"] == "dedupe"
    assert saved["10"]["fallback"] == "closed"
    assert saved["11"]["policy"] == "leader"
    assert saved["11"]["primary_account_id"] is None


def test_post_clears_policies_with_empty_list(client, config_service):
    client.post("/api/modules/declared_mod/responder", json={
        "policies": [{"group_id": "10", "policy": "all"}],
    })

    client.post("/api/modules/declared_mod/responder", json={"policies": []})

    assert config_service.list_channel_policies("declared_mod") == []


def test_post_warns_on_unknown_primary(client, config_service):
    resp = client.post("/api/modules/declared_mod/responder", json={
        "policies": [
            {"group_id": "10", "policy": "delegate", "primary_account_id": "ghost"},
        ],
    })

    assert resp.status_code == 200
    assert resp.json()["warnings"]
    saved = config_service.list_channel_policies("declared_mod")[0]
    assert saved["primary_account_id"] is None


def test_agent_virtual_module_is_always_declared(client):
    resp = client.get("/api/modules/agent/responder")

    assert resp.status_code == 200
    assert resp.json()["declared"] is True
