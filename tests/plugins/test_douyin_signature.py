"""douyin_parser 的签名单测（全离线，不联网）。

覆盖：SM3 标准向量、``a_bogus`` / ``X-Bogus`` 固定向量与编码契约。

固定向量的来源：本插件的签名实现已与上游 ``DLWangSan/douyin_parse`` 的
``abogus.py`` / ``xbogus.py`` 做过**逐字节等价对照**（固定随机数 + 固定时间戳，
多组参数），因此这里的向量等价于上游输出；它用来防止算法被无意改动。
"""

import hashlib

import pytest

from app.plugins.douyin_parser import abogus, sm3
from app.plugins.douyin_parser.abogus import ABogus
from app.plugins.douyin_parser.xbogus import XBogus

# 与 douyin_api.BASE_PARAMS 同形的真实参数集（含 aweme_id）
PARAMS = {
    "device_platform": "webapp",
    "aid": "6383",
    "channel": "channel_pc_web",
    "pc_client_type": "1",
    "version_code": "190500",
    "version_name": "19.5.0",
    "cookie_enabled": "true",
    "browser_language": "zh-CN",
    "browser_platform": "Win32",
    "browser_name": "Edge",
    "browser_online": "true",
    "engine_name": "Blink",
    "os_name": "Windows",
    "os_version": "10",
    "platform": "PC",
    "screen_width": "1920",
    "screen_height": "1080",
    "aweme_id": "7534291258635816251",
}

# 固定 (start_time, end_time, random_1, random_2, random_3) → 固定签名
ABOGUS_VECTOR = (
    "dfRhQd8DDDDTgD6D56KLfY3q61YVYmQI0SVkMD2fHBDOqL39HMYZ9exoIBGvXY8jwG/"
    "-IeEjy4hbT3ohrQ2y0Hwf9W0L/25ksDSkKl5Q5xSSs1X9eghgJ04qmkt5SMx2RvB-rOXmqhZHKRbp09oHmhK4b1dzFgf3qJLzOf=="
)

XBOGUS_VECTOR = "DFSzswVYzsxANapNtmWx-e9WX7jb"
XBOGUS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
XBOGUS_PATH = (
    "https://www.douyin.com/aweme/v1/web/aweme/detail/"
    "?device_platform=webapp&aid=6383&aweme_id=7534291258635816251"
)


def _abogus(**overrides) -> str:
    kwargs = {"start_time": 1700000000000, "end_time": 1700000000007}
    kwargs.update(overrides)
    return ABogus().get_value(PARAMS, "GET", random_num_1=12.5, random_num_2=34.25,
                              random_num_3=56.125, **kwargs)


# ==================== SM3 ====================


def test_sm3_standard_vectors():
    """GB/T 32905-2016 附录 A 标准向量。"""
    assert sm3.sm3_hex(b"abc") == (
        "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"
    )
    assert sm3.sm3_hex(b"abcd" * 16) == (
        "debe9ff92275b8a138604889c18e5a4d6fdb70e5387e5765293dcba39c0c5732"
    )
    assert sm3.sm3_hex(b"") == (
        "1ab21d8355cfa17f8e61194831e81a8f22bec8c728fefb747ed035eb5082aa2b"
    )


def test_sm3_digest_is_32_bytes():
    assert len(sm3.sm3_digest(b"hello")) == 32
    assert sm3.sm3_digest(b"hello").hex() == sm3.sm3_hex(b"hello")


def test_sm3_padding_boundaries():
    """分组边界（55/56/63/64/65 字节）必须各自正确，避免填充长度算错。"""
    if "sm3" not in hashlib.algorithms_available:
        pytest.skip("本机 OpenSSL 不提供 SM3，跳过交叉核对")
    for size in (0, 1, 55, 56, 63, 64, 65, 119, 120, 127, 128, 1000):
        data = bytes(range(256))[:size] if size <= 256 else b"x" * size
        assert sm3.sm3_hex(data) == hashlib.new("sm3", data).hexdigest()


# ==================== a_bogus ====================


def test_abogus_fixed_vector():
    assert _abogus() == ABOGUS_VECTOR


def test_abogus_is_deterministic_and_param_sensitive():
    assert _abogus() == _abogus()
    assert _abogus() != _abogus(start_time=1700000000001)
    other = dict(PARAMS, aweme_id="7345492945006595379")
    assert ABogus().get_value(other, "GET", 1700000000000, 1700000000007,
                              random_num_1=12.5, random_num_2=34.25,
                              random_num_3=56.125) != ABOGUS_VECTOR


def test_abogus_random_path_still_well_formed():
    """不传随机数（生产路径）时：长度稳定、字符集合法、每次不同。"""
    first = ABogus().get_value(PARAMS)
    second = ABogus().get_value(PARAMS)
    assert first != second  # 随机串参与，不应重复
    assert len(first) == len(ABOGUS_VECTOR)
    assert set(first) <= set(abogus._CHARSET_S4)


def test_abogus_accepts_preencoded_params_string():
    """dict 与已编码字符串两条入口必须等价（上游同样接受两者）。"""
    from urllib.parse import urlencode

    from_dict = ABogus().get_value(PARAMS, "GET", 1700000000000, 1700000000007,
                                   random_num_1=1.0, random_num_2=2.0, random_num_3=3.0)
    from_str = ABogus().get_value(urlencode(PARAMS), "GET", 1700000000000, 1700000000007,
                                  random_num_1=1.0, random_num_2=2.0, random_num_3=3.0)
    assert from_dict == from_str


def test_build_signed_url_encodes_signature_once():
    """``a_bogus`` 必须只编码一次：URL 里出现 ``%`` 转义，且没有 ``%25`` 二次编码。"""
    url = ABogus.build_signed_url("https://www.douyin.com/aweme/v1/web/aweme/detail/",
                                  {"aweme_id": "123", "aid": "6383"},
                                  "ab+/cd=ef")
    assert url.startswith("https://www.douyin.com/aweme/v1/web/aweme/detail/?")
    assert url.endswith("&a_bogus=ab%2B%2Fcd%3Def")
    assert "%25" not in url
    assert "aweme_id=123" in url and "aid=6383" in url


# ==================== X-Bogus ====================


def test_xbogus_fixed_vector(monkeypatch):
    """冻结时间后比对固定向量。"""
    from app.plugins.douyin_parser import xbogus as xb

    monkeypatch.setattr(xb.time, "time", lambda: 1700000000)
    result, value, ua = XBogus(XBOGUS_UA).get_xbogus(XBOGUS_PATH)
    assert value == XBOGUS_VECTOR
    assert ua == XBOGUS_UA
    assert result == f"{XBOGUS_PATH}&X-Bogus={value}"


def test_xbogus_default_ua_and_structure(monkeypatch):
    from app.plugins.douyin_parser import xbogus as xb

    monkeypatch.setattr(xb.time, "time", lambda: 1700000000)
    value = XBogus().get_xbogus("https://www.douyin.com/x?a=1")[1]
    assert len(value) == 28  # 21 字节 → 7 组 × 4 字符
    assert set(value) <= set(xb._CHARSET)
