"""非 ASCII WEBUI_TOKEN 不得让全站 500（回归测试）。

旧实现 ``tokens_match`` 直接 ``hmac.compare_digest(str, str)``：该函数对 ``str``
只接受 ASCII，非 ASCII 令牌会抛 ``TypeError`` → 鉴权层异常 → 所有 ``/api``、
页面、WS 握手全部 500，用户「改了个中文令牌后整个后台打不开」。
修复：统一 ``encode("utf-8")`` 后比较（对齐 ``onebot/reverse.py::_token_matches``），
并在首次使用时明确告警。
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.bootstrap import build_container
from app.core.settings import Settings
from app.webui.app import create_app
from app.webui.auth import (
    _NON_ASCII_WARNED,
    SESSION_COOKIE,
    is_authenticated,
    issue_session,
    tokens_match,
)

TOKEN = "中文口令-测试-abc"


def test_tokens_match_non_ascii_does_not_raise():
    """回归：非 ASCII 令牌比较不得抛 TypeError（旧实现必炸）。"""
    assert tokens_match(TOKEN, TOKEN) is True
    assert tokens_match("错误口令", TOKEN) is False
    assert tokens_match(TOKEN, "ascii-token") is False
    assert TOKEN in _NON_ASCII_WARNED, "首次使用非 ASCII 令牌应留下明确告警"


def test_is_authenticated_with_non_ascii_token():
    """会话 cookie / query 两条通道都支持非 ASCII 令牌。"""
    assert is_authenticated(expected_token=TOKEN, query_token=TOKEN) is True
    assert is_authenticated(expected_token=TOKEN, query_token="nope") is False
    assert is_authenticated(expected_token=TOKEN, session_cookie=issue_session(TOKEN)) is True
    assert is_authenticated(expected_token=TOKEN, session_cookie=issue_session("其它令牌")) is False


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        db_path=str(tmp_path / "t.db"),
        module_dir=str(tmp_path / "data" / "module"),
        log_dir=str(tmp_path / "logs"),
        webui_token=TOKEN,
    )
    return TestClient(create_app(build_container(settings)))


def test_api_with_non_ascii_token_returns_200(client):
    """鉴权接口在非 ASCII 令牌下必须正常返回 200（旧实现直接 500）。"""
    resp = client.get("/api/auth/session", params={"token": TOKEN})
    assert resp.status_code == 200, resp.text
    assert resp.json()["authenticated"] is True


def test_login_one_click_with_non_ascii_token_returns_redirect(client):
    """登录页一键登录（?accesstoken=）走 tokens_match，非 ASCII 时不得 500。"""
    resp = client.get("/login", params={"accesstoken": TOKEN}, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    assert SESSION_COOKIE in resp.cookies
