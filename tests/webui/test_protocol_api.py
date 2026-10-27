"""协议端 API（P5-2/P5-3）：列表 / 安装 / 启停 / 日志 / 扫码登录。

真实下载、真实进程、真实协议端都由注入替换；这里锁定"面板要回答的问题"和错误码。
"""

from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from pathlib import Path

from starlette.testclient import TestClient

from app.application.protocol.installer import ProtocolInstaller
from app.application.protocol.login import ProtocolLoginService
from app.application.protocol.supervisor import ProtocolSupervisor
from app.webui.app import create_app


class FakeProc:
    def __init__(self):
        self.pid = 1234
        self._code = None

    def poll(self):
        return self._code

    def terminate(self):
        self._code = 0

    def kill(self):
        self._code = -9

    def wait(self, timeout=None):
        return self._code


def _kit() -> bytes:
    buffer = __import__("io").BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("napcat.exe", "bin")
        archive.writestr("config.json", "{}")
    return buffer.getvalue()


def _data_root(container) -> Path:
    module_dir = container.get(__import__("app.core.settings", fromlist=["Settings"]).Settings).module_dir
    path = Path(module_dir).resolve().parent
    return path if Path(module_dir).name == "module" else Path(module_dir)


def _prepare(container, protocol="napcat"):
    """写入自定义清单，并给安装器/托管器/登录器注入假实现。"""
    payload = _kit()
    root = _data_root(container) / "protocol"
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps({
        "schema": "qqbotnext.protocol/1",
        "protocols": {
            protocol: {
                "display_name": "NapCat",
                "versions": [{
                    "version": "1.2.0",
                    "url": "https://example.com/napcat-1.2.0.zip",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                    "note": "推荐",
                }],
            }
        },
    }), encoding="utf-8")

    installer = container.get(ProtocolInstaller)
    installer._fetcher = lambda url: payload
    supervisor = container.get(ProtocolSupervisor)
    supervisor._spawn = lambda *args, **kwargs: FakeProc()
    login = container.get(ProtocolLoginService)
    login._get = lambda url: (
        {"data": {"qrcode": base64.b64encode(b"png").decode()}}
        if "Qrcode" in url
        else {"data": {"is_online": True, "uin": 10001, "nick": "小号"}}
    )
    return payload


async def test_protocol_list_shows_versions_and_state(container):
    _prepare(container)
    client = TestClient(create_app(container))

    resp = client.get("/api/protocol")

    assert resp.status_code == 200, resp.text
    napcat = next(item for item in resp.json()["protocols"] if item["protocol"] == "napcat")
    assert napcat["display_name"] == "NapCat"
    assert napcat["versions"] == ["1.2.0"]
    assert napcat["installed"] is False and napcat["running"] is False


async def test_install_start_stop_flow(container):
    _prepare(container)
    client = TestClient(create_app(container))

    installed = client.post("/api/protocol/napcat/install", json={"version": "1.2.0"})
    assert installed.status_code == 200, installed.text
    assert installed.json()["version"] == "1.2.0"

    started = client.post("/api/protocol/napcat/start", json={})
    assert started.status_code == 200, started.text
    assert started.json()["running"] is True
    assert started.json()["pid"] == 1234

    status = client.get("/api/protocol/napcat/status").json()
    assert status["running"] is True

    stopped = client.post("/api/protocol/napcat/stop", json={})
    assert stopped.status_code == 200
    assert stopped.json()["running"] is False


async def test_install_rejects_bad_checksum(container, monkeypatch):
    _prepare(container)
    installer = container.get(ProtocolInstaller)
    installer._fetcher = lambda url: b"tampered"
    client = TestClient(create_app(container))

    resp = client.post("/api/protocol/napcat/install", json={"version": "1.2.0"})

    assert resp.status_code == 400
    assert resp.json()["code"] == "PROTOCOL_CHECKSUM_MISMATCH"


async def test_start_without_install_returns_actionable_error(container):
    _prepare(container)
    client = TestClient(create_app(container))
    resp = client.post("/api/protocol/napcat/start", json={})
    assert resp.status_code == 400
    assert resp.json()["code"] == "PROTOCOL_NOT_INSTALLED"


async def test_qrcode_and_login_status(container):
    _prepare(container)
    client = TestClient(create_app(container))

    qr = client.get("/api/protocol/napcat/qrcode")
    assert qr.status_code == 200, qr.text
    assert qr.json()["image"].startswith("data:image/png;base64,")

    status = client.get("/api/protocol/napcat/login-status").json()
    assert status["online"] is True and status["nickname"] == "小号"


async def test_login_complete_autofills_account(container, config_service):
    _prepare(container)
    await config_service.save_bots([{"id": "acc1", "name": "主号", "ws_url": "ws://127.0.0.1:9/"}])
    client = TestClient(create_app(container))

    resp = client.post("/api/protocol/napcat/login/complete", json={"account_id": "acc1"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["ws_url"].endswith("/onebot/v11/ws")
    bot = config_service.get_bots()[0]
    assert bot["conn_mode"] == "reverse"
    assert "/onebot/v11/ws" in bot["ws_url"]


async def test_login_complete_requires_account(container):
    _prepare(container)
    client = TestClient(create_app(container))
    resp = client.post("/api/protocol/napcat/login/complete", json={})
    assert resp.status_code == 400
    assert resp.json()["code"] == "PROTOCOL_ACCOUNT_REQUIRED"


async def test_log_tail_endpoint(container):
    _prepare(container)
    client = TestClient(create_app(container))
    log_dir = _data_root(container) / "protocol" / "napcat"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "protocol.log").write_text("a\nb\nc", encoding="utf-8")

    resp = client.get("/api/protocol/napcat/log", params={"lines": 2})

    assert resp.status_code == 200
    assert resp.json()["log"].splitlines() == ["b", "c"]
