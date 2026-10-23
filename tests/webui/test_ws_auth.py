"""WebSocket 鉴权：会话 cookie 与旧通道并存。

HTTP 中间件对 websocket scope 不生效，所以 `/ws/logs` 在端点内单独校验。
这里验证三件事：cookie 能过、无凭据被拒（4401）、Bearer/query 仍能过。
"""

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.bootstrap import build_container
from app.core.settings import Settings
from app.webui.app import create_app
from app.webui.auth import SESSION_COOKIE, issue_session

TOKEN = "ws-test-token-abcdef"


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        debug=True,
        db_path=str(tmp_path / "t.db"),
        module_dir=str(tmp_path / "module"),
        log_dir=str(tmp_path / "logs"),
        webui_token=TOKEN,
    )
    return TestClient(create_app(build_container(settings)))


def test_ws_rejects_without_credentials(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/logs") as ws:
            ws.receive_text()
    assert exc.value.code == 4401


def test_ws_accepts_session_cookie(client):
    """浏览器登录后靠 cookie 连 WS——这是独立登录页能正常工作的关键。"""
    client.cookies.set(SESSION_COOKIE, issue_session(TOKEN))
    with client.websocket_connect("/ws/logs") as ws:
        # 端点每秒推一次日志快照；能收到（不抛 4401）即说明鉴权通过
        assert ws.receive_text() is not None


def test_ws_accepts_bearer_token(client):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with client.websocket_connect("/ws/logs", headers=headers) as ws:
        assert ws.receive_text() is not None


def test_ws_accepts_query_token(client):
    with client.websocket_connect(f"/ws/logs?token={TOKEN}") as ws:
        assert ws.receive_text() is not None


def test_ws_rejects_wrong_cookie(client):
    client.cookies.set(SESSION_COOKIE, issue_session("other-token"))
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/logs") as ws:
            ws.receive_text()
    assert exc.value.code == 4401


def test_ws_open_when_auth_disabled(tmp_path):
    settings = Settings(
        debug=True,
        db_path=str(tmp_path / "t2.db"),
        module_dir=str(tmp_path / "module2"),
        log_dir=str(tmp_path / "logs2"),
        webui_token="",
    )
    client = TestClient(create_app(build_container(settings)))
    with client.websocket_connect("/ws/logs") as ws:
        assert ws.receive_text() is not None