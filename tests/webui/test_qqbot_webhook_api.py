"""官方 Webhook 回调 API 契约（公开路径 + 验签 + op12/op13）。"""

from __future__ import annotations

import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

from app.infrastructure.gateway_hub import GatewayHub
from app.infrastructure.qqbot.webhook import _seed
from app.webui.app import create_app

SECRET = "naOC0ocQE3shWLAfffVLB1rhYPG7"
APP_ID = "123456"


def _signed(secret: str, body: dict) -> tuple[bytes, dict]:
    raw = json.dumps(body, ensure_ascii=False).encode()
    timestamp = "1725442341"
    private = Ed25519PrivateKey.from_private_bytes(_seed(secret))
    signature = private.sign(timestamp.encode() + raw).hex()
    return raw, {"X-Signature-Ed25519": signature, "X-Signature-Timestamp": timestamp}


async def test_webhook_validation_and_event_ack(container, config_service, monkeypatch):
    await config_service.save_bots([{
        "id": "official",
        "name": "官方号",
        "protocol": "qqbot",
        "transport": "webhook",
        "app_id": APP_ID,
        "app_secret": SECRET,
    }])
    client = TestClient(create_app(container))

    # 1. 回调地址验证（op=13）：返回签名
    resp = client.post("/qqbot/webhook/official", json={
        "op": 13, "d": {"plain_token": "TOKEN", "event_ts": "1725442341"},
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plain_token"] == "TOKEN"
    public = Ed25519PrivateKey.from_private_bytes(_seed(SECRET)).public_key()
    public.verify(bytes.fromhex(body["signature"]), b"1725442341TOKEN")

    # 2. 验签失败：401
    bad = client.post("/qqbot/webhook/official", json={"op": 0, "t": "X", "d": {}})
    assert bad.status_code == 401

    # 3. 验签通过：先 ACK op=12，再交给网关
    received: list[dict] = []

    async def fake_handle(account_id: str, payload: dict) -> bool:
        received.append({"account_id": account_id, "payload": payload})
        return True

    gateway = container.get(GatewayHub)
    monkeypatch.setattr(gateway, "handle_webhook", fake_handle)

    raw, headers = _signed(SECRET, {"op": 0, "t": "GROUP_AT_MESSAGE_CREATE", "d": {"id": "m1"}})
    resp = client.post("/qqbot/webhook/official", content=raw, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"op": 12}
    assert received and received[0]["account_id"] == "official"


async def test_webhook_unknown_account_404(container):
    client = TestClient(create_app(container))
    resp = client.post("/qqbot/webhook/nobody", json={"op": 13, "d": {}})
    assert resp.status_code == 404
