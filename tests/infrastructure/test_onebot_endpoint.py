"""OneBot 端点解析与连接模式归一测试。

`endpoint.py` 是「配置校验 + 监听器启动 + 前端提示」三处的共同前置步骤，
因此这里覆盖的是三种调用方都会踩的边界：缺端口、非法 scheme、token 拆分、
以及历史配置里五花八门的模式写法。
"""

from __future__ import annotations

import pytest

from app.infrastructure.onebot.endpoint import (
    FIRST_PORT,
    FORWARD,
    REVERSE,
    REVERSE_PATH,
    EndpointError,
    is_reverse,
    normalize_mode,
    parse_endpoint,
    suggest_address,
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


def test_parse_endpoint_url_decodes_token_and_path():
    """OneBot 实现按 URL 编码传 token：中文 → %E4%B8%AD...，含 & 的 token → %26。"""
    ep = parse_endpoint("ws://127.0.0.1:3001/onebot/v11/ws?access_token=%E4%B8%AD%E6%96%87")
    assert ep.token == "中文", "不解码会拿 %E4%B8%AD%E6%96%87 当 token，永远校验失败"

    ep = parse_endpoint("ws://127.0.0.1:3001/onebot/v11/ws?access_token=abc%26def")
    assert ep.token == "abc&def", "含 & 的 token 必须以 %26 形式作为一个参数解析"
    assert ep.query == ""

    ep = parse_endpoint("ws://127.0.0.1:3001/onebot/%E4%B8%AD/v11")
    assert ep.path == "/onebot/中/v11"


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


# ==================== 新增账号的默认地址 ====================


def test_suggest_address_forward_shape():
    url = suggest_address(FORWARD)
    assert url == f"ws://127.0.0.1:{FIRST_PORT}/"
    # 建议出来的地址必须能被同一条解析链接受（否则成了"建议一个存不下的地址"）
    assert parse_endpoint(url).port == FIRST_PORT


def test_suggest_address_reverse_shape():
    url = suggest_address("reverse")
    assert url == f"ws://127.0.0.1:{FIRST_PORT}{REVERSE_PATH}"
    # 反向地址必须带端口，否则监听器起不来
    assert parse_endpoint(url, require_port=True).port == FIRST_PORT


def test_suggest_address_skips_ports_already_in_use():
    existing = [
        f"ws://127.0.0.1:{FIRST_PORT}/",
        f"ws://127.0.0.1:{FIRST_PORT + 1}/onebot/v11/ws",
        "ws://127.0.0.1:abc/broken",     # 读不懂的地址直接跳过
        "",
    ]
    assert suggest_address(FORWARD, existing) == f"ws://127.0.0.1:{FIRST_PORT + 2}/"
    assert suggest_address(REVERSE, existing).endswith(f":{FIRST_PORT + 2}{REVERSE_PATH}")


def test_suggest_address_unknown_mode_is_forward():
    assert suggest_address(None) == suggest_address(FORWARD)
    assert suggest_address("garbage") == suggest_address(FORWARD)
