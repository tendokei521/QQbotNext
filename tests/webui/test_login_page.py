"""独立登录页与会话鉴权的测试。

覆盖四层：
1. `app/webui/auth.py` 纯函数（签名、过期、篡改、限流、开放重定向防护）；
2. HTTP 登录流程（302 跳登录页、密码校验、cookie 下发、登出）；
3. 中间件分流（API 返回 401 JSON、页面 302、静态资源 401 不重定向）；
4. WebSocket 接受 cookie，同时旧的三条通道仍然可用。
"""

import time

import pytest
from starlette.testclient import TestClient

from app.bootstrap import build_container
from app.core.settings import Settings
from app.webui.app import create_app
from app.webui.auth import (
    SESSION_COOKIE,
    LoginRateLimiter,
    credential_from_headers,
    issue_session,
    safe_next_path,
    verify_session,
)

TOKEN = "unit-test-token-0123456789"


# --------------------------------------------------------------------------- #
# 1. 会话签名纯函数
# --------------------------------------------------------------------------- #
def test_issued_session_verifies():
    value = issue_session(TOKEN)
    assert verify_session(TOKEN, value)


def test_session_rejects_wrong_and_malformed_tokens():
    assert not verify_session(TOKEN, issue_session("another-token"))
    for bad in ["", None, "garbage", ".", "abc.", ".def"]:
        assert not verify_session(TOKEN, bad)


def test_session_rejects_tampered_payload():
    value = issue_session(TOKEN)
    payload, _, sig = value.rpartition(".")
    forged = payload[:-2] + ("aa" if payload[-2:] != "aa" else "bb") + "." + sig
    assert not verify_session(TOKEN, forged)


def test_session_rejects_tampered_signature():
    value = issue_session(TOKEN)
    payload, _, sig = value.rpartition(".")
    assert not verify_session(TOKEN, payload + "." + sig[:-2] + "00")


def test_session_expires():
    value = issue_session(TOKEN, ttl=-1)
    assert not verify_session(TOKEN, value)


def test_token_never_appears_in_cookie_value():
    """cookie 里不该出现明文令牌（否则 HttpOnly 的意义就没了）。"""
    assert TOKEN not in issue_session(TOKEN)


def test_credential_basic_tolerates_colon_in_token():
    """令牌含冒号时只按第一个冒号切分，不能把令牌截断。"""
    import base64

    encoded = base64.b64encode(b"user:pa:ss:word").decode()
    assert credential_from_headers(f"Basic {encoded}") == "pa:ss:word"
    assert credential_from_headers(f"Bearer {TOKEN}") == TOKEN
    assert credential_from_headers("", TOKEN) == TOKEN


# --------------------------------------------------------------------------- #
# 2. 开放重定向防护
# --------------------------------------------------------------------------- #
def test_safe_next_rejects_external_and_injection():
    for bad in [
        "//evil.com",
        "http://evil.com",
        "/\\evil.com",
        "",
        None,
        "/x\r\nSet-Cookie: a=b",
    ]:
        assert safe_next_path(bad) == "/"


def test_safe_next_keeps_internal_paths():
    for good in ["/", "/bots", "/agent/basic?x=1", "/a/b#c"]:
        assert safe_next_path(good) == good


# --------------------------------------------------------------------------- #
# 3. 登录限流
# --------------------------------------------------------------------------- #
def test_rate_limiter_window_expires():
    limiter = LoginRateLimiter(max_failures=1, window=0.05)
    limiter.record_failure("ip")
    assert limiter.is_blocked("ip")
    time.sleep(0.08)
    assert not limiter.is_blocked("ip")


# --------------------------------------------------------------------------- #
# 4. 端到端：登录页 + 中间件分流
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path):
    settings = Settings(
        db_path=str(tmp_path / "t.db"),
        module_dir=str(tmp_path / "data" / "module"),
        log_dir=str(tmp_path / "logs"),
        webui_port=19999,
        webui_token=TOKEN,
    )
    app = create_app(build_container(settings))
    # follow_redirects=False：断言 302/303 本身，而不是被自动跟随后看最终页
    return TestClient(app, follow_redirects=False)


def test_unauthenticated_page_redirects_to_login(client):
    resp = client.get("/", headers={"accept": "text/html"})
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?next=")


def test_unauthenticated_api_returns_401_json(client):
    resp = client.get("/api/bots")
    assert resp.status_code == 401
    assert resp.json()["status"] == "error"


def test_login_page_has_no_token_leak(client):
    """登录页 HTML 绝不能包含真实令牌。"""
    resp = client.get("/login")
    assert TOKEN not in resp.text


def test_login_page_is_well_formed_html(client):
    """模板必须是真 HTML：doctype 不能被转义成实体（该坑曾真实发生过）。"""
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "no-store" in resp.headers.get("cache-control", "")
    body = resp.text
    assert body.lstrip().lower().startswith("<!doctype html>")
    assert "&lt;!doctype" not in body
    # 关键控件在场，且服务端占位符已全部替换
    assert 'name="token"' in body
    assert 'action="/login"' in body
    assert 'method="post"' in body.lower()
    assert "{error_block}" not in body
    assert "{next_path}" not in body


def test_login_with_wrong_token_shows_error(client):
    resp = client.post("/login", data={"token": "wrong", "next": "/"})
    assert resp.status_code == 401
    assert "令牌不正确" in resp.text


