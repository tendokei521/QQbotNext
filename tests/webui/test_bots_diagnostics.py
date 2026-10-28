"""诊断台三个 API 的契约测试（前端 EventMonitorDialog.vue 依赖这些字段名）。

契约（不要改字段名）：
- GET  /api/bots/{account_id}/events?limit=50
  → {"events":[{seq,time,event_type,user_id,group_id,summary,raw}],
     "listening":bool, "connected":bool, "account":{"id","name"}}
- POST /api/bots/{account_id}/events/clear → {"status":"success","message":"已清空"}
- POST /api/bots/{account_id}/test-message {"group_id":<int?>,"text":"..."}
  → 成功 {"status":"success","message":"测试消息已发送"}
  → 失败 400/500 {"status":"error","message":"中文原因"}
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.webui.api.bots import router
from app.webui.api.deps import get_bot_service, get_config_service


class _FakeReverseLink:
    def __init__(self) -> None:
        self._listening = False

    def listening(self, account_id: str) -> bool:
        return self._listening


class _FakeConn:
    """最小连接桩：只提供诊断台用到的状态 + send_group_msg。"""

    def __init__(self) -> None:
        self.status = "connected"
        self.sent: list[tuple[int, str]] = []
        self.response: dict | None = {"status": "ok", "retcode": 0, "data": {"message_id": 1}}
        self.raise_exc: Exception | None = None

    async def send_group_msg(self, group_id: int, text: str) -> dict | None:
        if self.raise_exc is not None:
            raise self.raise_exc
        self.sent.append((group_id, text))
        return self.response


class _FakeGateway:
    def __init__(self) -> None:
        self.connections = {"main": _FakeConn()}
        self.reverse_link = _FakeReverseLink()
        self.events: list[dict] = []
        self.latest_group: int | None = None
        self.cleared = False

    def recent_events(self, account_id: str, limit: int = 50) -> list[dict]:
        return self.events[-limit:]

    def clear_events(self, account_id: str) -> None:
        self.cleared = True
        self.events = []

    def latest_group_id(self, account_id: str) -> int | None:
        return self.latest_group


class _FakeBotService:
    def __init__(self, gateway: _FakeGateway) -> None:
        self.gateway = gateway


class _FakeConfigService:
    def get_bot(self, account_id: str) -> dict | None:
        return {"id": account_id, "name": "主号"} if account_id == "main" else None

    def get_account_name(self, account_id: str) -> str:
        return "主号" if account_id == "main" else account_id


@pytest.fixture
def client():
    gateway = _FakeGateway()
    bot_service = _FakeBotService(gateway)
    config_service = _FakeConfigService()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_bot_service] = lambda: bot_service
    app.dependency_overrides[get_config_service] = lambda: config_service
    with TestClient(app) as test_client:
        yield test_client, gateway, gateway.connections["main"]


# ---------- GET events ----------


def test_events_contract_fields(client):
    test_client, gateway, _ = client
    gateway.events = [{
        "seq": 7, "time": 1_700_000_000, "event_type": "message.group",
        "user_id": 100, "group_id": 999, "summary": "你好", "raw": {"post_type": "message"},
    }]

    resp = test_client.get("/api/bots/main/events", params={"limit": 50})

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"events", "listening", "connected", "account"}
    assert body["connected"] is True
    assert body["listening"] is False
    assert body["account"] == {"id": "main", "name": "主号"}
    event = body["events"][0]
    assert set(event) >= {"seq", "time", "event_type", "user_id", "group_id", "summary", "raw"}
    assert event["event_type"] == "message.group"
    assert event["raw"] == {"post_type": "message"}


def test_events_unknown_account_404(client):
    test_client, _, _ = client
    resp = test_client.get("/api/bots/ghost/events")
    assert resp.status_code == 404
    assert resp.json()["status"] == "error"


def test_events_trims_oversized_raw(client):
    test_client, gateway, _ = client
    gateway.events = [{
        "seq": 1, "time": 1, "event_type": "message.group", "user_id": 1, "group_id": 2,
        "summary": "x", "raw": {"message": [{"type": "text", "data": {"text": "x" * 10000}}]},
    }]

    resp = test_client.get("/api/bots/main/events")

    assert resp.status_code == 200
    raw = resp.json()["events"][0]["raw"]
    assert "已截断" in json.dumps(raw, ensure_ascii=False), "超大 raw 必须截断，避免响应体爆掉"


# ---------- POST clear ----------


def test_clear_events_contract(client):
    test_client, gateway, _ = client
    resp = test_client.post("/api/bots/main/events/clear")
    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "message": "已清空"}
    assert gateway.cleared is True


# ---------- POST test-message ----------


def test_test_message_success_with_explicit_group(client):
    test_client, _, conn = client
    resp = test_client.post("/api/bots/main/test-message", json={"group_id": 123456, "text": "你好"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "message": "测试消息已发送"}
    assert conn.sent == [(123456, "你好")]


def test_test_message_uses_latest_group_when_omitted(client):
    test_client, gateway, conn = client
    gateway.latest_group = 777888
    resp = test_client.post("/api/bots/main/test-message", json={"text": "用最近的群"})
    assert resp.status_code == 200
    assert conn.sent == [(777888, "用最近的群")]


def test_test_message_without_group_returns_400(client):
    test_client, _, _ = client
    resp = test_client.post("/api/bots/main/test-message", json={"text": "hi"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert "群号" in body["message"]


def test_test_message_empty_text_400(client):
    test_client, _, _ = client
    resp = test_client.post("/api/bots/main/test-message", json={"group_id": 1, "text": "   "})
    assert resp.status_code == 400
    assert "不能为空" in resp.json()["message"]


def test_test_message_too_long_400(client):
    test_client, _, _ = client
    resp = test_client.post("/api/bots/main/test-message", json={"group_id": 1, "text": "字" * 501})
    assert resp.status_code == 400
    assert "500" in resp.json()["message"]


def test_test_message_invalid_group_400(client):
    test_client, _, _ = client
    resp = test_client.post("/api/bots/main/test-message", json={"group_id": "abc", "text": "hi"})
    assert resp.status_code == 400
    assert "群号必须是正整数" in resp.json()["message"]


def test_test_message_not_connected_400(client):
    test_client, _, conn = client
    conn.status = "error"
    resp = test_client.post("/api/bots/main/test-message", json={"group_id": 1, "text": "hi"})
    assert resp.status_code == 400
    assert "未连接" in resp.json()["message"]


def test_test_message_onebot_failure_passthrough(client):
    """发送失败：OneBot 的 retcode/message 原样回传，方便区分配置/群号问题。"""
    test_client, _, conn = client
    conn.response = {"status": "failed", "retcode": 120, "message": "群不存在"}
    resp = test_client.post("/api/bots/main/test-message", json={"group_id": 1, "text": "hi"})
    assert resp.status_code == 400
    message = resp.json()["message"]
    assert "群不存在" in message and "120" in message


def test_test_message_send_exception_500(client):
    test_client, _, conn = client
    conn.raise_exc = ConnectionError("连接已断开")
    resp = test_client.post("/api/bots/main/test-message", json={"group_id": 1, "text": "hi"})
    assert resp.status_code == 500
    assert resp.json()["status"] == "error"
    assert "发送失败" in resp.json()["message"]


def test_test_message_invalid_json_body_400(client):
    test_client, _, _ = client
    resp = test_client.post(
        "/api/bots/main/test-message",
        content="not-json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["status"] == "error"
