"""向外连接（正向）空地址的真实 API 行为：应能保存账号但不发起连接。"""

from __future__ import annotations

from starlette.testclient import TestClient

from app.webui.app import create_app


async def test_forward_without_address_via_api(container, config_service):
    client = TestClient(create_app(container))

    resp = client.post("/api/setup/default-account", json={
        "id": "main", "name": "主号", "conn_mode": "forward", "ws_url": "",
    })

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["conn_mode"] == "forward"
    assert body["ws_url"] == ""
    assert body["connecting"] is False
    bots = config_service.get_bots()
    assert len(bots) == 1 and bots[0]["conn_mode"] == "forward"


async def test_switch_mode_via_api_keeps_single_account(container, config_service):
    client = TestClient(create_app(container))
    client.post("/api/setup/default-account", json={"id": "main", "name": "主号"})

    switched = client.post("/api/setup/default-account", json={
        "id": "main", "name": "主号", "conn_mode": "forward", "ws_url": "",
    })

    assert switched.status_code == 200, switched.text
    assert len(config_service.get_bots()) == 1
    assert config_service.get_bots()[0]["conn_mode"] == "forward"
