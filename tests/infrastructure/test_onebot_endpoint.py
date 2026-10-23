"""OneBot 端点解析与连接模式归一测试。

`endpoint.py` 是「配置校验 + 监听器启动 + 前端提示」三处的共同前置步骤，
因此这里覆盖的是三种调用方都会踩的边界：缺端口、非法 scheme、token 拆分、
以及历史配置里五花八门的模式写法。
"""

from __future__ import annotations

import pytest

from app.infrastructure.onebot.endpoint import (
    FORWARD,
    REVERSE,
    EndpointError,
    is_reverse,
    normalize_mode,
    parse_endpoint,
)


def test_normalize_mode_accepts_aliases():
    for raw in ("forward", "ws", "forward_ws", "client", "FORWARD", " forward "):
        assert normalize_mode(raw) == FORWARD, raw
    for raw in ("reverse", "ws_reverse", "reverse_ws", "server", "REVERSE"):
        assert normalize_mode(raw) == REVERSE, raw


def test_normalize_mode_falls_back_to_forward():
    """存量账号都没有这个字段：任何未知值都必须落到正向，不能静默改行为。"""
    for raw in (None, "", "  ", "garbage", 0, 123, [], {}):
        assert normalize_mode(raw) == FORWARD, raw


def test_is_reverse_matches_normalize_mode():
    assert is_reverse("ws_reverse") is True
    assert is_reverse("forward") is False
    assert is_reverse(None) is False


def test_parse_endpoint_splits_access_token():
    ep = parse_endpoint("ws://127.0.0.1:3001/onebot/v11/ws?access_token=abc&x=1")
    assert (ep.host, ep.port, ep.path) == ("127.0.0.1", 3001, "/onebot/v11/ws")
    assert ep.token == "abc"
    assert ep.query == "x=1"
    assert ep.is_tls is False


def test_parse_endpoint_default_path_and_ports():
    ep = parse_endpoint("ws://127.0.0.1:3001")
    assert ep.path == "/"
    ep = parse_endpoint("wss://example.com/ws")
    assert (ep.port, ep.default_port, ep.is_tls) == (None, 443, True)
    assert ep.bindable() == ("example.com", 443)
    ep = parse_endpoint("ws://example.com/ws")
    assert ep.bindable() == ("example.com", 80)


def test_parse_endpoint_require_port_for_listener():
    """反向监听没有端口 = 用户无从在 OneBot 实现里填目标，必须报错。"""
    with pytest.raises(EndpointError, match="端口"):
        parse_endpoint("ws://127.0.0.1/onebot/v11/ws", require_port=True)
    # 正向不要求端口（沿用 ws 默认端口由对端决定）
    assert parse_endpoint("ws://127.0.0.1/ws").port is None


def test_parse_endpoint_rejects_bad_addresses():
    for bad in ("", "   ", "127.0.0.1:3001", "ftp://127.0.0.1:1", "ws://"):
        with pytest.raises(EndpointError):
            parse_endpoint(bad)


def test_parse_endpoint_rejects_invalid_port():
    with pytest.raises(EndpointError, match="端口"):
        parse_endpoint("ws://127.0.0.1:99999/ws")


def test_parse_endpoint_ipv6_literal():
    ep = parse_endpoint("ws://[::1]:3001/onebot/v11/ws")
    assert ep.host == "::1"
    assert ep.port == 3001
