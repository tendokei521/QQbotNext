"""ws_url 拆合时的 token URL 编解码回归测试。

真实缺陷：``join_ws_url`` 把 access_token 原样拼进 URL，``split_ws_url`` 原样拆出。
于是非 ASCII token（中文）在 HTTP 层被二次编码/解码后永远对不上；含 ``&`` 的 token
会被当成两个 query 参数截断。修复：join 用 ``quote(token, safe="")``，split 用 unquote。
"""

from __future__ import annotations

from app.infrastructure.config.ws_url import (
    ACCESS_TOKEN_MASK,
    join_ws_url,
    mask_ws_url,
    restore_ws_url,
    split_ws_url,
)


def test_join_ws_url_encodes_non_ascii_token():
    joined = join_ws_url("ws://h:3000/ws", "中文令牌")
    assert "中文" not in joined
    assert joined == "ws://h:3000/ws?access_token=%E4%B8%AD%E6%96%87%E4%BB%A4%E7%89%8C"
    assert split_ws_url(joined) == ("ws://h:3000/ws", "中文令牌")


def test_token_with_ampersand_round_trips():
    """含 & 的 token 必须编码成 %26，否则 URL 里会被拆成两个 query 参数。"""
    token = "abc&def"
    joined = join_ws_url("ws://h:3000/ws", token)
    assert "access_token=abc%26def" in joined
    base, decoded = split_ws_url(joined)
    assert (base, decoded) == ("ws://h:3000/ws", token)
    assert split_ws_url(joined + "&x=1")[1] == token, "编码后的 token 不能被后续参数拆坏"


def test_ascii_token_unchanged_for_backward_compatibility():
    """存量 ASCII token 的 URL 形态不变（避免升级后配置全员重写）。"""
    assert join_ws_url("ws://h/", "abc123") == "ws://h/?access_token=abc123"
    assert split_ws_url("ws://h/?access_token=abc123&x=1") == ("ws://h/?x=1", "abc123")


def test_mask_and_restore_round_trip_with_encoded_token():
    token = "中文&令牌"
    original = join_ws_url("ws://h:3000/ws", token)

    masked = mask_ws_url(original)
    assert token not in masked
    assert f"access_token={ACCESS_TOKEN_MASK}" in masked

    assert restore_ws_url(masked, original) == original
