"""登录限流取址：X-Forwarded-For 只在可信反代后采信，避免换头绕过限流。

历史问题（`app/webui/app.py::_client_address`）：只要请求带 XFF 就取首段，
公网直连客户端每次换个头就是"新来源"→ 10 次/5 分钟限流形同虚设。

修法：仅当 TCP 对端是回环/私网地址（本机或内网反代）时才采信 XFF。
"""

from __future__ import annotations

from types import SimpleNamespace

from starlette.testclient import TestClient

from app.bootstrap import build_container
from app.core.settings import Settings
from app.webui.app import _client_address, _is_trusted_proxy, create_app

TOKEN = "login-addr-test-token-0123456789"


def _req(host: str | None, xff: str | None = None):
    headers = {} if xff is None else {"x-forwarded-for": xff}
    client = SimpleNamespace(host=host) if host is not None else None
    return SimpleNamespace(headers=headers, client=client)


def test_trusted_proxy_detection():
    assert _is_trusted_proxy("127.0.0.1")
    assert _is_trusted_proxy("::1")
    assert _is_trusted_proxy("10.0.0.5")
    assert _is_trusted_proxy("192.168.1.20")
    assert not _is_trusted_proxy("203.0.113.9")
    assert not _is_trusted_proxy("testclient")
    assert not _is_trusted_proxy("")


def test_trusts_xff_from_loopback_proxy():
    assert _client_address(_req("127.0.0.1", "1.2.3.4")) == "1.2.3.4"
    assert _client_address(_req("10.0.0.5", "1.2.3.4, 5.6.7.8")) == "1.2.3.4"


def test_ignores_spoofed_xff_from_public_client():
    """公网直连：换 XFF 不能换限流桶。"""
    assert _client_address(_req("203.0.113.9", "1.2.3.4")) == "203.0.113.9"
    assert _client_address(_req("203.0.113.9", "9.9.9.9, 1.1.1.1")) == "203.0.113.9"


def test_no_client_falls_back_to_unknown():
    assert _client_address(_req(None, "1.2.3.4")) == "unknown"


def test_testclient_host_is_not_treated_as_proxy():
    """starlette TestClient 的 client.host 是 "testclient"（非 IP）→ 不采信 XFF。"""
    assert _client_address(_req("testclient", "1.2.3.4")) == "testclient"


def test_xff_header_cannot_bypass_login_rate_limit(tmp_path):
    """端到端：连续换 XFF 仍会在达到阈值后被 429。"""
    from app.webui.auth import login_rate_limiter

    settings = Settings(
        db_path=str(tmp_path / "t.db"),
        module_dir=str(tmp_path / "data" / "module"),
        log_dir=str(tmp_path / "logs"),
        webui_port=19999,
        webui_token=TOKEN,
    )
    client = TestClient(create_app(build_container(settings)), follow_redirects=False)

    login_rate_limiter.clear()
    try:
        for index in range(login_rate_limiter.max_failures):
            resp = client.post(
                "/login",
                data={"token": "wrong", "next": "/"},
                headers={"x-forwarded-for": f"1.2.3.{index}"},
            )
            assert resp.status_code == 401
        resp = client.post(
            "/login",
            data={"token": "wrong", "next": "/"},
            headers={"x-forwarded-for": "9.9.9.9"},
        )
        assert resp.status_code == 429, "换 XFF 头不应绕过限流"
    finally:
        login_rate_limiter.clear()