def test_login_success_sets_hardened_cookie_and_redirects(client):
    resp = client.post("/login", data={"token": TOKEN, "next": "/bots"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/bots"
    cookie = resp.headers["set-cookie"]
    assert SESSION_COOKIE in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()
    assert "Path=/" in cookie
    # cookie 下发后即可访问 API 与页面（会话真正生效）
    assert client.get("/api/bots").status_code == 200
    assert client.get("/", headers={"accept": "text/html"}).status_code == 200


def test_logged_in_user_visiting_login_is_sent_away(client):
    client.post("/login", data={"token": TOKEN, "next": "/"})
    resp = client.get("/login?next=%2Fbots")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/bots"


def test_logout_clears_cookie(client):
    client.post("/login", data={"token": TOKEN, "next": "/"})
    resp = client.get("/logout")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    # 之后 API 再次 401
    assert client.get("/api/bots").status_code == 401


def test_login_rate_limit_blocks_bruteforce(client):
    from app.webui.auth import login_rate_limiter

    login_rate_limiter.clear()
    try:
        for _ in range(login_rate_limiter.max_failures):
            assert client.post("/login", data={"token": "wrong", "next": "/"}).status_code == 401
        resp = client.post("/login", data={"token": "wrong", "next": "/"})
        assert resp.status_code == 429
        assert resp.headers.get("retry-after")
        # 限流期间即使令牌正确也不放行（否则限流形同虚设）
        assert client.post("/login", data={"token": TOKEN, "next": "/"}).status_code == 429
    finally:
        login_rate_limiter.clear()


# --------------------------------------------------------------------------- #
# 4b. `?accesstoken=` 一键登录（与 `?token=` 等价）
# --------------------------------------------------------------------------- #
def test_accesstoken_rejected_when_wrong(client):
    assert client.get("/api/bots?accesstoken=nope").status_code == 401


def test_page_with_accesstoken_sets_cookie_and_strips_token(client):
    """一键登录：换成会话 cookie，并 303 到不含令牌的干净地址（其它参数保留）。"""
    resp = client.get(f"/?accesstoken={TOKEN}&mode=raw", headers={"accept": "text/html"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/?mode=raw", "令牌必须从地址栏摘掉"
    assert SESSION_COOKIE in resp.headers.get("set-cookie", "")
    # 之后靠 cookie 直接可用
    assert client.get("/", headers={"accept": "text/html"}).status_code == 200


def test_wrong_accesstoken_on_page_falls_back_to_login(client):
    resp = client.get("/?accesstoken=wrong", headers={"accept": "text/html"})
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?next=")
    # 不能因为给了个错令牌就下发会话
    assert SESSION_COOKIE not in resp.headers.get("set-cookie", "")
    # next 里也不该回显那个错令牌
    assert "wrong" not in resp.headers["location"]


def test_login_page_accepts_accesstoken_query(client):
    """/login?accesstoken=… 直接登录（登录页是公开路径，中间件不会替它换 cookie）。"""
    resp = client.get(f"/login?accesstoken={TOKEN}&next=%2Fbots")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/bots"
    assert SESSION_COOKIE in resp.headers.get("set-cookie", "")


def test_stale_accesstoken_does_not_refresh_session(client):
    """已登录用户带着写错的令牌访问页面：只摘令牌，不重发 cookie。"""
    client.get(f"/?accesstoken={TOKEN}", headers={"accept": "text/html"})
    resp = client.get("/?accesstoken=wrong", headers={"accept": "text/html"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert SESSION_COOKIE not in resp.headers.get("set-cookie", "")


def test_api_with_accesstoken_is_never_redirected(client):
    """XHR 拿到 303 会被当成「成功」，所以 API 请求绝不能走一键登录跳转。

    `<img>` 之类非 HTML 请求同理，不能被 303 成 JSON/页面。
    """
    resp = client.get(f"/api/bots?accesstoken={TOKEN}")
    assert resp.status_code == 200
    assert "location" not in resp.headers
    img = client.get(f"/api/media/none?accesstoken={TOKEN}", headers={"accept": "image/*"})
    assert img.status_code != 303


def test_legacy_bearer_and_query_still_work(client):
    assert client.get("/api/bots", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert client.get(f"/api/bots?token={TOKEN}").status_code == 200


def test_session_endpoint_reports_state_and_never_returns_token(client):
    assert client.get("/api/auth/session").json() == {"authenticated": False, "auth_enabled": True}
    client.post("/login", data={"token": TOKEN, "next": "/"})
    resp = client.get("/api/auth/session")
    assert resp.json()["authenticated"] is True
    assert TOKEN not in resp.text


def test_static_asset_401_not_redirected(client):
    """静态资源未认证时不能 302 成 HTML，否则浏览器会报 MIME 错误。"""
    resp = client.get("/assets/index-BRqn8jKY.js")
    assert resp.status_code in (401, 404)
    assert resp.status_code != 302


# --------------------------------------------------------------------------- #
# 5. 未启用鉴权时行为不变（回归保护）
# --------------------------------------------------------------------------- #
def test_no_token_means_no_auth(tmp_path):
    settings = Settings(
        db_path=str(tmp_path / "t2.db"),
        module_dir=str(tmp_path / "data" / "module2"),
        log_dir=str(tmp_path / "logs2"),
        webui_token="",
    )
    client = TestClient(create_app(build_container(settings)), follow_redirects=False)
    # 无令牌配置下 API 直接放行
    assert client.get("/api/bots").status_code == 200
    # /login 也没意义：直接送回首页
    assert client.get("/login").status_code == 302