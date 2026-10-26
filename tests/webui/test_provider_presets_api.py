"""Provider 预设内置模板 API 冒烟：路由真挂上、导入能落库、重复导入幂等。"""

from __future__ import annotations

import pytest


@pytest.fixture
def client(container):
    from starlette.testclient import TestClient

    from app.webui.app import create_app

    return TestClient(create_app(container))


def test_list_builtin_presets(client):
    resp = client.get("/api/provider-presets/builtin")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    presets = data["presets"]
    assert presets, "内置预设目录不能为空"
    keys = {p["key"] for p in presets}
    assert {"opencode-go", "opencode", "deepseek"} <= keys
    # 未落库时 installed_preset_id 为空
    assert all(p["installed_preset_id"] == "" for p in presets)


def test_import_builtin_preset_is_idempotent(client):
    first = client.post(
        "/api/provider-presets/builtin/opencode-go/import",
        json={"api_key": "oc_sk_test"},
    )
    assert first.status_code == 200
    body = first.json()
    assert body["ok"] is True and body["created"] is True
    preset = body["preset"]
    assert preset["name"] == "OpenCode Go"
    assert preset["config"]["api_base"] == "https://opencode.ai/zen/go/v1"
    assert preset["config"]["api_key"] == "oc_sk_test"

    again = client.post("/api/provider-presets/builtin/opencode-go/import", json={})
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["preset"]["id"] == preset["id"]

    listed = client.get("/api/provider-presets").json()["presets"]
    assert len(listed) == 1

    marked = {
        p["key"]: p["installed_preset_id"]
        for p in client.get("/api/provider-presets/builtin").json()["presets"]
    }
    assert marked["opencode-go"] == preset["id"]


def test_import_unknown_builtin_preset_returns_400(client):
    resp = client.post("/api/provider-presets/builtin/not-a-provider/import", json={})
    assert resp.status_code == 400
    assert resp.json()["status"] == "error"
