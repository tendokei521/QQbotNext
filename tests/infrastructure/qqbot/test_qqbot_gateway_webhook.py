"""Webhook 传输的网关契约：连接即在线（无长连接）、事件从回调入口入队。"""

from __future__ import annotations

import pytest

from app.infrastructure.qqbot.gateway import QqBotGateway


@pytest.mark.asyncio
async def test_webhook_connect_marks_online_and_calls_login():
    gateway = QqBotGateway()
    logins: list[str] = []

    async def on_login(conn):
        logins.append(conn.account_id)

    gateway.login_handler = on_login
    await gateway.add_bot(
        "", None, False, account_id="official", name="官方号",
        transport="webhook", app_id="app", app_secret="secret",
    )
    assert await gateway.connect_bot("official") is True
    conn = gateway.connections["official"]
    assert conn.status == "connected"
    assert logins == ["official"]
    await gateway.disconnect_bot("official")
    assert conn.status == "disconnected"


@pytest.mark.asyncio
async def test_webhook_payload_is_forwarded_to_same_handler(monkeypatch):
    gateway = QqBotGateway()
    await gateway.add_bot(
        "", None, False, account_id="official", name="官方号",
        transport="webhook", app_id="app", app_secret="secret",
    )
    await gateway.connect_bot("official")

    seen: list[dict] = []

    async def fake_handle(conn, item, queue):
        seen.append(item)

    monkeypatch.setattr(gateway, "_handle_payload", fake_handle)
    ok = await gateway.handle_webhook("official", {
        "op": 0,
        "id": "EV1",
        "t": "GROUP_AT_MESSAGE_CREATE",
        "d": {"id": "M1", "group_openid": "G1"},
    })
    assert ok is True
    assert seen and seen[0]["t"] == "GROUP_AT_MESSAGE_CREATE"
    assert seen[0]["d"]["group_openid"] == "G1"
    await gateway.shutdown()
